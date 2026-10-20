"""任务执行器：在工作线程里驱动现有引擎，并把进度回报给 GUI。

HTTP 直链：包一层 MultiThreadDownloader，用自实现的探针 + 分块循环复用其
          _chunk_list / 状态持久化逻辑，从而获得逐块进度（原实现只有 tqdm 输出）。
BT：      驱动 Aria2BtDownloader，轮询 aria2 的 tellStatus 获取进度。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from ..downloader import (DEFAULT_UA, STATE_SUFFIX, DownloadError, LinkExpired,
                          MultiThreadDownloader, READ_TIMEOUT, RETRY_TIMES,
                          PROBE_TIMEOUT, _chunk_list)
from ..thunder import decode_thunder
from ..verify import check
from .tasks import SpeedMeter, Task, TaskKind, TaskState


class TaskRunner:
    """驱动单个任务，线程安全地把状态写回 Task，并通过 emit 通知 GUI。"""

    def __init__(self, task: Task, emit):
        self.task = task
        self.emit = emit          # callable(task) → GUI 侧切主线程
        self.meter = SpeedMeter()
        self._last_emit = 0.0

    # ------------------------------------------------------------------ 入口
    def start(self):
        t = self.task
        t.started_at = time.time()
        t.state = TaskState.PROBING
        t.error = ""
        t.verified = None
        t._stop.clear()
        self.meter.reset()
        t._worker = threading.Thread(target=self._run, name=f"task-{t.tid}",
                                     daemon=True)
        t._worker.start()

    def stop(self):
        self.task._stop.set()

    # ------------------------------------------------------------------ 执行
    def _run(self):
        t = self.task
        try:
            src = t.source.strip()
            if src.lower().startswith("thunder://"):
                t.source = decode_thunder(src)
                t.kind = TaskKind.THUNDER
            if t.kind == TaskKind.BT or src.lower().startswith(("magnet:", "http")) and src.lower().endswith(".torrent"):
                self._run_bt()
            else:
                self._run_http()
        except LinkExpired as exc:
            self._fail(f"直链已失效：{exc}")
        except DownloadError as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - 任何异常都要反馈到 UI
            self._fail(f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ HTTP
    def _run_http(self):
        t = self.task
        dl = MultiThreadDownloader(
            t.source,
            headers={"User-Agent": DEFAULT_UA},
            output=t.output or None,
            threads=t.threads,
            chunk_mb=t.chunk_mb,
        )
        # 复用原实现的探测与命名
        session = dl._session()
        final_url, size, ranges_ok, etag, disposition = dl._probe(session)
        out = dl._resolve_output(disposition, final_url)
        t.output = str(out)
        t.total_bytes = size
        t.state = TaskState.DOWNLOADING
        self._emit(force=True)

        if not ranges_ok or not size:
            self._http_single(session, final_url, out)
        else:
            self._http_chunked(dl, out, final_url, size, etag)

        if t._stop.is_set():
            t.state = TaskState.PAUSED
            self._emit(force=True)
            return

        # 校验
        md5 = t.expect_md5.strip()
        sha1 = t.expect_sha1.strip()
        if md5 or sha1:
            t.state = TaskState.VERIFYING
            self._emit(force=True)
            problems = check(out, md5 or None, sha1 or None)
            if problems:
                t.verified = False
                self._fail("；".join(problems))
                return
            t.verified = True

        t.done_bytes = t.total_bytes = out.stat().st_size
        t.state = TaskState.DONE
        t.finished_at = time.time()
        t.speed = 0.0
        self._emit(force=True)

    def _http_chunked(self, dl, out, url, size, etag):
        t = self.task
        state_path = out.with_name(out.name + STATE_SUFFIX)
        chunks = dl._load_state(state_path, url, size, etag)
        if not out.exists() or out.stat().st_size != size:
            chunks = None
        if chunks is None:
            chunks = _chunk_list(size, dl.chunk_size)
            dl._preallocate(out, size)

        t.chunk_total = len(chunks)
        t.chunk_done = sum(1 for c in chunks if c.done)
        t.done_bytes = sum(c.end - c.start + 1 for c in chunks if c.done)
        # _save_state() 依赖这两个实例属性，必须先填好
        dl._chunks = chunks
        dl._meta = {"url": url, "size": size, "etag": etag,
                    "chunk_size": dl.chunk_size}
        self._emit(force=True)

        todo = [c for c in chunks if not c.done]
        if not todo:
            return

        lock = threading.Lock()
        errors: list[Exception] = []

        def work(chunk):
            if t._stop.is_set():
                return
            try:
                self._download_chunk(dl, chunk, out, url, size, lock)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)
                t._stop.set()

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=t.threads) as pool:
            list(pool.map(work, todo))

        state_path.unlink(missing_ok=True)
        if errors:
            raise errors[0]

    def _download_chunk(self, dl, chunk, out, url, size, lock):
        """逐块下载，边写边回报进度。"""
        t = self.task
        session = dl._session()
        headers = {"Range": f"bytes={chunk.start}-{chunk.end}"}
        last_err = None
        for attempt in range(RETRY_TIMES):
            pos = chunk.start
            try:
                if t._stop.is_set():
                    return
                resp = session.get(url, headers=headers, stream=True,
                                   timeout=(PROBE_TIMEOUT, READ_TIMEOUT))
                with resp, open(out, "r+b") as fh:
                    if resp.status_code in (403, 410):
                        raise LinkExpired(f"HTTP {resp.status_code}：直链已失效")
                    if resp.status_code == 200 and not (chunk.start == 0 and chunk.end == size - 1):
                        raise DownloadError("服务器不支持 Range 分块")
                    if resp.status_code not in (200, 206):
                        raise DownloadError(f"HTTP {resp.status_code}")
                    fh.seek(pos)
                    for data in resp.iter_content(256 * 1024):
                        if t._stop.is_set():
                            return
                        fh.write(data)
                        pos += len(data)
                        with lock:
                            t.done_bytes += len(data)
                        self._emit()
                if pos != chunk.end + 1:
                    raise DownloadError(f"分块 {chunk.index} 提前结束")
                chunk.done = True
                with lock:
                    t.chunk_done += 1
                dl._save_state(out)
                self._emit(force=True)
                return
            except LinkExpired:
                raise
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                with lock:
                    t.done_bytes -= (pos - chunk.start)
                if attempt + 1 < RETRY_TIMES:
                    time.sleep(min(2 ** attempt, 8))
        raise DownloadError(f"分块 {chunk.index} 重试失败: {last_err}")

    def _http_single(self, session, url, out):
        t = self.task
        resp = session.get(url, stream=True, timeout=(20, 90))
        with resp, open(out, "wb") as fh:
            if resp.status_code in (403, 410):
                raise LinkExpired(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            if total:
                t.total_bytes = total
            t.chunk_total = 1
            buf = 0
            for data in resp.iter_content(1024 * 1024):
                if t._stop.is_set():
                    return
                fh.write(data)
                t.done_bytes += len(data)
                buf += len(data)
                if buf >= 256 * 1024:
                    buf = 0
                    self._emit()
            t.chunk_done = 1

    # -------------------------------------------------------------------- BT
    def _run_bt(self):
        from ..bt import Aria2BtDownloader, BtError

        t = self.task
        try:
            dl = Aria2BtDownloader(
                t.source,
                output_dir=t.output or ".",
                aria2c_path=None,
            )
        except BtError as exc:
            self._fail(str(exc))
            return

        # aria2 的进度通过其内部 _tell 获取；这里起一个轮询线程
        done_flag = threading.Event()
        t.state = TaskState.DOWNLOADING
        self._emit(force=True)

        def poll():
            while not done_flag.is_set() and not t._stop.is_set():
                try:
                    st = dl._tell(dl.gid) if getattr(dl, "gid", None) else None
                    if st:
                        total = int(st.get("totalLength", "0"))
                        done = int(st.get("completedLength", "0"))
                        speed = int(st.get("downloadSpeed", "0"))
                        if total:
                            t.total_bytes = total
                            t.done_bytes = done
                            t.speed = float(speed)
                            t.eta = (total - done) / speed if speed > 0 else 0.0
                            self._emit()
                except Exception:  # noqa: BLE001 - 轮询失败不应中断任务
                    pass
                time.sleep(0.5)

        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        try:
            files = dl.run()
        finally:
            done_flag.set()

        if files:
            t.total_bytes = t.done_bytes = sum(
                Path(f).stat().st_size for f in files if Path(f).exists())
            t.chunk_total = t.chunk_done = 1
            t.state = TaskState.DONE
            t.finished_at = time.time()
            t.speed = 0.0
            self._emit(force=True)
        elif not t._stop.is_set():
            self._fail("BT 下载未返回文件")

    # ------------------------------------------------------------------ 工具
    def _fail(self, msg: str):
        t = self.task
        if t._stop.is_set() and not msg:
            t.state = TaskState.PAUSED
        else:
            t.state = TaskState.FAILED
            t.error = msg
        t.finished_at = time.time()
        t.speed = 0.0
        self._emit(force=True)

    def _emit(self, force=False):
        """节流后回调 GUI（默认 120ms 一次，避免重绘过频）。"""
        now = time.monotonic()
        if not force and now - self._last_emit < 0.12:
            return
        self._last_emit = now
        t = self.task
        if t.total_bytes:
            t.eta = ((t.total_bytes - t.done_bytes) / t.speed) if t.speed > 0 else 0.0
        t.speed = self.meter.update(t.done_bytes)
        self.emit(t)

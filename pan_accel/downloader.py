"""多线程分块下载核心。

支持 HTTP Range 的直链：并发分块下载 + 断点续传；
不支持 Range 的直链：自动退化为单线程流式下载。
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from tqdm import tqdm

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) pan-accelerator/1.0"
STATE_SUFFIX = ".panacc.state.json"
PROBE_TIMEOUT = 20
READ_TIMEOUT = 90
RETRY_TIMES = 5


class DownloadError(RuntimeError):
    """下载失败。"""


class LinkExpired(DownloadError):
    """直链被服务端拒绝（403/410），通常已过期，需要重新获取。"""


@dataclass
class Chunk:
    index: int
    start: int  # 含
    end: int    # 含
    done: bool = False


def _filename_from_disposition(cd: str | None) -> str | None:
    if not cd:
        return None
    m = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", cd)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd)
    if m:
        return m.group(1)
    m = re.search(r"filename\s*=\s*([^;]+)", cd)
    if m:
        return m.group(1).strip()
    return None


def _sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip() or "download.bin"
    return name


def _chunk_list(size: int, chunk_size: int) -> list[Chunk]:
    chunks = []
    start = 0
    index = 0
    while start < size:
        end = min(start + chunk_size, size) - 1
        chunks.append(Chunk(index=index, start=start, end=end))
        start = end + 1
        index += 1
    return chunks


class MultiThreadDownloader:
    """对单条直链做多线程分块下载。

    url      : 下载直链（浏览器直链、网盘 API 的 dlink 均可）
    headers  : 请求头（Cookie / UA / Referer 等，跟随链接来源）
    output   : 输出文件路径；为 None 时自动取名；为目录时在目录下自动取名
    """

    def __init__(self, url, *, headers=None, output=None, threads=8,
                 chunk_mb=32, insecure=False):
        self.url = url
        self.headers = dict(headers or {})
        self.headers.setdefault("User-Agent", DEFAULT_UA)
        self.output = Path(output) if output else None
        self.threads = max(1, min(int(threads), 64))
        self.chunk_size = max(1, int(chunk_mb)) * 1024 * 1024
        self.insecure = insecure
        self._abort = threading.Event()
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------- public

    def run(self) -> Path:
        session = self._session()
        final_url, size, ranges_ok, etag, disposition = self._probe(session)
        out = self._resolve_output(disposition, final_url)
        if not ranges_ok or not size:
            return self._single_thread(session, final_url, out)

        state_path = out.with_name(out.name + STATE_SUFFIX)
        chunks = self._load_state(state_path, final_url, size, etag)
        if not out.exists() or out.stat().st_size != size:
            chunks = None
        if chunks is None:
            chunks = _chunk_list(size, self.chunk_size)
            self._preallocate(out, size)

        todo = [c for c in chunks if not c.done]
        done_bytes = size - sum(c.end - c.start + 1 for c in todo)
        if todo:
            verb = "续传" if done_bytes else "下载"
            tqdm.write(
                f"{out.name}: 总 {size / 1e6:.1f} MB，"
                f"分 {len(chunks)} 块，{verb}剩余 {len(todo)} 块"
            )

        bar = tqdm(total=size, initial=done_bytes, desc=out.name,
                   unit="B", unit_scale=True, unit_divisor=1024)
        self._chunks = chunks
        self._meta = {"url": final_url, "size": size, "etag": etag,
                      "chunk_size": self.chunk_size}
        pool = ThreadPoolExecutor(self.threads)
        try:
            futures = {pool.submit(self._worker, c, out, final_url, size, bar): c
                       for c in todo}
            failed = None
            for fut in as_completed(futures):
                try:
                    fut.result()
                except BaseException as exc:  # noqa: BLE001 - 取首个失败统一上报
                    if failed is None:
                        failed = exc
                        self._abort.set()
                        for f in futures:
                            f.cancel()
            if failed is not None:
                raise failed
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            bar.close()

        state_path.unlink(missing_ok=True)
        return out

    def stop(self):
        """请求中止当前下载（已完成的分块保留，可续传）。"""
        self._abort.set()

    # ------------------------------------------------------------ workers

    def _worker(self, chunk, out, url, size, bar):
        session = self._session()
        headers = {"Range": f"bytes={chunk.start}-{chunk.end}"}
        last_err = None
        with open(out, "r+b") as fh:
            for attempt in range(RETRY_TIMES):
                pos = chunk.start
                try:
                    if self._abort.is_set():
                        raise DownloadError("已中止")
                    resp = session.get(url, headers=headers, stream=True,
                                       timeout=(PROBE_TIMEOUT, READ_TIMEOUT))
                    with resp:
                        if resp.status_code in (403, 410):
                            raise LinkExpired(
                                f"HTTP {resp.status_code}：直链已失效，请重新获取后重跑同一命令")
                        if resp.status_code == 200:
                            # 服务器忽略 Range，仅当该块就是整个文件时可用
                            if not (chunk.start == 0 and chunk.end == size - 1):
                                raise DownloadError("服务器不支持 Range 分块下载")
                        elif resp.status_code != 206:
                            raise DownloadError(f"HTTP {resp.status_code} {resp.reason}")
                        fh.seek(pos)
                        for data in resp.iter_content(256 * 1024):
                            if self._abort.is_set():
                                raise DownloadError("已中止")
                            fh.write(data)
                            pos += len(data)
                            bar.update(len(data))
                    if pos != chunk.end + 1:
                        raise DownloadError(
                            f"分块 {chunk.index} 提前结束（{pos - chunk.start}/"
                            f"{chunk.end - chunk.start + 1} 字节）")
                    chunk.done = True
                    self._save_state(out)
                    return
                except LinkExpired:
                    raise
                except Exception as exc:  # noqa: BLE001 - 单块失败统一重试
                    last_err = exc
                    bar.update(-(pos - chunk.start))  # 回退本块已计入的进度
                    if attempt + 1 < RETRY_TIMES:
                        time.sleep(min(2 ** attempt, 30) + random.random())
        raise DownloadError(f"分块 {chunk.index} 重试 {RETRY_TIMES} 次仍失败: {last_err}")

    def _single_thread(self, session, url, out):
        resp = session.get(url, stream=True, timeout=(PROBE_TIMEOUT, READ_TIMEOUT))
        bar = None
        try:
            with resp:
                if resp.status_code in (403, 410):
                    raise LinkExpired(f"HTTP {resp.status_code}：直链已失效")
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0) or None
                bar = tqdm(total=total, desc=out.name,
                           unit="B", unit_scale=True, unit_divisor=1024)
                with open(out, "wb") as fh:
                    for data in resp.iter_content(1024 * 1024):
                        if self._abort.is_set():
                            raise DownloadError("已中止")
                        fh.write(data)
                        bar.update(len(data))
        finally:
            if bar:
                bar.close()
        return out

    # -------------------------------------------------------------- probe

    def _probe(self, session):
        headers = dict(self.headers)
        headers["Range"] = "bytes=0-0"
        try:
            resp = session.get(self.url, headers=headers, stream=True,
                               timeout=PROBE_TIMEOUT, allow_redirects=True)
        except requests.RequestException as exc:
            raise DownloadError(f"探测链接失败: {exc}") from exc
        with resp:
            disposition = resp.headers.get("Content-Disposition")
            etag = resp.headers.get("ETag")
            if resp.status_code in (401, 403, 410):
                raise LinkExpired(
                    f"HTTP {resp.status_code}：链接被拒绝，可能已过期或缺少 Cookie/UA")
            if resp.status_code == 206:
                cr = resp.headers.get("Content-Range", "")
                try:
                    total = int(cr.rsplit("/", 1)[-1])
                except ValueError:
                    total = 0
                return resp.url, total, True, etag, disposition
            if resp.status_code == 200:
                total = int(resp.headers.get("Content-Length") or 0)
                return resp.url, total, False, etag, disposition
            raise DownloadError(f"探测链接失败: HTTP {resp.status_code} {resp.reason}")

    # ------------------------------------------------------------- helper

    def _session(self):
        s = requests.Session()
        s.headers.update(self.headers)
        if self.insecure:
            s.verify = False
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:  # noqa: BLE001
                pass
        return s

    def _resolve_output(self, disposition, final_url):
        name = (_filename_from_disposition(disposition)
                or unquote(urlparse(final_url).path.rsplit("/", 1)[-1])
                or "download.bin")
        name = _sanitize(name)
        if self.output is None:
            return Path(name)
        if self.output.exists() and self.output.is_dir():
            return self.output / name
        s = str(self.output)
        if s.endswith(os.sep) or s.endswith("/"):
            return Path(s + name)
        return self.output

    def _preallocate(self, out, size):
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as fh:
            if size > 0:
                fh.seek(size - 1)
                fh.write(b"\0")

    def _save_state(self, out):
        state_path = out.with_name(out.name + STATE_SUFFIX)
        with self._state_lock:
            tmp = state_path.with_name(state_path.name + ".tmp")
            tmp.write_text(json.dumps(
                {**self._meta,
                 "chunks": [asdict(c) for c in self._chunks]}),
                encoding="utf-8")
            os.replace(tmp, state_path)

    def _load_state(self, state_path, url, size, etag):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("size") != size or data.get("chunk_size") != self.chunk_size:
            return None
        if etag and data.get("etag") and data["etag"] != etag:
            return None
        cur = _chunk_list(size, self.chunk_size)
        saved = data.get("chunks") or []
        if len(saved) != len(cur):
            return None
        for c, d in zip(saved, cur):
            if (c.get("start"), c.get("end")) != (d.start, d.end):
                return None
            d.done = bool(c.get("done"))
        return cur

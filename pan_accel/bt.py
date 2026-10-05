"""BT / 磁力链下载，通过 aria2c 引擎（JSON-RPC 控制）。

为何用 aria2c 而不是 libtorrent：libtorrent 官方 Python 绑定目前没有
覆盖所有 Python 版本的 wheel；aria2c 是成熟开源（GPLv2）单文件引擎，
支持 DHT / Tracker / PEX / LPD、磁力链元数据获取、select-file 与续传。

aria2c 获取方式（任选其一）：
    winget install aria2
    scoop install aria2
    或到 https://github.com/aria2/aria2/releases 下载解压，
    将 aria2c(.exe) 放入 ~/.panacc/bin/

断点续传：aria2 的任务 session 按"任务标识"（磁力链 btih / .torrent 文件
哈希）持久化到 <输出目录>/.panacc-aria2/，重跑同一命令自动恢复。
"""
from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from tqdm import tqdm

SESSION_DIRNAME = ".panacc-aria2"
RPC_WAIT = 20  # 等 aria2 RPC 就绪的秒数

ARIA2_CANDIDATES = (
    "aria2c.exe" if os.name == "nt" else "aria2c",
)


class BtError(RuntimeError):
    pass


def find_aria2c(explicit=None) -> str:
    """定位 aria2c：显式路径 > PATH > ~/.panacc/bin > winget/scoop 常见位置。"""
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            p = p / ARIA2_CANDIDATES[0]
        if not p.exists():
            raise BtError(f"指定的 aria2c 路径不存在: {explicit}")
        return str(p)
    found = shutil.which("aria2c")
    if found:
        return found
    for cand in (
        Path.home() / ".panacc" / "bin" / ARIA2_CANDIDATES[0],
        Path.home() / "scoop" / "shims" / ARIA2_CANDIDATES[0],
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "Microsoft" / "WinGet" / "Links" / ARIA2_CANDIDATES[0],
    ):
        if cand.exists():
            return str(cand)
    raise BtError(
        "未找到 aria2c，请任选其一安装：\n"
        "  winget install aria2      (Windows)\n"
        "  scoop install aria2\n"
        "  或从 https://github.com/aria2/aria2/releases 下载解压，\n"
        "  将 aria2c.exe 放入 ~/.panacc/bin/ 目录")


def _bdecode(data, i=0):
    c = data[i:i+1]
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i+1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i+1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i+1] != b"e":
            k, i = _bdecode(data, i)
            v, i = _bdecode(data, i)
            out[k] = v
        return out, i + 1
    j = data.index(b":", i)
    n = int(data[i:j])
    return data[j+1:j+1+n], j+1+n


def torrent_file_list(torrent_bytes):
    """解析 .torrent 的 [(相对路径, 大小)]，失败返回 None。"""
    try:
        data, _ = _bdecode(torrent_bytes)
        info = data.get(b"info") or {}
        name = (info.get(b"name") or b"").decode("utf-8", "replace")
        entries = []
        if b"files" in info:  # 多文件种子
            for f in info[b"files"]:
                parts = [p.decode("utf-8", "replace")
                         for p in f.get(b"path") or []]
                entries.append(("/".join([name] + parts),
                                int(f.get(b"length", 0))))
        else:
            entries.append((name, int(info.get(b"length", 0))))
        return entries
    except Exception:
        return None


def _match_any(rel_path, patterns):
    """rel_path 可为 '种子名/sub/f.mp4'；对全路径与去掉种子名前缀的路径都尝试匹配。"""
    rel = rel_path.replace("\\", "/").lower()
    inner = rel.split("/", 1)[-1]
    return any(fnmatch.fnmatch(rel, p.lower().lstrip("/"))
               or fnmatch.fnmatch(inner, p.lower().lstrip("/"))
               for p in patterns)


def _task_id(uri: str) -> str:
    """任务标识：磁力链用 btih，.torrent 文件用内容哈希，URL 用自身哈希。"""
    u = uri.strip()
    if u.lower().startswith("magnet:"):
        for xt in parse_qs(urlparse(u).query, keep_blank_values=True).get("xt", []):
            m = re.fullmatch(r"urn:btih:([0-9a-fA-F]{40})", xt.strip())
            if m:
                return m.group(1).lower()
        return hashlib.sha1(u.encode()).hexdigest()[:16]
    p = Path(u)
    if p.exists() and p.is_file():
        return hashlib.sha1(p.read_bytes()).hexdigest()[:16]
    return hashlib.sha1(u.encode()).hexdigest()[:16]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Aria2BtDownloader:
    """用本机 aria2c 下载 magnet / .torrent（文件路径或 URL）。

    file_patterns: 通配符列表，仅下载匹配文件（对种子内相对路径匹配）
    seed_minutes : 下载完成后做种分钟数（0 = 完成即停，默认）
    list_only    : 只获取并打印种子内文件列表，不下载
    """

    def __init__(self, uri, output_dir=".", file_patterns=None, seed_minutes=0,
                 metadata_timeout=300, idle_timeout=600, list_only=False,
                 aria2c_path=None):
        self.uri = str(uri).strip()
        if not self.uri:
            raise BtError("缺少 magnet 链接或 .torrent 路径")
        self.is_magnet = self.uri.lower().startswith("magnet:")
        self.is_torrent_url = (not self.is_magnet
                               and self.uri.lower().startswith(("http://", "https://"))
                               and urlparse(self.uri).path.lower().endswith(".torrent"))
        if not (self.is_magnet or self.is_torrent_url or Path(self.uri).exists()):
            raise BtError(
                f"无法识别的任务: {self.uri[:60]} —— 需要 magnet 链接、"
                ".torrent 文件路径或以 .torrent 结尾的 URL")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.file_patterns = list(file_patterns or [])
        self.seed_minutes = max(0, int(seed_minutes))
        self.metadata_timeout = max(30, int(metadata_timeout))
        self.idle_timeout = max(60, int(idle_timeout))
        self.list_only = bool(list_only)
        self.aria2c_path = aria2c_path
        self._proc = None
        self._port = None
        self._secret = None

    # ------------------------------------------------------------- public

    def run(self):
        if self.list_only and not self.is_magnet:
            # .torrent 的文件列表本地即可解析，无需起引擎
            if self.is_torrent_url:
                data = requests.get(self.uri, timeout=60).content
            else:
                p = Path(self.uri)
                if not p.exists():
                    raise BtError(f".torrent 文件不存在: {self.uri}")
                data = p.read_bytes()
            entries = torrent_file_list(data)
            if entries is None:
                raise BtError(".torrent 文件解析失败（不是有效的种子文件）")
            print(f"种子: {entries[0][0].split('/', 1)[0]}"
                  f"（{len(entries)} 个文件）")
            for name, size in entries:
                print(f"  {size / 1e6:10.1f} MB  {name}")
            return None

        aria2c = find_aria2c(self.aria2c_path)
        session_dir = self.output_dir / SESSION_DIRNAME
        session_dir.mkdir(parents=True, exist_ok=True)
        session_file = session_dir / f"{_task_id(self.uri)}.session"
        self._session_file = session_file
        self._port = _free_port()
        self._secret = secrets.token_hex(8)

        cmd = [
            aria2c,
            "--enable-rpc=true",
            f"--rpc-listen-port={self._port}",
            f"--rpc-secret={self._secret}",
            "--rpc-listen-all=false",
            f"--dir={self.output_dir}",
            "--console-log-level=warn",
            "--summary-interval=0",
            "--continue=true",
            "--auto-file-renaming=false",
            "--bt-enable-lpd=true",
            f"--seed-time={self.seed_minutes}",
            f"--save-session={session_file}",
            "--save-session-interval=5",
            "--force-save=true",
            f"--stop-with-process={os.getpid()}",
        ]
        if session_file.exists():
            cmd.append(f"--input-file={session_file}")
        if self.is_magnet:
            cmd.append("--bt-save-metadata=true")

        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            self._wait_rpc()
            gid = self._add_task()
            try:
                if self.list_only:
                    self._list_files(gid)
                    return None
                final_gid = self._download(gid)
                return self._result_files(final_gid)
            finally:
                self._cleanup_task(gid)
        finally:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None

    # -------------------------------------------------------------- setup

    def _wait_rpc(self):
        start = time.time()
        while time.time() - start < RPC_WAIT:
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() or b"").decode("utf-8", "replace")
                raise BtError(f"aria2c 启动失败:\n{err.strip() or '(无输出)'}")
            try:
                self._rpc("aria2.getVersion")
                return
            except (requests.RequestException, BtError):
                time.sleep(0.3)
        raise BtError("aria2c RPC 未就绪（超时）")

    def _add_task(self):
        if self.is_magnet:
            return self._rpc("aria2.addUri", [[self.uri]])
        if self.is_torrent_url:
            resp = requests.get(self.uri, timeout=60)
            resp.raise_for_status()
            data = resp.content
        else:
            p = Path(self.uri)
            if not p.exists():
                raise BtError(f".torrent 文件不存在: {self.uri}")
            data = p.read_bytes()
        options = {}
        if self.file_patterns:
            # 添加任务时就带 select-file，未选中的文件从第一秒起就不下载
            names = torrent_file_list(data)
            if names is not None:
                kept = [i + 1 for i, (n, _sz) in enumerate(names)
                        if _match_any(n, self.file_patterns)]
                if not kept:
                    raise BtError("没有文件匹配 --file 过滤条件: "
                                  + ", ".join(self.file_patterns))
                options["select-file"] = ",".join(map(str, kept))
        if options:
            # addTorrent 签名: (torrent, uris[], options) —— uris 需空数组占位
            return self._rpc("aria2.addTorrent",
                             [base64.b64encode(data).decode(), [], options])
        return self._rpc("aria2.addTorrent",
                         [base64.b64encode(data).decode()])

    # -------------------------------------------------------------- tasks

    def _list_files(self, gid):
        print("等待种子元数据……")
        start = time.time()
        st = None
        while time.time() - start < self.metadata_timeout:
            st = self._tell(gid)
            nxt = self._followed(st)
            if nxt:
                gid = nxt
                st = self._tell(gid)
                break
            if int(st.get("numFiles", 0)) > 0 and self._torrent_name(st):
                break  # .torrent 直下：元数据现成
            self._raise_if_error(st, metadata=True)
            if self._proc.poll() is not None:
                raise BtError("aria2c 意外退出")
            time.sleep(0.5)
        else:
            raise BtError("获取种子元数据超时：未发现可用节点，磁力链可能已失效")
        name = self._torrent_name(st) or "(未知)"
        files = st.get("files") or []
        print(f"种子: {name}（{len(files)} 个文件）")
        for f in files:
            rel = os.path.relpath(f.get("path", ""), str(self.output_dir))
            print(f"  {int(f.get('length', 0)) / 1e6:10.1f} MB  {rel}")

    def _download(self, gid):
        bar = None
        done = 0
        filters_applied = False
        meta_start = time.time()
        idle_since = None
        try:
            while True:
                if self._proc.poll() is not None:
                    raise BtError("aria2c 意外退出（查看输出目录内日志或重试）")
                st = self._tell(gid)
                self._raise_if_error(st)
                nxt = self._followed(st)
                if nxt:
                    gid = nxt  # 跟随到元数据完成后的真正下载任务
                    continue
                status = st.get("status")

                if not filters_applied and self.file_patterns \
                        and int(st.get("numFiles", 0)) > 0:
                    self._apply_filters(gid, st)
                    filters_applied = True
                    st = self._tell(gid)

                total = int(st.get("totalLength", 0))
                got = int(st.get("completedLength", 0))
                if total > 0 and bar is None:
                    name = self._torrent_name(st) or "BT"
                    bar = tqdm(total=total, initial=got, desc=name[:48],
                               unit="B", unit_scale=True, unit_divisor=1024)
                    done = got
                elif bar and total > 0 and bar.total != total:
                    bar.total = total  # select-file 后 wanted 总量变化
                    bar.refresh()
                if bar:
                    bar.update(got - done)
                    done = got
                    speed = int(st.get("downloadSpeed", 0))
                    bar.set_postfix(speed=f"{speed / 1000:.0f}KB/s",
                                    peers=int(st.get("connections", 0)))

                if status == "complete":
                    break
                if status == "removed":
                    raise BtError("任务被移除")
                if int(st.get("seeder", 0) in (1, "1", "true")):
                    # 下载完成。seed-time>0 时等 aria2 做种到点（Ctrl+C 可提前结束）；
                    # seed-time=0 时 aria2(RPC 常驻)不会自行退出，直接结束
                    if bar:
                        bar.close()
                        bar = None
                    if self.seed_minutes > 0:
                        tqdm.write(
                            f"下载完成，做种 {self.seed_minutes} 分钟后自动结束"
                            "（Ctrl+C 立即退出并保存进度）")
                        self._proc.wait()
                    break
                if total == 0:
                    # 还在获取磁力链元数据
                    if time.time() - meta_start > self.metadata_timeout:
                        raise BtError(
                            "获取种子元数据超时：未发现可用节点，磁力链可能已失效")
                elif int(st.get("downloadSpeed", 0)) < 1024 \
                        and status == "active":
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since > self.idle_timeout:
                        raise BtError(
                            "长时间无可下载节点（无速度）。已下载部分已保存，"
                            "重新执行同一命令可继续")
                else:
                    idle_since = None
                    meta_start = time.time()
                time.sleep(0.5)
        finally:
            if bar:
                bar.close()
        return gid

    def _apply_filters(self, gid, st):
        base = str(self.output_dir)
        kept = []
        for i, f in enumerate(st.get("files") or []):
            rel = os.path.relpath(f.get("path", ""), base)
            if _match_any(rel, self.file_patterns):
                kept.append(i + 1)  # aria2 文件索引 1-based
        if not kept:
            raise BtError("没有文件匹配 --file 过滤条件: "
                          + ", ".join(self.file_patterns))
        self._rpc("aria2.changeOption",
                  [gid, {"select-file": ",".join(map(str, kept))}])

    # ------------------------------------------------------------ results

    def _result_files(self, gid):
        st = self._tell(gid)
        return [f["path"] for f in st.get("files") or []
                if f.get("selected", "true") == "true"]

    def _cleanup_task(self, gid):
        # 任务完成后移除 aria2 内的任务记录并清理续传 session，
        # 避免残留 checkpoint 影响之后对同一种子的新任务
        try:
            st = self._tell(gid)
            if st.get("status") == "complete":
                self._rpc("aria2.remove", [gid])
                self._session_file.unlink(missing_ok=True)
        except BtError:
            pass

    # --------------------------------------------------------------- misc

    def _torrent_name(self, st):
        info = (st.get("bittorrent") or {}).get("info") or {}
        return info.get("name")

    def _raise_if_error(self, st, metadata=False):
        if st.get("status") == "error":
            msg = st.get("errorMessage") or "未知错误"
            raise BtError(
                f"aria2 任务出错: {msg}" +
                ("" if not metadata else "（获取元数据阶段）"))

    def _tell(self, gid):
        return self._rpc("aria2.tellStatus",
                         [gid, ["status", "totalLength", "completedLength",
                                "downloadSpeed", "uploadSpeed", "connections",
                                "numFiles", "errorMessage", "seeder",
                                "followedBy", "bittorrent", "files"]])

    def _followed(self, st):
        """magnet 是两段式任务：元数据完成后由 followedBy 指向真正的 BT 任务。"""
        followed = st.get("followedBy") or []
        return followed[0] if followed else None

    def _rpc(self, method, params=()):
        r = requests.post(
            f"http://127.0.0.1:{self._port}/jsonrpc",
            json={"jsonrpc": "2.0", "id": "panacc", "method": method,
                  "params": ["token:" + self._secret, *params]},
            timeout=10)
        data = r.json()
        if "error" in data:
            raise BtError(f"aria2 RPC {method}: {data['error'].get('message')}")
        return data["result"]

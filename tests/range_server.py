"""测试用：支持 HTTP Range 的本地限速静态文件服务器。

可关闭 Range 支持、按字节速率限流，用于验证
分块并发、断点续传和单线程退化三条路径。
"""
from __future__ import annotations

import functools
import http.server
import os
import re
import time


class _RangeHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and self.server.support_ranges and size > 0:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                a, b = m.groups()
                if a:
                    start = int(a)
                    end = int(b) if b else size - 1
                elif b:
                    start = max(size - int(b), 0)
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_error(416)
                    return
                status = 206
        self.send_response(status)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError,
                        ConnectionAbortedError):
                    return
                remaining -= len(chunk)
                if self.server.throttle:
                    time.sleep(len(chunk) / self.server.throttle)

    def log_message(self, *args):  # 测试时不刷日志
        pass


def make_server(directory, filename=None, throttle=0, support_ranges=True):
    """起一个 daemon 线程的本地服务器，返回 (httpd, url)。"""
    handler = functools.partial(_RangeHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.throttle = throttle
    httpd.support_ranges = support_ranges
    name = filename
    if name is None:
        name = next(f for f in os.listdir(directory)
                    if os.path.isfile(os.path.join(directory, f)))
    port = httpd.server_address[1]
    return httpd, f"http://127.0.0.1:{port}/{name}"

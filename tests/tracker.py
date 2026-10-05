"""测试用：极简 HTTP tracker。

只做一件事：对所有 announce 请求返回固定 peer（本机做种端端口），
让两个本机 aria2 之间通过 tracker 建立连接，验证完整 BT 流程。
"""
from __future__ import annotations

import http.server
import socket
import struct
from urllib.parse import parse_qs, urlparse


class _TrackerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        if "info_hash" not in q:
            self.send_error(400, "missing info_hash")
            return
        # compact peers 格式：4 字节网络序 IP + 2 字节大端端口
        compact = struct.pack(">4sH", socket.inet_aton("127.0.0.1"),
                              self.server.seed_port)
        body = (b"d8:completei1e10:incompletei0e8:intervali1e"
                b"12:min intervali1e5:peers%d:%se" % (len(compact), compact))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 测试时不刷日志
        pass


def make_tracker(seed_port):
    """起一个 daemon 兼容的 tracker 服务器，返回 (httpd, announce_url)。"""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _TrackerHandler)
    httpd.seed_port = seed_port
    port = httpd.server_address[1]
    return httpd, f"http://127.0.0.1:{port}/announce"

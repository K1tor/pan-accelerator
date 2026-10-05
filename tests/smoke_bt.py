"""BT / 磁力链冒烟测试：本机做种 + 本机 tracker + panacc bt 下载。

覆盖：magnet 元数据获取与下载、.torrent 直接下载、--file 过滤、--list。
前置：aria2c 可用（find_aria2c 的搜索路径），pip install torf。
用法: python -m tests.smoke_bt
"""
from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pan_accel.bt import BtError, find_aria2c  # noqa: E402
from tests.tracker import make_tracker  # noqa: E402

SEED_PORT = 6885
PIECE = 256 * 1024


def md5f(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_cli(*argv, timeout=240):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT))
    proc = subprocess.Popen([sys.executable, "-m", "pan_accel.cli", *argv],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=env)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise AssertionError(
            f"panacc 超时（{timeout}s）\n--- stdout ---\n{out[-2000:]}"
            f"\n--- stderr ---\n{err[-2000:]}") from None
    return _Result(proc.returncode, out, err)


def wait_port(port, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            time.sleep(0.5)
        finally:
            s.close()
    return False


def build_fixture(tmp, announce, webseed):
    """构造种子内容（两个文件，含子目录），生成带 tracker 与 webseed 的 .torrent。

    webseed 让做种端先从本地 HTTP 服务器真实下载一遍获得完整数据，
    之后自然做种——避免 aria2 对"已存在文件 + 无 control file"拒做种的问题。
    """
    import torf

    seed_root = tmp / "seed"          # webseed 源目录，torrent 名 MyTorrent
    content = seed_root / "MyTorrent"
    (content / "sub").mkdir(parents=True)
    data1 = os.urandom(3 * 1024 * 1024)
    data2 = os.urandom(3 * 1024 * 1024)
    (content / "data1.bin").write_bytes(data1)
    (content / "sub" / "data2.bin").write_bytes(data2)

    torrent = tmp / "MyTorrent.torrent"
    t = torf.Torrent(path=content, trackers=[announce], webseeds=[webseed])
    t.generate()
    t.write(torrent)
    return torrent, t.infohash, {
        "data1.bin": hashlib.md5(data1).hexdigest(),
        "sub/data2.bin": hashlib.md5(data2).hexdigest(),
    }


def main():
    try:
        aria2c = find_aria2c()
    except BtError as exc:
        print(f"SKIP：{exc}")
        return 0
    print(f"aria2c: {aria2c}")
    try:
        import torf  # noqa: F401
    except ImportError:
        print("SKIP：缺少 torf（pip install torf）")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="panacc_bt_"))
    tracker, announce = make_tracker(SEED_PORT)
    import threading
    threading.Thread(target=tracker.serve_forever, daemon=True).start()

    from tests.range_server import make_server
    web_server, _ = make_server(tmp / "seed", filename="MyTorrent/data1.bin")
    threading.Thread(target=web_server.serve_forever, daemon=True).start()
    web_base = f"http://127.0.0.1:{web_server.server_address[1]}"

    torrent, infohash, expect = build_fixture(tmp, announce, web_base + "/")

    # 做种端：先从 webseed 完整下载，随后进入做种（tracker 间隔 1s 可被找到）
    seeder_log = open(tmp / "seeder.log", "wb")
    seeder = subprocess.Popen(
        [aria2c, f"--dir={tmp}", f"--listen-port={SEED_PORT}",
         "--enable-dht=false", "--bt-enable-lpd=false", "--seed-time=3600",
         "--console-log-level=warn", "--summary-interval=0",
         "--enable-peer-exchange=false", str(torrent)],
        stdout=seeder_log, stderr=subprocess.STDOUT)
    seeded = tmp / "MyTorrent" / "data1.bin"
    deadline = time.time() + 90
    while time.time() < deadline:
        if seeded.exists() and seeded.stat().st_size == 3 * 1024 * 1024 \
                and md5f(seeded) == expect["data1.bin"]:
            break
        time.sleep(1)
    else:
        seeder.terminate()
        raise AssertionError("做种端未能在 webseed 完成下载：\n"
                             + (tmp / "seeder.log").read_text(encoding="utf-8", errors="replace")[-1500:])
    time.sleep(2)  # 等 announce 生效
    print(f"做种就绪（6885）tracker: {announce}")

    magnet = f"magnet:?xt=urn:btih:{infohash}&tr={quote(announce, safe='')}"
    try:
        print("[1/4] magnet 下载（tracker + 元数据获取）")
        r = run_cli("bt", magnet, "-o", str(tmp / "dl1"))
        assert r.returncode == 0, f"退出码 {r.returncode}\n{r.stdout}{r.stderr}"
        assert md5f(tmp / "dl1" / "MyTorrent" / "data1.bin") == expect["data1.bin"]
        assert md5f(tmp / "dl1" / "MyTorrent" / "sub" / "data2.bin") == expect["sub/data2.bin"]
        print("      通过")

        print("[2/4] .torrent 文件直接下载")
        r = run_cli("bt", str(torrent), "-o", str(tmp / "dl2"))
        assert r.returncode == 0, f"退出码 {r.returncode}\n{r.stdout}{r.stderr}"
        assert md5f(tmp / "dl2" / "MyTorrent" / "data1.bin") == expect["data1.bin"]
        print("      通过")

        print("[3/4] --file 过滤（仅 sub/*）")
        r = run_cli("bt", str(torrent), "-o", str(tmp / "dl3"),
                    "--file", "sub/*")
        assert r.returncode == 0, f"退出码 {r.returncode}\n{r.stdout}{r.stderr}"
        assert (tmp / "dl3" / "MyTorrent" / "sub" / "data2.bin").exists()
        assert not (tmp / "dl3" / "MyTorrent" / "data1.bin").exists(), \
            "未被选择的文件不应下载"
        print("      通过")

        print("[4/4] --list 只列文件不下载")
        r = run_cli("bt", str(torrent), "--list")
        assert r.returncode == 0, r.stderr
        assert "data1.bin" in r.stdout and "sub/data2.bin" in r.stdout, r.stdout
        print("      通过")
    finally:
        seeder.terminate()
        try:
            seeder.wait(timeout=10)
        except subprocess.TimeoutExpired:
            seeder.kill()
        tracker.shutdown()

    print("\nBT SMOKE PASS ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())

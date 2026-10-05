"""冒烟测试：验证多线程下载、断点续传、cURL 解析与错误处理。

用法（项目根目录下）: python -m tests.smoke
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pan_accel.curlimport import parse_curl  # noqa: E402
from tests.range_server import make_server  # noqa: E402

THROTTLE = 2.5 * 1024 * 1024  # 每连接限速 2.5 MB/s，4 线程合计 10 MB/s
SIZE = 40 * 1024 * 1024


def md5f(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run_cli(*argv, timeout=180):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "pan_accel.cli", *argv],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="panacc_smoke_"))
    payload = tmp / "payload.bin"
    data = os.urandom(SIZE)
    payload.write_bytes(data)
    expect = hashlib.md5(data).hexdigest()
    small = tmp / "small.bin"
    small_data = os.urandom(6 * 1024 * 1024)
    small.write_bytes(small_data)
    expect_small = hashlib.md5(small_data).hexdigest()
    print(f"测试文件: {SIZE / 1e6:.0f} MB @ {THROTTLE / 1e6:.0f} MB/s  md5={expect[:12]}...")

    server, url = make_server(tmp, filename="payload.bin", throttle=THROTTLE)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("[1/5] 多线程分块下载")
    out = tmp / "out.bin"
    r = run_cli("dl", url, "-o", str(out), "-t", "4", "--chunk-mb", "8")
    assert r.returncode == 0, f"退出码 {r.returncode}\n{r.stderr}{r.stdout}"
    assert md5f(out) == expect, "完整下载 MD5 不一致"
    assert not out.with_name(out.name + ".panacc.state.json").exists()
    print("      通过")

    print("[2/5] 中断后断点续传")
    out2 = tmp / "out2.bin"
    state = out2.with_name(out2.name + ".panacc.state.json")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT))
    proc = subprocess.Popen(
        [sys.executable, "-m", "pan_accel.cli", "dl", url,
         "-o", str(out2), "-t", "4", "--chunk-mb", "2"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    import time
    time.sleep(1.5)  # 每块 0.8s，此时已有若干块完成、仍有未完成块
    proc.terminate()
    proc.wait(timeout=15)
    assert state.exists(), "中断后应保留分块状态文件"
    st = json.loads(state.read_text(encoding="utf-8"))
    assert any(not c["done"] for c in st["chunks"]), "中断后应有未完成分块"
    r = run_cli("dl", url, "-o", str(out2), "-t", "4", "--chunk-mb", "2")
    assert r.returncode == 0, f"续传失败\n{r.stderr}{r.stdout}"
    assert md5f(out2) == expect, "续传后 MD5 不一致"
    assert not state.exists()
    print("      通过")

    print("[3/5] 直链过期后换新链接续传（状态文件复用）")
    # 重新生成一个等效 payload 的服务器模拟"新直链"，URL 不同但文件一致
    server2, url2 = make_server(tmp, filename="payload.bin", throttle=THROTTLE)
    threading.Thread(target=server2.serve_forever, daemon=True).start()
    out3 = tmp / "out3.bin"
    env2 = env
    proc = subprocess.Popen(
        [sys.executable, "-m", "pan_accel.cli", "dl", url,
         "-o", str(out3), "-t", "2", "--chunk-mb", "2"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env2)
    time.sleep(1.2)
    proc.terminate()
    proc.wait(timeout=15)
    state2 = out3.with_name(out3.name + ".panacc.state.json")
    assert state2.exists(), "中断后应保留分块状态"
    r = run_cli("dl", url2, "-o", str(out3), "-t", "2", "--chunk-mb", "2")
    assert r.returncode == 0, f"换链接续传失败\n{r.stderr}{r.stdout}"
    assert md5f(out3) == expect
    print("      通过")

    print("[4/5] cURL 命令解析（bash / PowerShell 格式）")
    u, h, m = parse_curl(
        "curl 'https://ex.com/f.bin?sig=1' -H 'User-Agent: Mozilla/5.0' "
        "-H 'Cookie: a=b; c=d' --compressed -o file -L")
    assert u == "https://ex.com/f.bin?sig=1", u
    assert h == ["User-Agent: Mozilla/5.0", "Cookie: a=b; c=d"], h
    assert m is None, m
    u, h, m = parse_curl(
        "curl.exe 'https://ex.com/f.bin' `\n"
        "  -H 'User-Agent: netdisk;5.0' `\n"
        "  -X POST")
    assert u == "https://ex.com/f.bin", u
    assert h == ["User-Agent: netdisk;5.0"], h
    assert m == "POST", m
    print("      通过")

    print("[5/5] 错误处理（404）与单线程退化")
    r = run_cli("dl", url.replace("payload.bin", "nope.bin"), timeout=60)
    assert r.returncode != 0, "404 应返回非零退出码"
    server_norange, url_norange = make_server(
        tmp, filename="small.bin", throttle=THROTTLE, support_ranges=False)
    threading.Thread(target=server_norange.serve_forever, daemon=True).start()
    out4 = tmp / "out4.bin"
    r = run_cli("dl", url_norange, "-o", str(out4))
    assert r.returncode == 0, f"单线程退化失败\n{r.stderr}{r.stdout}"
    assert md5f(out4) == expect_small
    print("      通过")

    server.shutdown()
    server2.shutdown()
    server_norange.shutdown()
    print("\nSMOKE PASS ✅")


if __name__ == "__main__":
    main()

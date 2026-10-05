"""panacc 命令行入口。

用法概览:
    panacc dl <直链> [-o 输出] [-t 线程数] [--curl "..."] [--cookie ...] [--ua ...]
    panacc baidu auth --app-key K --app-secret S
    panacc baidu ls [/路径]
    panacc baidu dl /路径 [-o 输出目录] [-t 线程数]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__
from .bt import Aria2BtDownloader, BtError
from .curlimport import CurlParseError, parse_curl
from .downloader import DEFAULT_UA, DownloadError, LinkExpired, MultiThreadDownloader
from .providers.baidu_open import BaiduOpen, BaiduOpenError, DOWNLOAD_UA
from .thunder import ThunderError, decode_thunder
from .verify import check, hash_file


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="panacc",
        description="网盘/直链下载加速器：HTTP 多线程（百度网盘、夸克、迅雷直链）"
                    "与 BT/磁力链（aria2 引擎）。不破解限速，只把你有权下载的"
                    "内容下载得更快。")
    p.add_argument("--version", action="version", version=f"panacc {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dl", help="下载直链（浏览器 Copy as cURL 或裸 URL）")
    d.add_argument("url", nargs="?", help="下载直链")
    d.add_argument("-o", "--out", help="输出文件路径或目录（默认自动命名）")
    d.add_argument("-t", "--threads", type=int, default=8, help="并发线程数（默认 8）")
    d.add_argument("--chunk-mb", type=int, default=32, help="分块大小 MB（默认 32）")
    d.add_argument("-H", "--header", action="append", default=[], metavar="K: V",
                   help="附加请求头，可重复")
    d.add_argument("--cookie", help="Cookie 字符串")
    d.add_argument("--ua", help="User-Agent")
    d.add_argument("--curl", help='粘贴浏览器「Copy as cURL」整段命令，自动提取链接与请求头')
    d.add_argument("--expect-md5", help="下载后校验 MD5")
    d.add_argument("--expect-sha1", help="下载后校验 SHA1")
    d.add_argument("--insecure", action="store_true", help="跳过 TLS 证书校验")

    b = sub.add_parser("baidu", help="百度网盘（官方开放平台 API）")
    bsub = b.add_subparsers(dest="bcmd", required=True)

    bt = sub.add_parser("bt", help="下载磁力链 / .torrent（BT，aria2 引擎）")
    bt.add_argument("uri", help="magnet:?xt=... 或 .torrent 文件路径/URL")
    bt.add_argument("-o", "--out-dir", default=".", help="输出目录（默认当前目录）")
    bt.add_argument("--file", action="append", metavar="模式",
                    help="仅下载匹配的文件（通配符，如 'sub/*.mp4'，可重复）")
    bt.add_argument("--list", action="store_true", help="仅列出种子内文件，不下载")
    bt.add_argument("--seed-minutes", type=int, default=0,
                    help="下载完成后做种分钟数（默认 0，即完成即停）")
    bt.add_argument("--metadata-timeout", type=int, default=300,
                    help="磁力链元数据获取超时秒数（默认 300）")
    bt.add_argument("--idle-timeout", type=int, default=600,
                    help="有元数据后无下载速度的容忍秒数（默认 600）")
    bt.add_argument("--aria2c", help="指定 aria2c 可执行文件路径")

    ba = bsub.add_parser("auth", help="登录/授权（需要开放平台 AppKey/SecretKey）")
    ba.add_argument("--app-key", required=True)
    ba.add_argument("--app-secret", required=True)

    bl = bsub.add_parser("ls", help="列目录")
    bl.add_argument("path", nargs="?", default="/", help="网盘路径（默认根目录）")

    bd = bsub.add_parser("dl", help="下载文件或整个目录（目录自动递归）")
    bd.add_argument("path", help="网盘路径，如 /电影/a.mkv 或 /资料")
    bd.add_argument("-o", "--out-dir", default=".", help="输出目录（保留网盘目录结构）")
    bd.add_argument("-t", "--threads", type=int, default=8, help="单文件并发线程数")
    bd.add_argument("--chunk-mb", type=int, default=32, help="分块大小 MB")
    bd.add_argument("--insecure", action="store_true", help="跳过 TLS 证书校验")

    return p


def _split_headers(pairs):
    out = {}
    for h in pairs:
        if ":" not in h:
            raise SystemExit(f"请求头格式应为 'K: V'，收到: {h}")
        k, v = h.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _cmd_dl(args):
    url = args.url
    if url and url.strip().lower().startswith("thunder://"):
        url = decode_thunder(url.strip())
        print(f"已解码迅雷链接: {url}")
    headers = {}
    if args.curl:
        cu, ch, method = parse_curl(args.curl)
        if cu:
            url = cu
        if method and method.upper() != "GET":
            raise SystemExit(
                f"--curl 解析到的是 {method.upper()} 请求（通常是取直链的接口），"
                "请复制对 CDN 域名的 GET 下载请求，或直接粘贴直链")
        headers.update(_split_headers(ch))
    if not url:
        raise SystemExit("缺少下载链接：请提供 URL 或使用 --curl")
    headers.setdefault("User-Agent", args.ua or DEFAULT_UA)
    if args.ua:
        headers["User-Agent"] = args.ua
    if args.cookie:
        headers["Cookie"] = args.cookie
    headers.update(_split_headers(args.header))

    dl = MultiThreadDownloader(url, headers=headers, output=args.out,
                               threads=args.threads, chunk_mb=args.chunk_mb,
                               insecure=args.insecure)
    out = dl.run()

    problems = check(out, args.expect_md5, args.expect_sha1)
    if problems:
        for msg in problems:
            print(msg, file=sys.stderr)
        print(f"文件已保留: {out}", file=sys.stderr)
        sys.exit(2)
    print(f"完成: {out}")


def _cmd_bt(args):
    downloader = Aria2BtDownloader(
        args.uri, output_dir=args.out_dir, file_patterns=args.file,
        seed_minutes=args.seed_minutes, metadata_timeout=args.metadata_timeout,
        idle_timeout=args.idle_timeout, list_only=args.list,
        aria2c_path=args.aria2c)
    try:
        files = downloader.run()
    except KeyboardInterrupt:
        # run() 的 finally 已终止 aria2c 并保存 session
        print("\n已中断。任务进度已保存，重新执行同一命令可续传。")
        os._exit(130)
    if files:
        print(f"完成 {len(files)} 个文件:")
        for f in files:
            print(f"  {f}")


def _cmd_baidu(args):
    if args.bcmd == "auth":
        path = BaiduOpen.login(args.app_key, args.app_secret)
        print(f"已保存凭证到 {path}")
        return
    baidu = BaiduOpen()
    if args.bcmd == "ls":
        _baidu_ls(baidu, args.path)
        return
    if args.bcmd == "dl":
        _baidu_dl(baidu, args)


def _baidu_ls(baidu, path):
    def human(n):
        n = float(n)
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or u == "TB":
                return f"{n:.1f}{u}"
            n /= 1024

    items = baidu.list(path)
    if not items:
        print("（空目录）")
        return
    for it in items:
        kind = "d" if it.get("isdir") else "-"
        size = "" if it.get("isdir") else human(it.get("size", 0))
        print(f"{kind}  {size:>10}  {it.get('server_filename', it.get('path'))}")


def _baidu_dl(baidu, args):
    pairs = baidu.resolve(args.path)
    out_dir = Path(args.out_dir)
    ok = fail = 0
    for entry, meta, root_dir in pairs:
        rel = meta["path"][len(root_dir):].lstrip("/")
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        size_mb = meta.get("size", 0) / 1e6
        print(f"下载 {meta['path']} -> {dest}（{size_mb:.1f} MB）")
        try:
            dl = MultiThreadDownloader(
                meta["dlink"], headers={"User-Agent": DOWNLOAD_UA},
                output=dest, threads=args.threads, chunk_mb=args.chunk_mb,
                insecure=args.insecure)
            dl.run()
            md5 = meta.get("md5")
            if md5:
                got = hash_file(dest, "md5")
                if got.lower() != md5.lower():
                    print("  ⚠ MD5 与网盘端不一致（大文件时网盘可能按分块规则记录 md5），文件已保留")
            ok += 1
        except (DownloadError, LinkExpired) as exc:
            print(f"  ✗ 失败: {exc}")
            fail += 1
        time.sleep(0.2)  # 轻微间隔，礼貌使用官方接口
    print(f"完成 {ok} 个，失败 {fail} 个")
    if fail:
        sys.exit(1)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "dl":
            _cmd_dl(args)
        elif args.cmd == "bt":
            _cmd_bt(args)
        elif args.cmd == "baidu":
            _cmd_baidu(args)
    except (DownloadError, BaiduOpenError, CurlParseError, BtError,
            ThunderError) as exc:
        sys.exit(f"错误: {exc}")
    except KeyboardInterrupt:
        print("\n已中断。已完成分块的状态已保存，重新执行同一命令即可续传。")
        os._exit(130)


if __name__ == "__main__":
    main()

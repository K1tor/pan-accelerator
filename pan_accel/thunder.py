"""迅雷专有链接协议（thunder://）解码。

thunder:// 链接 = base64( "AA" + 原始链接 + "ZZ" )，
原始链接通常为 http(s)/ftp 直链。ed2k 链接不支持（需要 eD2k 网络）。
"""
from __future__ import annotations

import base64

__all__ = ["decode_thunder", "ThunderError"]


class ThunderError(ValueError):
    pass


def decode_thunder(url: str) -> str:
    """把 thunder:// 链接还原成原始下载链接。"""
    scheme, sep, payload = url.strip().partition("://")
    if not sep or scheme.lower() != "thunder":
        raise ThunderError(f"不是迅雷链接: {url[:60]}")
    try:
        raw = base64.b64decode(payload.strip(), validate=True)
    except Exception as exc:
        raise ThunderError(f"thunder:// 内容解码失败: {exc}") from exc
    if not (raw.startswith(b"AA") and raw.endswith(b"ZZ")):
        raise ThunderError("thunder:// 内容格式异常（缺少 AA/ZZ 包裹）")
    inner = raw[2:-2].decode("utf-8", "replace").strip()
    if not inner:
        raise ThunderError("thunder:// 解码结果为空")
    if inner.lower().startswith(("ed2k://", "magnet:")):
        raise ThunderError(
            f"解码出的是 {inner.split(':', 1)[0]} 链接，请直接使用 panacc bt 下载")
    return inner

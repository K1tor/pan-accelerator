"""下载结果哈希校验。"""
from __future__ import annotations

import hashlib
from pathlib import Path


def hash_file(path, algo="md5", buf=1024 * 1024) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(buf), b""):
            h.update(block)
    return h.hexdigest()


def check(path, expect_md5=None, expect_sha1=None) -> list[str]:
    """返回不一致项的描述列表，空列表表示全部通过。"""
    problems = []
    path = Path(path)
    if expect_md5:
        got = hash_file(path, "md5")
        if got.lower() != expect_md5.lower().removeprefix("md5:"):
            problems.append(f"MD5 校验不一致：期望 {expect_md5}，实际 {got}")
    if expect_sha1:
        got = hash_file(path, "sha1")
        if got.lower() != expect_sha1.lower().removeprefix("sha1:"):
            problems.append(f"SHA1 校验不一致：期望 {expect_sha1}，实际 {got}")
    return problems

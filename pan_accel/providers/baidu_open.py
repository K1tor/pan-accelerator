"""百度网盘官方开放平台（pan.baidu.com/union）接入。

这是百度官方提供的合法途径：开发者实名认证后在开放平台创建应用，
通过 OAuth 拿到 access_token，用 xpan 接口列目录、取 dlink 高速下载。
个人认证账号享有官方下载配额，不涉及任何破解。

首次使用：
    panacc baidu auth --app-key <AppKey> --app-secret <SecretKey>
"""
from __future__ import annotations

import json
import time
import webbrowser
from pathlib import Path

import requests

AUTH_BASE = "https://openapi.baidu.com/oauth/2.0"
API_FILE = "https://pan.baidu.com/rest/2.0/xpan/file"
DOWNLOAD_UA = "pan.baidu.com"  # 官方文档要求的下载 UA

ERRMSG = {
    2: "参数错误",
    -6: "身份验证失败（access_token 无效）",
    -9: "路径不存在",
    12: "请求过于频繁（QPS 超限）",
    111: "access_token 已过期",
    31066: "文件或目录不存在",
}


class BaiduOpenError(RuntimeError):
    pass


class BaiduOpen:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path) if config_path else (
            Path.home() / ".panacc" / "baidu.json")

    # ------------------------------------------------------------- auth

    @staticmethod
    def login(app_key: str, app_secret: str, config_path=None) -> Path:
        """OOB 授权流程：打开浏览器 -> 用户粘贴授权码 -> 换 token 保存。"""
        url = (f"{AUTH_BASE}/authorize?response_type=code"
               f"&client_id={app_key}&redirect_uri=oob"
               f"&scope=basic,netdisk&display=page")
        print("即将打开百度授权页面，登录后页面会显示授权码：")
        print(url)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 无 GUI 环境忽略
            pass
        code = input("粘贴授权码: ").strip()
        resp = requests.get(
            f"{AUTH_BASE}/token",
            params={"grant_type": "authorization_code", "code": code,
                    "client_id": app_key, "client_secret": app_secret,
                    "redirect_uri": "oob"},
            timeout=30)
        data = resp.json()
        if "access_token" not in data:
            raise BaiduOpenError(f"获取 token 失败: {data}")
        cfg = {
            "app_key": app_key,
            "app_secret": app_secret,
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": time.time() + int(data.get("expires_in", 2592000)),
        }
        path = Path(config_path) if config_path else (
            Path.home() / ".panacc" / "baidu.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return path

    # ------------------------------------------------------------ files

    def list(self, dir_path="/"):
        """列目录（自动翻页）。返回 xpan file.list 的条目列表。"""
        dir_path = _norm_dir(dir_path)
        items, page = [], 1
        while True:
            data = self._rest("list", {
                "dir": dir_path, "web": "1", "limit": "1000",
                "page": str(page), "order": "name", "desc": "0"})
            files = data.get("list") or []
            items.extend(files)
            if len(files) < 1000:
                return items
            page += 1

    def resolve(self, path: str):
        """把网盘路径解析成 [(文件条目, filemetas 元信息)]。

        path 为文件时返回单文件；为目录时递归返回目录下全部文件。
        """
        path = _norm_dir(path)
        if path == "/":
            root, name = "/", ""
        else:
            root, name = path.rsplit("/", 1)
            root = root or "/"
        items = self.list(root)
        match = next((it for it in items
                      if it.get("server_filename") == name
                      or it.get("path") == path), None)
        if match is None:
            raise BaiduOpenError(f"网盘中未找到: {path}")
        if match.get("isdir"):
            files = self._walk(match["path"])
            if not files:
                raise BaiduOpenError(f"{path} 下没有可下载的文件")
            root_dir = match["path"]
        else:
            files = [match]
            root_dir = root
        metas = self.filemetas([f["fs_id"] for f in files])
        by_id = {m["fs_id"]: m for m in metas}
        pairs = []
        for f in files:
            meta = by_id.get(f["fs_id"])
            if meta and meta.get("dlink"):
                pairs.append((f, meta, root_dir))
        return pairs

    def filemetas(self, fs_ids):
        out = []
        for i in range(0, len(fs_ids), 100):  # 接口单次最多 100 个
            data = self._rest("filemetas", {
                "fsids": json.dumps(fs_ids[i:i + 100]), "dlink": "1"})
            out.extend(data.get("list") or [])
        return out

    def _walk(self, dir_path):
        out = []
        for it in self.list(dir_path):
            if it.get("isdir"):
                out.extend(self._walk(it["path"]))
            else:
                out.append(it)
        return out

    # ------------------------------------------------------------ token

    def _access_token(self) -> str:
        if not self.config_path.exists():
            raise BaiduOpenError(
                "尚未登录百度网盘，请先执行: panacc baidu auth --app-key ... --app-secret ...")
        cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        if time.time() >= cfg.get("expires_at", 0) - 300:
            resp = requests.get(
                f"{AUTH_BASE}/token",
                params={"grant_type": "refresh_token",
                        "refresh_token": cfg["refresh_token"],
                        "client_id": cfg["app_key"]},
                timeout=30).json()
            if "access_token" not in resp:
                raise BaiduOpenError("刷新 access_token 失败，请重新执行 panacc baidu auth")
            cfg["access_token"] = resp["access_token"]
            cfg["refresh_token"] = resp.get("refresh_token", cfg["refresh_token"])
            cfg["expires_at"] = time.time() + int(resp.get("expires_in", 2592000))
            self.config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return cfg["access_token"]

    def _rest(self, method, params=None, _retry=0):
        p = {"method": method, "access_token": self._access_token(), **(params or {})}
        resp = requests.get(API_FILE, params=p,
                            headers={"User-Agent": DOWNLOAD_UA}, timeout=30)
        try:
            data = resp.json()
        except ValueError:
            raise BaiduOpenError(
                f"接口返回异常(HTTP {resp.status_code}): {resp.text[:200]}") from None
        errno = data.get("errno", 0)
        if errno == 0:
            return data
        if errno == 12 and _retry < 3:
            time.sleep(1.5 * (_retry + 1))
            return self._rest(method, params, _retry + 1)
        if errno in (111, -6):
            raise BaiduOpenError("access_token 已失效，请重新执行 panacc baidu auth")
        raise BaiduOpenError(f"接口错误 errno={errno} {ERRMSG.get(errno, '')}".strip())


def _norm_dir(p: str) -> str:
    p = (p or "/").replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    return p

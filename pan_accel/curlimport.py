"""解析浏览器「Copy as cURL」命令，提取下载直链与请求头。

兼容三种来源的复制格式：
- bash（Git Bash / WSL / macOS）：单引号、行尾 ``\\`` 续行
- PowerShell：行尾反引号 `` ` `` 续行
- Windows cmd：行尾 ``^`` 续行
"""
from __future__ import annotations

__all__ = ["parse_curl", "CurlParseError"]


class CurlParseError(ValueError):
    pass


def parse_curl(text: str):
    """返回 (url, headers, method)。method 为 None 表示未显式指定。"""
    tokens = _tokenize(_strip_continuations(text))
    url = None
    headers: list[str] = []
    method = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("curl", "curl.exe"):
            i += 1
        elif t in ("-H", "--header"):
            headers.append(_next(tokens, i, t))
            i += 2
        elif t.startswith("--header="):
            headers.append(t.split("=", 1)[1])
            i += 1
        elif t in ("-A", "--user-agent"):
            headers.append("User-Agent: " + _next(tokens, i, t))
            i += 2
        elif t in ("-b", "--cookie"):
            headers.append("Cookie: " + _next(tokens, i, t))
            i += 2
        elif t in ("-e", "--referer"):
            headers.append("Referer: " + _next(tokens, i, t))
            i += 2
        elif t in ("-X", "--request"):
            method = _next(tokens, i, t)
            i += 2
        elif t in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
                   "-F", "--form", "--output", "-o", "--url", "--retry",
                   "--connect-timeout", "--max-time", "-x", "--proxy", "-u", "--user"):
            _next(tokens, i, t)  # 跳过该选项的取值
            i += 2
        elif t.startswith("-"):
            i += 1  # -L/--compressed/-s/-k/-v 等无值开关，忽略
        else:
            if url is None:
                url = t
            i += 1
    return url, headers, method


def _next(tokens, i, opt):
    if i + 1 >= len(tokens):
        raise CurlParseError(f"选项 {opt} 缺少取值")
    return tokens[i + 1]


def _strip_continuations(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.rstrip()
        if s[-1:] in ("\\", "`", "^"):
            s = s[:-1]
        lines.append(s)
    return "\n".join(lines)


def _tokenize(s: str) -> list[str]:
    tokens = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
        elif c == "'":
            i += 1
            buf = []
            while True:
                j = s.find("'", i)
                if j < 0:
                    raise CurlParseError("单引号未闭合")
                buf.append(s[i:j])
                i = j + 1
                # bash 的 '\'' 拼接写法
                if s[i:i + 2] == "\\'":
                    buf.append("'")
                    i += 2
                else:
                    break
            tokens.append("".join(buf))
        elif c == '"':
            i += 1
            buf = []
            closed = False
            while i < n:
                c = s[i]
                if c == '"':
                    i += 1
                    closed = True
                    break
                if c == "\\" and i + 1 < n and s[i + 1] in '"\\`$':
                    buf.append(s[i + 1])
                    i += 2
                else:
                    buf.append(c)
                    i += 1
            if not closed:
                raise CurlParseError("双引号未闭合")
            tokens.append("".join(buf))
        else:
            j = i
            while j < n and not s[j].isspace():
                j += 1
            tokens.append(s[i:j])
            i = j
    return tokens

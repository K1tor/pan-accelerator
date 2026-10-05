"""直链来源插件包。

provider 的职责只有一个：把"网盘路径/分享"解析成可直接 GET 的下载直链
（dlink），交给 MultiThreadDownloader 下载。
"""

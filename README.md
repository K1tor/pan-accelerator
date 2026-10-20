# pan-accelerator 网盘下载加速器

把你在**百度网盘 / 夸克网盘 / 迅雷**（以及任何网站）中**有权下载的文件**下载得更快：

- HTTP 直链：多线程分块下载、断点续传、MD5 校验，一条命令；
- BT / 磁力链：`panacc bt`（aria2 引擎），支持 DHT/Tracker/PEX、选文件下载、断点续传；
- 迅雷 `thunder://` 链接：`panacc dl` 自动解码后高速下载。

## ⚠️ 先读：这个工具做什么、不做什么

| 做的事 | 不做的事 |
|---|---|
| 多线程分块下载直链，吃满你自己的带宽 | ❌ 伪造客户端身份 |
| BT / 磁力链下载（aria2 开源引擎） | ❌ 调用逆向的私有接口 |
| 断点续传（分块级状态持久化） | ❌ 绕过会员限速的任何手段 |
| MD5 / SHA1 校验 | ❌ 破解、爬取他人资源 |
| 接入百度网盘**官方开放平台 API** | ❌ 迅雷 P2SP「高速通道」等专有加速 |

**加速能力从哪来**：很多网盘 CDN 按"每个连接"限速，多连接并发就能提升总吞吐；但如果服务端按"账号/会话"限速（例如夸克非会员单文件限速），多线程无法突破——这是服务端策略，任何工具都一样。**百度网盘的正确长期方案是走官方开放平台 API（见方式二）**。

使用本项目即表示你同意：仅下载你拥有合法权限的文件，遵守网盘服务条款，控制并发礼貌下载，风险自担。

## 安装

需要 Python 3.9+。

```bash
cd pan-accelerator
pip install -e .          # 安装 panacc 命令
# 或者不安装直接用: python -m pan_accel.cli --help
```

BT / 磁力链功能还需要 aria2c（任选其一）：

```bash
winget install aria2      # Windows
scoop install aria2
# 或从 https://github.com/aria2/aria2/releases 下载解压，
# 把 aria2c.exe 放到 ~/.panacc/bin/（panacc 会自动找到）
```

## 图形界面（Material 3 Expressive）

除了命令行，项目还带一个 **Android 17 风格**的桌面界面（tkinter 自绘，**零额外依赖**）：

```bash
python -m pan_accel.gui            # 直接启动
python -m pan_accel.gui --light    # 浅色主题
python -m pan_accel.gui --seed "#7C5CD6"   # 指定种子色（动态取色）
python panacc-gui.py               # 免安装启动器
```

界面能力：

- **下载中心**：直链 / `thunder://` / BT 磁力链统一入口，任务队列，逐块进度热图，实时速度与剩余时间
- **统计页**：任务总数 / 进行中 / 完成 / 失败，累计进度与逐任务进度条
- **外观页**：5 组种子色实时生成 M3 色调板、圆角阶梯预览、色彩角色对照
- **设置页**：深色模式、完成后自动打开目录等开关

设计要点（`pan_accel/gui/`）：

| 文件 | 职责 |
|---|---|
| `tokens.py` | M3 tonal palette 生成、语义色彩角色、spring 缓动曲线、圆角阶梯 |
| `widgets.py` | 自绘控件：圆角卡片、涟漪按钮、开关、滑块、导航栏、输入框、进度环、波形图 |
| `tasks.py` | 任务状态模型（`Task` / `TaskState`）与滑动窗口测速 |
| `runner.py` | 引擎适配层：包装现有下载器采集逐块进度，不改动 CLI 行为 |
| `app.py` | 主窗口与四个页面 |

> 视觉参考 Material Design 3 Expressive：大圆角、tonal 表面、state layer、spring 动效；
> 配色由种子色按感知色调阶梯生成，明暗两套均通过 WCAG 对比度校验。

## 快速上手

### 方式一：浏览器直链导入（通用：夸克 / 百度 / 迅雷云盘 / 任意直链）

适合临时下载。思路：网页版网盘里触发下载 → 从浏览器开发者工具把真实下载请求复制出来 → 交给 panacc 高速下载。

1. 网页版登录网盘，点开文件下载，按 `F12` 打开开发者工具 → **Network（网络）** 面板；
2. 找到**对 CDN 域名的 GET 请求**（夸克是 `*.uc.cn` / `*.quark.cn` 的直链 GET；迅雷云盘是 `*.xunlei.com` 的下载 GET；注意都不是 `/file/download` 这类 POST 接口）。找不到时，可以右键直链新标签打开，对新出现的下载请求操作；
3. 右键该请求 → **Copy → Copy as cURL (bash)**；
4. 粘贴给 panacc：

```bash
panacc dl --curl "在此粘贴整段 curl 命令" -t 16
```

工具自动提取 URL、Cookie、UA、Referer 并开始多线程下载。直链过期（403）后重新复制一次再跑同一命令，**已下载的分块会自动复用**。

也可以手动指定：

```bash
panacc dl "https://..." --cookie "BAIDUID=...; STOKEN=..." --ua "netdisk;5.0" -t 16
```

### 方式二：百度网盘官方开放平台（推荐，长期稳定）

百度为开发者提供了官方 API，个人实名认证后即有下载配额，稳定且不违反任何条款。

1. 到 [百度网盘开放平台](https://pan.baidu.com/union) 注册开发者并完成认证，创建应用获得 **AppKey / SecretKey**（授权回调填 `oob`）；
2. 登录授权（浏览器打开授权页，粘贴授权码即可）：

```bash
panacc baidu auth --app-key 你的AppKey --app-secret 你的SecretKey
```

3. 列目录、下载（文件或整个目录，自动递归、保留目录结构）：

```bash
panacc baidu ls /
panacc baidu dl /资料/项目文档.pdf -o ./downloads -t 16
panacc baidu dl /资料            # 整个目录
```

> 说明：大文件时网盘端记录的 md5 可能按分块规则计算，校验不一致时会给出 ⚠ 警告但不删除文件。

### 方式三：BT / 磁力链（aria2 引擎）

```bash
panacc bt "magnet:?xt=urn:btih:..." -o ./downloads
panacc bt 某资源.torrent -o ./downloads
panacc bt "magnet:?..." --file "*.mp4"      # 只下载匹配的文件
panacc bt "magnet:?..." --list              # 先看看种子里有什么
panacc bt "magnet:?..." --seed-minutes 30   # 完成后做种 30 分钟（默认 0）
```

特性：DHT / Tracker / PEX / LPD 全自动；磁力链元数据通过 DHT 与 peers 获取；分块级断点续传（任务状态持久化在输出目录 `.panacc-aria2/` 下，重跑同一命令自动恢复）；`Ctrl+C` 随时中断。

### 方式四：迅雷 thunder:// 链接

```bash
panacc dl "thunder://QUFodHRw..."
# 输出: 已解码迅雷链接: http://... → 随后按普通直链多线程下载
```

`thunder://` 本质是 base64 包裹的 http(s)/ftp 直链，解码后直接进入方式一的多线程管线。注意：迅雷客户端的「高速通道」（P2SP 专有服务）无法也不应被模拟，普通直链的最终速度取决于源站。

### 方式五：任意普通直链

```bash
panacc dl "https://mirror.example.com/big.iso" -t 32
```

## 命令速查

`panacc dl`：

| 参数 | 说明 |
|---|---|
| `-o` | 输出文件路径或目录（默认自动命名） |
| `-t` | 并发线程数（默认 8，建议 8~32） |
| `--chunk-mb` | 分块大小 MB（默认 32，调小利于续传粒度） |
| `-H "Referer: ..."` | 附加请求头，可重复 |
| `--cookie` / `--ua` | 快捷设置 Cookie / User-Agent |
| `--curl` | 粘贴 Copy as cURL 命令自动解析 |
| `--expect-md5` / `--expect-sha1` | 下载完成后哈希校验，不符退出码 2 |
| `--insecure` | 跳过 TLS 证书校验 |

`panacc bt`：

| 参数 | 说明 |
|---|---|
| `-o` | 输出目录 |
| `--file "模式"` | 仅下载匹配文件（通配符，可重复） |
| `--list` | 仅列出种子内文件（.torrent 本地解析，magnet 需联网取元数据） |
| `--seed-minutes` | 完成后做种分钟数（默认 0，即完成即停） |
| `--metadata-timeout` | 磁力链元数据获取超时秒数（默认 300） |
| `--idle-timeout` | 无下载速度的容忍秒数（默认 600，超时报错可续传） |
| `--aria2c` | 指定 aria2c 可执行文件路径 |

## 工作原理

1. **探测**：发 `Range: bytes=0-0`，判断是否支持分块、取总大小、从 `Content-Disposition` 自动命名；
2. **分块**：按默认 32MB 切块，N 个工作线程从队列领块并发下载，直接写入预分配文件的对应偏移（无合并步骤）；
3. **续传**：每完成一块，立即把分块位图原子写入 `<文件>.panacc.state.json`；中断（Ctrl+C / 断网 / 进程被杀）后重跑同一命令自动续传，换新直链也能续（大小、分块布局一致即复用）；BT 任务由 aria2 session 持久化到输出目录 `.panacc-aria2/`；
4. **重试**：单块失败指数退避重试 5 次；403/410 视为直链过期，立即中止并提示；
5. **退化**：不支持 Range 的链接自动退化为单线程流式下载；
6. **BT**：磁力链分两段——先经 DHT/Tracker 从 peers 获取种子元数据（BEP-9），再下载真实数据；完成后默认不做种。

## 项目结构

```
pan-accelerator/
├── pan_accel/
│   ├── downloader.py       # HTTP 多线程分块下载核心（续传、重试、探测）
│   ├── bt.py               # BT/磁力链：aria2c JSON-RPC 驱动（选文件、续传、做种）
│   ├── thunder.py          # thunder:// 迅雷链接解码
│   ├── curlimport.py       # 「Copy as cURL」解析（bash/PowerShell/cmd 续行）
│   ├── verify.py           # MD5 / SHA1 校验
│   ├── cli.py              # panacc 命令行入口（dl / bt / baidu）
│   └── providers/
│       └── baidu_open.py   # 百度网盘官方开放平台（OAuth + xpan 接口）
├── tests/
│   ├── range_server.py     # 测试用：支持 Range 的限速 HTTP 服务器
│   ├── tracker.py          # 测试用：极简 HTTP tracker
│   ├── smoke.py            # HTTP 冒烟测试（python -m tests.smoke）
│   └── smoke_bt.py         # BT 冒烟测试（本机做种+tracker，python -m tests.smoke_bt）
├── pyproject.toml
└── README.md
```

## 扩展：接入其他网盘

provider 的职责只有一个——把"网盘路径"解析成可直接 GET 的 dlink。照 `providers/baidu_open.py` 的样子实现 `resolve()`，返回 `(文件条目, 元信息含 dlink, 根目录)` 即可接入现有的多线程下载与续传管线。夸克/迅雷网盘目前没有面向个人的官方开放 API，如需集成请先确认对方是否提供了合法接口。

## 测试

```bash
python -m tests.smoke       # HTTP 多线程/续传/cURL 解析（本地限速服务器）
python -m tests.smoke_bt    # BT/磁力链全流程（本机做种 + 本机 tracker + webseed）
```

BT 测试完全自包含：本地 HTTP 服务器提供 webseed → 做种端先下载获得完整数据 → 本机 tracker 牵线 → 验证 magnet 元数据获取、多文件下载、--file 选择、--list。测试无需外网。

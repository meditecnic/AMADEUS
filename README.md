# AMADEUS

**一个可以在自己电脑上和牧濑红莉栖说话的桌面应用。**

她使用日语回答，屏幕上同时有中文字幕。她会记住你们聊过的事，能看得见，也能让她忘掉。（Amadeus 的人格设定基本上就是参考官方小说和相关资料）本项目共有两条世界线：**STEINS;GATE** 和 **β**。两边的对话、记忆互不相通，界面也各是各的样子。（sg 线相当于是架空的，在现实中红莉栖还活着的情况下，诞生的 Amadeus；加了些设定，你在 sg 线提到真实红莉栖，Amadeus 还会争风吃醋。）

这是一个非官方、非商业的粉丝作品，和官方没有任何关系，仓库里也不包含任何原作素材。 · [English](#english) · [日本語](#日本語)

| | |
|---|---|
| ![SG worldline chat](assets/screenshots/sg-chat.png) | ![β worldline chat](assets/screenshots/beta-chat.png) |
| ![Worldline shift transition](assets/screenshots/worldline-shift.png) | ![Memory star map](assets/screenshots/memory-star-map.png) |

*上面的截图是在没放原作素材包的情况下截的，所以舞台上是自制的占位画面（世界线跃迁那张是整合进来的 WebGL2 过渡演出，记忆星图里展示的是测试数据）。目前除了登录页，界面正在整体重做，等这轮做完会换上新截图。*

## 现在做到哪了

从 6 月中旬算起，已经开发了大约五个月。

**Windows 安装包已经打出来了**，之前在一台干净的英文 Windows Server 2022 虚拟机上跑过了完整的 10 项验收（安装、自动装 WebView2、真实对话、记忆的写入/召回/遗忘、重启后依然记得、卸载时保留数据），现在每次打包也都会在 CI 的干净 Windows 环境里自动跑一轮安装和启动检查。v0.1.0 正在做最后的准备，还没有公开发布。（打包脚本在 `packaging/windows/`，冻结后端是构建产物，不进仓库。）

目前已经能用的功能和特性：

- **日语对话 + 逐句中文字幕**：她的回复按句流式返回，每一句日语都配好对应的中文字幕；就算某句翻译慢一点也不会卡住正文，万一漏翻也会明确标出来，不会悄悄丢掉。
- **存在本机的长期记忆**：对话里提取出来的事实和经历都存在你本机的 SQLite 数据库里（写入时会用 768 维的 `jina-embeddings-v5-text-nano-retrieval` 建好向量索引）。事实分两类：「常驻事实」每一轮对话都会放进提示词；「按需事实」则是在需要用到时，由模型主动调用 `recall_memory` 工具（目前仅 DeepSeek 支持）去回忆，另外和当前这句话匹配上的事实、以及相关的过往经历，也会在组装提示词时一起带上。她记住了什么随时能在档案页里查看、修改或删除，过往经历也能查看和删除；清空某段对话时，可以顺便把那段对话独有的长期记忆一起抹掉，还可以在记忆星图里浏览。
- **两条互相独立的世界线**：**STEINS;GATE** 和 **β** 各自有独立的人格、会话、记忆和界面主题，两边的数据完全分开保存。
- **世界线跃迁**：在右上角「**系统 → 世界线**」里就能切换——仪表盘显示当前所在的世界线，图里可以预览选中的目标线，按下跃迁按钮就会开始切换，两边各自的会话和输入框草稿都会原样保留。如果不想等动画，演出途中点一下鼠标或者按 Esc 就能跳过动画（背后仍然会等目标线确认就绪）。
- **接入你自己的大模型**：支持 DeepSeek、智谱 GLM，或任何兼容 OpenAI 接口的服务（包括本地跑的模型）。可选接入 Tavily 或 Firecrawl 做联网搜索。
- **可选的红莉栖语音**：通过本机的 GPT-SoVITS 服务把她的回答念出来（见下文「[语音](#语音可选)」）；基于 sherpa-onnx 的 SenseVoice 语音识别后端也已经写好了（前端麦克风界面还没做）。
- **默认全在本机运行**：所有本地服务都只监听本机回环地址（loopback），WebSocket 连接会校验 Origin；安装版把 API 密钥存在系统的 Windows 凭据管理器里，源码运行默认只放在内存里。你的对话内容只会发给你自己在设置里配好的大模型服务；如果打开了联网搜索，搜索词才会发给你选的搜索服务（Tavily 或 Firecrawl）。

目前除了登录页以外的界面正在整体重做，上面的截图等这轮重做完再换。英文和日文界面也在计划中——不管切换到哪种界面，她都还是说日语；英文界面下配英文字幕，日文界面下就不再显示字幕了。

## 架构与技术细节

如果你好奇它底下是怎么跑起来的，整体结构大概是这样：

```mermaid
flowchart LR
  subgraph Desktop["桌面端 (Tauri 2)"]
    UI["React 19 + TypeScript UI"]
  end
  subgraph Backend["Python 后端 (FastAPI, 本机回环, 系统分配端口)"]
    WS["/ws/chat 流式对话"]
    Persona["人格 + 提示词编译"]
    Mem["记忆: SQLite + jina"]
    Tr["字幕翻译"]
    TTS["TTS 队列"]
  end
  LLM["大模型服务<br/>(DeepSeek / GLM / OpenAI 兼容)"]
  Sovits["GPT-SoVITS (可选)"]
  UI <--> WS
  WS --> Persona --> LLM
  Mem -- "常驻事实 + 过往经历" --> Persona
  WS --> Tr --> LLM
  WS --> TTS --> Sovits
```

**一轮对话是怎么走的**：你在界面上发出一句话，前端通过 WebSocket 传给后端；后端把当前世界线和身份下的「常驻事实」与「相关过往经历」取出来，编译进红莉栖的人格提示词，然后流式请求模型的日语回复。如果用的是 DeepSeek，模型判断需要查旧事时会调 `recall_memory` 把按需事实捞出来，再走第二轮生成。每一句日语只要一生成完就会立刻推给前端显示，对应的中文字幕和（可选的）语音则各自在独立有序的轨道上跟进——这样就算某句翻译慢了半拍，也不会拖慢她的日语正文，字幕可以在本轮结束后再补齐。

顺便记几条工程上的实测与打包细节：

- **记忆召回实测**：在连上真实模型（`deepseek-flash`）的受控端到端测试里，12 道需要用到已存事实的提问全部成功调取了记忆，4 道不需要查记忆的对照题也全部没有误调工具（这个结果仅代表当时那套题集和测试环境）。
- **Windows 安装包与自动化检查**：Tauri 外壳把打包好的 Python 后端（PyInstaller 冻结，内置嵌入模型缓存）一起封进 NSIS 安装程序。启动时由外壳在系统分配的本机回环端口拉起后端，每个数据目录只允许开一个实例，退出窗口时把后端进程也一起关干净。除了前面提到的 Windows Server 2022 虚拟机 10 项人工验收之外，现在每次打包 CI（`.github/workflows/release.yml`、`packaging/windows/`）都会自动跑一轮完整检查：先确认冻结后端在没设 `AMADEUS_DATA_DIR` 时拒绝启动，跑 `--self-check` 和 `smoke_frozen.py`，再在一台干净的 Windows runner 上跑 `install_smoke.py`——覆盖静默安装（检查 HKCU 卸载注册表项和开始菜单快捷方式）、进登录页并连上本地无密钥 `OpenAI-compatible` 端点达到 `ONLINE` 且控制台零报错、二次启动单实例锁拦截、关闭窗口后桌面端/后端/WebView2 整棵进程树完全退出、通过 Windows Filtering Platform 和 CDP 检查没有任何外部网络连接，以及静默卸载后完整保留用户数据。打 tag 或手动发版时，在创建草稿 Release 之前，还会用 `verify_update_signature.py` 对照 `desktop/src-tauri/tauri.conf.json` 里的公钥，校验 `.sig` 更新签名和 `latest.json`。
- **平时是怎么开发的**：这个项目由我一个人维护，产品方向、架构取舍和最终验收都是我自己在管。具体代码实现大部分是由 AI 编程代理拆成小批次写出来的，合并前通常会叫另一个代理对照规格做一轮审查；代码改动要过测试套件，界面改动要看实机截图，安装包改动则上干净的 Windows 环境跑通。

## 安装与常见问题

桌面版目前只支持 **Windows 10/11 (64 位)**。等 v0.1.0 发布后，可以直接从 [GitHub Releases](https://github.com/meditecnic/AMADEUS/releases) 下载安装包。

下面整理了几个安装和使用时可能会遇到的小问题：

- **Windows SmartScreen 弹窗提示「未知发布者」**：个人项目还没有购买 Authenticode 代码签名证书，所以 Windows 会弹窗提醒。只要你确认安装包是从本仓库的 GitHub Releases 下载的，并且按下面一条核对过 SHA-256 校验和没问题，点「**更多信息 → 仍要运行**」就可以安心安装了。（补充一句：自动更新包里带的 Tauri 签名是用来确认更新包来源的，不能代替 Windows 的代码签名。）
- **怎么核对下载的安装包有没有损坏（`SHA256SUMS.txt`）**：发布页会和安装包一起提供一份 `SHA256SUMS.txt`。下载后可以在 PowerShell 里运行：
  `Get-FileHash .\Amadeus_<版本>_x64-setup.exe -Algorithm SHA256`
  把算出来的 `Hash` 和 `SHA256SUMS.txt` 里对应文件那行的数值比对一下（不区分大小写），完全一致再安装。同版本的其他发布附件也可以用同一条命令校验。
- **安装时需要联网吗（WebView2）**：安装程序里内置了微软的 WebView2 在线引导程序（`embedBootstrapper`）。如果你的电脑上还没有装过 Microsoft Edge WebView2 Runtime，安装程序会自动联网下载安装，这时需要保持网络畅通；已经装过的话就不需要了。
- **日志文件放在哪**：安装版运行时的后端日志（`backend-stdout.log`、`backend-stderr.log`，以及启动失败时的 `backend-startup.log`）默认写在 `%LOCALAPPDATA%\com.amadeus.desktop\logs`；如果你设置了环境变量 `AMADEUS_DATA_DIR`，日志就会写在 `<AMADEUS_DATA_DIR>\logs`。
- **遇到 Bug 想提 Issue**：非常欢迎来 [GitHub Issues](https://github.com/meditecnic/AMADEUS/issues) 反馈！不过贴日志或者截图前一定记得检查一眼，**千万不要把自己的 API 密钥、令牌或者私人聊天内容贴出来**。

## 本地开发运行

如果你想把源码拉下来自己跑，或者改改前端看看效果，有两种运行方式：平时只看界面可以用默认的「**本地 mock**」，想跟她真聊就起「**真实后端**」。两种方式都是在浏览器里打开 http://localhost:1420 （浏览器开发模式在 Linux 上也能跑）。

```powershell
git clone https://github.com/meditecnic/AMADEUS.git
cd AMADEUS
```

### 1. 先看看界面（默认本地 mock）

只需要装好 **Node.js 22.13+**（20.x 分支用 20.19+ 也可以），不用配 Python 后端，也不用准备 API 密钥、大模型或语音服务。

在仓库根目录下打开 PowerShell：

```powershell
cd desktop
npm ci
# 如果之前在同一终端里设过连真实后端的环境变量，先清掉
Remove-Item Env:AMADEUS_DEV_MOCK -ErrorAction SilentlyContinue
npm run dev -- --port 1420 --strictPort
```

打开 http://localhost:1420/?scenario=streaming ，等界面上的 USER ID 自动输入完 `Salieri` 后，用公开的本地测试口令 **`Gott würfelt nicht`** 登录。（算是原作的一个小彩蛋吧，后续我也会做些调整）

随便发一句话，就能收到三组预设好的、逐句流式出来的日语回复和中文字幕。这里的历史会话和记忆用的都是合成测试数据，你在界面里做的修改只留在开发服务器的内存里，服务重启就会还原；浏览器端则会记住你的界面偏好和当前选中的测试场景。

在底层，这个 mock 是直接挂在同一个 1420 端口上拦截处理 `/api`、`/ws/chat`（以及 `/ws`），不会去连任何后端、模型或语音服务。它只是一个**仅限开发阶段使用**的 Vite 插件（`apply: 'serve'`），mock 服务和场景切换脚本在打生产包时会被完全剔除，跑 `npm run preview` 时也不会生效。

如果想看登录失败、空状态、离线、记忆档案等其他预设状态，可以翻看 [场景目录与切换说明](desktop/src/dev-mock/README.md)、[场景 JSON 文件](desktop/src/dev-mock/scenarios/) 和 [浏览器走查说明](desktop/validation/README.md)。

### 2. 接入模型真聊（真实后端）

想跟她进行真实对话，除了 Node.js 之外，还需要安装 **Python 3.11** 并准备好大模型服务的 API 密钥。切换前记得先把上面的 mock 开发服务器停掉。

在 Windows 上创建 Python 虚拟环境之前，建议先把下面两个前置依赖装好：

1. **Microsoft Visual C++ 2015–2022 Redistributable (x64)**（[微软下载页](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)，安装包直链 [`vc_redist.x64.exe`](https://aka.ms/vs/17/release/vc_redist.x64.exe)）。在一台全新的 Windows 上如果缺了 `vcruntime140_1.dll`，平时 `pip install` 和启动后端看着都不会报错，但等到第一次写入记忆、后台加载 `torch` 的时候就会抛出 `WinError 1114`（`c10.dll` 初始化失败）。
2. **Python 3.11**（[Python 3.11.9 发布页](https://www.python.org/downloads/release/python-3119/)，64 位 Windows 安装包 [`python-3.11.9-amd64.exe`](https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe)——3.11.9 是 python.org 官方提供 Windows 图形安装包的最后一个 3.11 小版本）。

先在 `backend/` 目录下建好 `.venv` 虚拟环境并装好依赖（首次运行前做一次就行）：

```powershell
cd backend
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..
```

**推荐启动方式（一条命令同时拉起前后端）**：装好依赖后，在 `desktop/` 下运行 `npm run dev:real`（仓库根目录下的 `run_dev.bat` 也是调用同一个启动器）。启动器会在内存里生成一枚全新的本地 API 令牌，分别传给后端和 Vite（绝对不会通过 `VITE_*` 环境变量暴露进前端包），任意一边退出或者按 Ctrl+C 时，两个进程会一起干净停掉：

```powershell
# 在仓库根目录下执行；先给开发模式指定一个独立的数据目录
$env:AMADEUS_DATA_DIR = Join-Path $env:LOCALAPPDATA 'amadeus-dev-data'
Remove-Item Env:AMADEUS_DB_PATH -ErrorAction SilentlyContinue
cd desktop
npm ci
npm run dev:real
```

补充几个启动器的小细节：可以通过 `AMADEUS_PYTHON` 指定 Python 解释器路径（Windows 默认找 `backend/.venv/Scripts/python.exe`，Linux/macOS 默认找 `backend/.venv/bin/python`）。在 Linux/macOS 上跑时，同样先把 `AMADEUS_DATA_DIR` 设到一个独立的开发目录、`unset AMADEUS_DB_PATH`，然后进 `desktop/` 跑 `npm run dev:real`。启动器如果发现数据目录为空会直接拒绝启动，并且会自动清掉旧版的 `AMADEUS_DB_PATH` 覆盖变量。后端端口默认自动挑一个空闲端口（`BACKEND_PORT` 未设置或设为 `0`）；如果你想固定端口，可以把 `BACKEND_PORT` 设为 1–65535，端口被占用时启动会直接报错退出。

**备选启动方式（手动开两个终端，不启用本地 API 认证）**：如果你习惯把前后端拆在两个终端里自己看日志，保持 `AMADEUS_LOCAL_API_TOKEN` 和 `AMADEUS_DEV_LOCAL_API_TOKEN` 不设置即可（此时后端启动会打一条 `WARNING`，提示当前 HTTP 和 WS 未开启本地令牌认证、仅限开发调试使用）。

终端 1，在仓库根目录下（PowerShell——建议显式用 `py -3.11`，免得不小心用到系统 `PATH` 里的其他 Python 版本）：

```powershell
cd backend
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 请使用 TEMP 以外的独立开发目录（TEMP 可能会被系统清理），也千万别指向正式安装版的数据目录
$env:AMADEUS_DATA_DIR = Join-Path $env:LOCALAPPDATA 'amadeus-dev-data'
Remove-Item Env:AMADEUS_DB_PATH -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

终端 2，在仓库根目录下（PowerShell——如果前面跑本地 mock 时已经执行过 `npm ci`，这里可以跳过）：

```powershell
cd desktop
npm ci
$env:AMADEUS_DEV_MOCK = "0"
npm run dev -- --port 1420 --strictPort
```

这里设 `AMADEUS_DEV_MOCK=0` 就会关掉本地 mock，恢复 Vite 原有的代理，把 `/api`、`/ws` 和 `/health` 转发到 `http://127.0.0.1:8000`（可以通过 `AMADEUS_BACKEND_ORIGIN` 改后端地址）。前端通过同源的 `POST /__amadeus/bootstrap` 拿内存令牌（双终端手动模式下拿到的是空令牌；正式打包的安装版则是通过主窗口的 Tauri 命令获取令牌）。在 Linux/macOS 上，后端用 `python3.11 -m venv .venv` 和 `.venv/bin/python`，前端跑 `AMADEUS_DEV_MOCK=0 npm run dev -- --port 1420 --strictPort`。

Linux/macOS 手动起后端前，先在那个终端里配好独立的开发数据目录：

```sh
export AMADEUS_DATA_DIR="$(mktemp -d -t amadeus-dev.XXXXXXXX)"
unset AMADEUS_DB_PATH
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

需要注意：源码运行（包括直接跑脚本）只要没设非空的 `AMADEUS_DATA_DIR`，就会直接拒绝解析数据目录并报错退出：`RuntimeError: AMADEUS_DATA_DIR required; set it to an explicit data directory`（退出码 1）。只有在已经设好 `AMADEUS_DATA_DIR` 的前提下，`AMADEUS_DB_PATH` 才会作为数据库文件路径的覆盖项生效；只设 `AMADEUS_DB_PATH` 是不能绕过这项检查的。`run_dev.bat` 和 `npm run dev:real` 在拉起进程前都会先清掉旧的 `AMADEUS_DB_PATH` 并检查 `AMADEUS_DATA_DIR`。另外，如果自己直接用 `uvicorn --reload` 启动，当子进程因为没设目录拒绝启动时，外层的 reload watcher 可能会一直挂着不退。离线快照构建脚本和演练 CLI 自带显式路径隔离，不需要设这个变量。

服务跑起来之后，打开 http://localhost:1420 ，用 `Gott würfelt nicht` 登录（真实后端会自动忽略大小写，并把 `ü` / `ue` 归一成 `u`，所以输 `gott wurfelt nicht` 或 `Gott wuerfelt nicht` 也能进）。登录进去后，点右上角的「**系统 → 连接**」，列表里默认已经有一条 `OpenAI-compatible · http://127.0.0.1:1234/v1 · local-model` 的预设（方便连本地兼容服务）；填好你自己的大模型连接配置和 API 密钥并保存，等连接测试通过，就可以开始和她说话了。

第一次写入记忆时，后端会自动下载并加载记忆嵌入模型（`jinaai/jina-embeddings-v5-text-nano-retrieval`，fp32，768 维，约 866 MB，Jina AI CC BY-NC 4.0 许可，固定 revision `ac5d898c8d382b17167c33e5c8af644a3519b47d`），旧版 E5 索引会在启动时自动重建（就算模型加载失败，对话也能继续用词法检索顶上，不会卡死聊天）。如果没配语音包，后端日志里打出的 `[WARN] TTS reference audio not found ...` 是正常现象，语音本身就是可选项（见下文「[语音](#语音可选)」）。

如果你想在启动前先把固定版本的记忆模型（连同带许可证声明的模型卡）提前下载到本地缓存：

```sh
hf download jinaai/jina-embeddings-v5-text-nano-retrieval --revision ac5d898c8d382b17167c33e5c8af644a3519b47d --include onnx/model.onnx onnx/model.onnx_data tokenizer.json README.md
```

顺带一提：桌面独立窗口是由打包出来的安装程序提供的（打包脚本在 `packaging/windows/`，冻结后端是构建产物，不进代码仓库）；在源码检出目录下直接跑 `npm run tauri -- dev` 是起不来的（因为外壳期望直接调用打包好的冻结后端），所以平时本地开发直接用浏览器开 http://localhost:1420 就好。

### 3. 数据、日志与密钥存在哪

- **安装版数据目录**：Windows 安装版默认把数据存在 `%LOCALAPPDATA%\com.amadeus.desktop\data`。卸载应用时默认会帮你保留数据；只有在卸载向导里主动勾选了删除应用数据，才会把这个默认目录删掉。如果想换个磁盘或目录存放，可以设置系统的用户环境变量 `AMADEUS_DATA_DIR` 后重启应用。
- **源码与独立后端必填目录**：源码运行以及单独直接运行 `amadeus-backend.exe` 时，都必须显式设置非空的 `AMADEUS_DATA_DIR`，没有默认回退目录，没设就会拒绝启动。
- **数据目录丢失或数据库异常时的保护**：如果配置的数据目录找不到或无效，程序启动时会直接停下提示，不会在原处新建一个空数据库把问题盖过去。如果已有的数据库在启动自检（`PRAGMA quick_check`）时没有通过，程序同样不会新建空库覆盖它，而是会进入「数据异常」状态：暂停对话等功能，把原本的数据库文件原封不动地留着。另外，如果在版本升级过程中发生数据迁移失败，程序会先自动退回到升级前的备份。
- **日志位置**：桌面端拉起后端时的日志文件（`backend-stdout.log`、`backend-stderr.log`，以及启动失败时的 `backend-startup.log`）默认写在 `%LOCALAPPDATA%\com.amadeus.desktop\logs`；如果设置了 `AMADEUS_DATA_DIR`，则会写在 `<AMADEUS_DATA_DIR>\logs`。
- **API 密钥存在哪**：Windows 安装版会把你填的 API 密钥安全地存进 Windows 凭据管理器；源码运行默认只保存在内存里，每次重启后重新填一下就行。如果你在 Windows 上跑源码开发时也想持久化保存密钥，可以设置环境变量 `AMADEUS_CREDENTIAL_BACKEND=windows`（注意这会直接读写你本机真实的 Windows 凭据）。其他操作系统下只保存在内存里。本地 mock 模式不会读写这些后端存储。

## 原作素材包

本仓库**不包含**任何原作素材（比如角色立绘、CG、Logo、BGM）。没有素材包也能正常使用，界面会自动显示我们自制的占位画面。（版权问题，目前的解法就只能是素材包了）

如果你手头有整理好的素材包，把它放到数据目录下的 `asset-pack/amadeus-original/`（保证根目录下有 `manifest.json`），然后重启后端即可。素材包单独分发并附有它自己的说明，里面只放来自原作的素材；而应用自己生成的底纹和插画则直接随本仓库发布，具体来源都登记在 [assets/PROVENANCE.md](assets/PROVENANCE.md) 里。

## 语音（可选）

语音功能完全是可选的，不配语音的话，她的台词就会直接以文字和字幕的形式呈现。

- **语音合成（TTS）**：通过本机运行的 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 服务完成，推荐搭配的声音模型是 Hugging Face 上 bysq 训练的 [TTS-KurisuMakise](https://huggingface.co/bysq/TTS-KurisuMakise)（GPT-SoVITS v2pro，Apache-2.0）。本仓库不附带、不下载、也不转发模型权重和参考音频，需要你自己准备。把你自己的参考音频片段放进数据目录下的 `voice-pack/ref/`（包含 `ref_neutral.wav`、`ref_happy.wav`、`ref_sad.wav` 等共 9 种情绪，完整列表见 `backend/app/config.py`，也可以用 `AMADEUS_REF_*` 环境变量逐个指定路径），在本机启动 GPT-SoVITS API 服务（默认地址 `http://127.0.0.1:9880`，可用 `SOVITS_URL` 覆盖），并把 `SOVITS_GPT_WEIGHTS` 和 `SOVITS_SOVITS_WEIGHTS` 指向对应的权重文件即可。
- **语音识别（ASR）**：后端已经接好了（其实前端麦克风页面还没做，哈哈）。第一次调用语音识别时，会自动从 [sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) 下载 SenseVoice int8 模型（约 163 MB）和 Silero VAD（约 644 KB），校验固定的 SHA-256 哈希后保存在数据目录下的 `models/speech/` 里；你也可以通过 `SENSEVOICE_MODEL`、`SENSEVOICE_TOKENS` 和 `AMADEUS_VAD_MODEL` 环境变量直接指向本地已有的模型文件。

**关于声音的版权说明**：TTS-KurisuMakise 这个模型模仿的是《命运石之门》里牧濑红莉栖的声音。角色本身、原作录音以及声优的表演均归各自的权利人所有。模型页面上标注的 Apache-2.0 许可只覆盖上传者自己有权授权的部分，并不代表授予了角色、原作录音或声优表演的任何权利。AMADEUS 不会内置、下载或分发该模型及任何参考音频。请仅在个人、非商业场景下使用，不要用它去冒充声优本人，也不要制作让人误以为是官方出品的内容。权利人如果希望移除相关引用或内容，请在 GitHub 上提一个 issue，我们会第一时间处理。

## 许可与声明

本仓库的代码采用 [MIT 许可](LICENSE) 发布。连接设置页上的模型供应商图标来自 [Lobe Icons](https://github.com/lobehub/lobe-icons)（MIT），各供应商的名称与商标归各自所有者所有。由于打包好的安装程序内置了采用 CC BY-NC 4.0 许可的 Jina AI 嵌入模型，因此**安装包整体仅限非商业使用**。语音相关组件包含 eSpeak NG（GPLv3），对应的源码包 `amadeus-0.1.0-speech-sources.zip` 会随每个版本在 GitHub Releases 一并提供，详情请看 [第三方说明](THIRD_PARTY_NOTICES.md)。

STEINS;GATE、STEINS;GATE 0 及其角色、名称与原作素材均归 MAGES. Inc. 及各自权利人所有。本仓库不包含这些原作素材，项目也不用于任何商业用途；权利人如有任何要求，我们会尽快配合处理。

## 个人的碎碎念吧

因为一直是个人开发，中途的困难挫折也有很多，目前最棘手的应该就是版权问题了。之前说尽量在 1 个月左右推送首个 MVP 安装包——安装包已经打好、过了工程验收，等我自己完整体验一轮就放出来。

okeydokey，大概就是这些，那么来句帅气的收尾吧：El Psy Kongroo.

---

## English

**A local-first AI companion desktop app.** Users talk with Amadeus Kurisu, who answers in Japanese with a paired subtitle for every line. Her long-term memory is stored on the user's own machine, where it can be inspected and selectively forgotten.

*Unofficial, non-commercial fan project based on STEINS;GATE. No original game assets are included.*

### What it does

- **Japanese conversation with paired Chinese subtitles.** Replies stream sentence by sentence; each Japanese sentence is matched with its translation, and missing translations are marked instead of silently dropped.
- **Long-term memory that stays local.** Facts and experiences from your conversations are stored in SQLite (with 768-dimensional `jina-embeddings-v5-text-nano-retrieval` embeddings indexed on write). Facts are split into resident facts, which go into the prompt every turn, and on-demand facts: the model fetches them with the `recall_memory` tool (DeepSeek only) when a turn needs them, and facts that match the current message are added to that turn's prompt. Related past experiences are also added when the prompt is compiled. In the memory archive you can view, correct and delete stored facts and view and delete experiences; you can also optionally erase a conversation's exclusive long-term memories when clearing a chat, and browse memories on the star map.
- **Two isolated worldlines.** *STEINS;GATE* and *β* each have their own persona, conversations, memory and visual theme. Conversations and memories are stored separately per worldline.
- **Worldline transitions.** Switch between worldlines in **系统 → 世界线**: the dial shows the current worldline, the diagram previews the selected target, and the shift button starts the transition while preserving each worldline's conversations and draft. Click or press Esc to skip the transition animation while still waiting for target confirmation.
- **Bring your own model.** DeepSeek, Zhipu GLM, or any OpenAI-compatible endpoint. Optional web search through Tavily or Firecrawl.
- **Optional voice.** Text-to-speech through a local GPT-SoVITS server; speech recognition with SenseVoice via sherpa-onnx (backend only for now, no microphone UI yet).
- **Local by design.** Every service binds to loopback, WebSocket connections are origin-checked, and API keys are stored in the Windows Credential Manager in installed builds or in memory by default in source runs. Conversation text is sent only to the model provider the user configures; if web search is enabled, search queries also go to the chosen search provider (Tavily or Firecrawl).

### Architecture

One chat turn: the UI sends the user's message over a WebSocket; the backend loads the resident facts and related past experiences for the active worldline and identity, compiles them into the persona prompt, and streams the model's Japanese reply. With DeepSeek, the model can call `recall_memory` to fetch on-demand facts, which adds a second model pass. Each sentence is published as soon as it is complete; its subtitle and (optional) speech follow on their own ordered tracks, so a slow translation never holds back the text and may arrive after the turn has finished.

### Engineering notes

- **Measured recall behaviour.** In a controlled end-to-end run against a real model (`deepseek-flash`), 12 of 12 prompts that needed a stored fact used it, and 4 of 4 control prompts did not call the tool. That result covers that question set and environment only.
- **Windows installer.** The Tauri shell bundles the backend (PyInstaller, with the embedding model cache) into an NSIS installer, starts it on a system-assigned loopback port on launch, allows only one instance per data directory, and shuts it down cleanly on exit. Alongside the earlier 10-item acceptance pass on a clean English Windows Server 2022 VM (install, WebView2 bootstrap, real chat, memory write / recall / forget, restart persistence, and an uninstall that keeps user data), every packaging run in CI (`.github/workflows/release.yml`, `packaging/windows/`) checks that the frozen backend refuses to start without `AMADEUS_DATA_DIR`, runs `--self-check` and `smoke_frozen.py`, and runs `install_smoke.py` on a clean Windows runner: silent install (HKCU uninstall registration and Start Menu shortcut), login page and local keyless `OpenAI-compatible` provider reaching `ONLINE` with zero console errors, single-instance lock on a second launch, full desktop/backend/WebView2 process-tree exit on window close, Windows Filtering Platform and CDP checks for no external network connections, and silent uninstall that retains user data. For tagged and manual release builds, `verify_update_signature.py` verifies the `.sig` update signature and `latest.json` against the updater public key in `desktop/src-tauri/tauri.conf.json` before creating a draft Release. No release is published yet; the frozen backend is a build artifact excluded from the repository.

### How it was built

Built and maintained by one developer, who owns the product direction, architecture decisions and acceptance testing. Most of the implementation was written by AI coding agents in small batches. A second agent reviews most batches against their specification before merging; code changes are gated on the test suite, UI changes are checked with screenshots, and installer changes on a clean Windows VM.

### Status

- **v0.1.0 is being prepared.** There is no public release yet.
- **The UI is being reworked across the app** (except the login screen); screenshots will be updated after the rework.
- The desktop app supports **Windows 10/11 x64 only**; the browser dev mode also runs on Linux.
- **Planned:** English and Japanese UI. Kurisu keeps speaking Japanese in every mode; in English mode her lines get English subtitles, and in Japanese mode subtitles are turned off.

### Installation

Once v0.1.0 is published, download the Windows installer from [GitHub Releases](https://github.com/meditecnic/AMADEUS/releases). The installer is unsigned, so Windows SmartScreen will warn; choose **More info → Run anyway** to continue. If WebView2 is missing, the installer downloads and installs it, which requires an internet connection. Voice is optional.

### Troubleshooting

- **Windows SmartScreen warning.** The installer is not Authenticode-signed. When SmartScreen warns about an unknown publisher, verify that the download came from this repository's GitHub Releases and that its SHA-256 matches `SHA256SUMS.txt`, then choose **More info → Run anyway**. (The Tauri update signature verifies update origin and does not replace Windows code signing.)
- **Verify downloads (`SHA256SUMS.txt`).** Download `SHA256SUMS.txt` alongside the installer and run `Get-FileHash .\Amadeus_<version>_x64-setup.exe -Algorithm SHA256` in PowerShell. Compare `Hash` (case-insensitive) with the matching entry in `SHA256SUMS.txt` before installing; other release attachments can be checked with the same command.
- **WebView2 installation.** The installer embeds the Microsoft Edge WebView2 bootstrapper (`embedBootstrapper`). If the WebView2 Runtime is missing on the machine, the installer downloads and installs it, which requires an internet connection.
- **Log files.** Installed builds write backend logs (`backend-stdout.log`, `backend-stderr.log`, and `backend-startup.log` on startup failure) to `%LOCALAPPDATA%\com.amadeus.desktop\logs` by default, or `<AMADEUS_DATA_DIR>\logs` when `AMADEUS_DATA_DIR` is set.
- **Reporting issues.** When opening an issue on [GitHub Issues](https://github.com/meditecnic/AMADEUS/issues), **do not include API keys, tokens, or conversation content**.

### Run it (development)

Choose between the default local mock for frontend work and a real backend for model integration. Both use the browser at http://localhost:1420.

```powershell
git clone https://github.com/meditecnic/AMADEUS.git
cd AMADEUS
```

#### Local mock (default)

You only need **Node.js 22.13+** (20.19+ on 20.x also works). No Python backend, API key, model provider or TTS server is needed.

From the repository root, in PowerShell:

```powershell
cd desktop
npm ci
# Clear a previous real-backend override, if any.
Remove-Item Env:AMADEUS_DEV_MOCK -ErrorAction SilentlyContinue
npm run dev -- --port 1420 --strictPort
```

Open http://localhost:1420/?scenario=streaming. Wait for USER ID to finish typing `Salieri`, then log in with the public local test passphrase **`Gott würfelt nicht`**. Send a message to receive three scripted Japanese/Chinese sentence pairs. Session histories and Memory use synthetic fixtures; edits live in the dev server's memory and reset when it restarts. The browser retains UI preferences and the selected scenario.

The mock handles `/api`, `/ws/chat` (and `/ws`) on the same port 1420 without contacting a backend, provider or TTS server. It is a **development-only** Vite plugin (`apply: 'serve'`); its mock server and selector script are excluded from production builds and are not available in `npm run preview`.

See the [scenario catalog and switching guide](desktop/src/dev-mock/README.md), [scenario JSON files](desktop/src/dev-mock/scenarios/), and [browser evidence walkthrough](desktop/validation/README.md) for login failure, empty, offline, memory and other local checks.

#### Real backend

For real conversations, also install **Python 3.11** and use an API key for a model provider. Stop the mock dev server first.

On Windows, install prerequisites before creating the virtual environment:

1. **Microsoft Visual C++ 2015–2022 Redistributable (x64)** ([download page](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist), direct [`vc_redist.x64.exe`](https://aka.ms/vs/17/release/vc_redist.x64.exe)). On a clean system without `vcruntime140_1.dll`, `pip install` and backend startup still succeed, and the failure only surfaces later during the first memory write when importing `torch` raises `WinError 1114` (`c10.dll` initialization failed).
2. **Python 3.11** ([Python 3.11.9 release page](https://www.python.org/downloads/release/python-3119/), 64-bit Windows installer [`python-3.11.9-amd64.exe`](https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe) — 3.11.9 is the last 3.11 release on python.org that includes Windows binary installers).

Recommended after installing the dependencies: start both processes with one command. The launcher generates a fresh in-memory local API token and passes it to the backend and Vite (never through a `VITE_*` variable). Both processes stop when either exits or you press Ctrl+C.

```powershell
# From the repository root; create backend/.venv and install requirements first.
$env:AMADEUS_DATA_DIR = Join-Path $env:LOCALAPPDATA 'amadeus-dev-data'
Remove-Item Env:AMADEUS_DB_PATH -ErrorAction SilentlyContinue
cd desktop
npm ci
npm run dev:real
```

`AMADEUS_PYTHON` can select a Python executable; the default is `backend/.venv/Scripts/python.exe` on Windows and `backend/.venv/bin/python` on Linux/macOS. On Linux/macOS, set `AMADEUS_DATA_DIR` to a separate development directory, unset `AMADEUS_DB_PATH`, then run `npm run dev:real` from `desktop/`. The launcher refuses an empty data directory and clears the legacy database override. It automatically selects a backend port by default (`BACKEND_PORT` unset or `0`); set `BACKEND_PORT` to 1–65535 to require a fixed port. An occupied fixed port fails startup. `run_dev.bat` now runs the same launcher.

Alternative: two manual terminals, without local API authentication. The backend logs a WARNING that HTTP and WS are unauthenticated and intended only for development. Leave `AMADEUS_LOCAL_API_TOKEN` and `AMADEUS_DEV_LOCAL_API_TOKEN` unset for this alternative.

Terminal 1, from the repository root (PowerShell — use `py -3.11` so another Python on `PATH` is not picked up silently):

```powershell
cd backend
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Use a separate development directory outside TEMP, which may be cleaned up; never select your installed app's data.
$env:AMADEUS_DATA_DIR = Join-Path $env:LOCALAPPDATA 'amadeus-dev-data'
Remove-Item Env:AMADEUS_DB_PATH -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2, from the repository root (PowerShell — skip `npm ci` if you already ran it in Local mock):

```powershell
cd desktop
npm ci
$env:AMADEUS_DEV_MOCK = "0"
npm run dev -- --port 1420 --strictPort
```

`AMADEUS_DEV_MOCK=0` disables the mock and restores Vite's existing `/api`, `/ws` and `/health` proxies to `http://127.0.0.1:8000` (override with `AMADEUS_BACKEND_ORIGIN`). Vite's same-origin `POST /__amadeus/bootstrap` supplies the in-memory token (empty for the manual alternative); packaged builds obtain it from the main window's Tauri command. On Linux/macOS, use `python3.11 -m venv .venv` and `.venv/bin/python` for the backend, and `AMADEUS_DEV_MOCK=0 npm run dev -- --port 1420 --strictPort` for the UI.

Before starting the backend on Linux/macOS, set a separate development directory in that terminal:

```sh
export AMADEUS_DATA_DIR="$(mktemp -d -t amadeus-dev.XXXXXXXX)"
unset AMADEUS_DB_PATH
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Source runs (including scripts) refuse data-directory resolution without a nonempty `AMADEUS_DATA_DIR`: `RuntimeError: AMADEUS_DATA_DIR required; set it to an explicit data directory` (exit 1). `AMADEUS_DB_PATH` still takes precedence for database files when a data directory is set; setting it alone does not bypass this check. `run_dev.bat` and `npm run dev:real` clear that legacy override and check the data directory before starting either process; set `AMADEUS_DATA_DIR` before invoking it. A direct `uvicorn --reload` invocation can leave its parent watcher running after the backend child refuses to start. The offline snapshot builder and rehearsal CLIs keep their explicit-path isolation and do not require this variable.

Open http://localhost:1420 and log in with `Gott würfelt nicht` (the real backend normalizes case and treats `ü` / `ue` as `u`, so `gott wurfelt nicht` and `Gott wuerfelt nicht` also work here). Then open **系统 → 连接** (System → Connections) in the top-right corner; the `OpenAI-compatible · http://127.0.0.1:1234/v1 · local-model` item already listed there is the built-in default preset for a local OpenAI-compatible server. Configure your model connection and save; once the connection check passes, the conversation can start. The memory embedding model (`jinaai/jina-embeddings-v5-text-nano-retrieval`, fp32, 768 dimensions, ~866 MB, CC BY-NC 4.0 by Jina AI; revision `ac5d898c8d382b17167c33e5c8af644a3519b47d`) is downloaded and loaded on first use; existing E5 indexes are rebuilt at startup (a failed load keeps chat available with lexical retrieval). Without a voice pack, `[WARN] TTS reference audio not found ...` lines in the backend log are normal; voice remains optional (see [Voice](#voice-optional)).

To prefetch the pinned model (including its model card with the license declaration):

```sh
hf download jinaai/jina-embeddings-v5-text-nano-retrieval --revision ac5d898c8d382b17167c33e5c8af644a3519b47d --include onnx/model.onnx onnx/model.onnx_data tokenizer.json README.md
```

The desktop window comes from the installer build; its packaging scripts are in `packaging/windows/`, and the frozen backend is a build artifact excluded from the repository; `npm run tauri -- dev` will not start from this checkout (the shell expects a bundled backend). For development, use the browser at http://localhost:1420.

#### Data location

The Windows installer stores data in `%LOCALAPPDATA%\com.amadeus.desktop\data`. Uninstalling keeps data by default; selecting the uninstaller's option to delete app data removes this default directory. To use another location, set the user environment variable `AMADEUS_DATA_DIR` and restart the app. Source runs and standalone runs of `amadeus-backend.exe` require a nonempty `AMADEUS_DATA_DIR` and refuse to start without it; there is no fallback data directory. If a configured data directory is missing or invalid, startup stops instead of creating an empty database. If existing databases fail the startup check (`PRAGMA quick_check`), the app does not create new ones either: it enters a data-unavailable state, pauses chat and other features, and leaves the original databases untouched. If a data migration fails during an upgrade, it first rolls back to the pre-upgrade backup. Desktop backend logs (`backend-stdout.log`, `backend-stderr.log`, and `backend-startup.log` when startup fails) are written to `%LOCALAPPDATA%\com.amadeus.desktop\logs` by default, or `<AMADEUS_DATA_DIR>\logs` when `AMADEUS_DATA_DIR` is set.

Installed Windows builds store API keys in the Windows Credential Manager; source runs keep them only in memory by default, so re-enter them after each restart. To persist keys from a source run on Windows, set `AMADEUS_CREDENTIAL_BACKEND=windows`; this accesses your real Windows credentials. Other systems keep keys in memory only. The local mock does not use these backend data stores.

### Voice (optional)

Without voice, her lines simply appear as text.

- **Speech synthesis** uses [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) with the voice model [TTS-KurisuMakise](https://huggingface.co/bysq/TTS-KurisuMakise) by bysq (GPT-SoVITS v2pro, Apache-2.0). Weights and reference audio are not included. Put your own reference clips in `voice-pack/ref/` under the data directory (`ref_neutral.wav`, `ref_happy.wav`, `ref_sad.wav` and six more emotions; see `backend/app/config.py`), or point the `AMADEUS_REF_*` variables at them. Start the GPT-SoVITS API locally (default `http://127.0.0.1:9880`, override with `SOVITS_URL`) and set `SOVITS_GPT_WEIGHTS` / `SOVITS_SOVITS_WEIGHTS` to the weight files.
- **Speech recognition** downloads the SenseVoice int8 model (~163 MB) and Silero VAD (~644 KB) from the [sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) on first use, verifies fixed SHA-256 hashes, and stores them under `models/speech/` in the data directory. Use `SENSEVOICE_MODEL`, `SENSEVOICE_TOKENS` and `AMADEUS_VAD_MODEL` to point at local copies.

**About the voice.** TTS-KurisuMakise imitates the voice of Makise Kurisu from STEINS;GATE. The character, the original recordings and the voice actor's performance belong to their respective rights holders. The Apache-2.0 label on the model page covers only what its uploader can license; it does not grant any rights to the character, the original recordings or the performance. AMADEUS does not include, download or redistribute the model or any reference audio. Use the voice for personal, non-commercial purposes only, and do not use it to impersonate the voice actor or to make content that looks official. Rights holders who want this reference removed can open a GitHub issue; we will respond promptly.

### Original asset pack

This repository contains **no** original game assets (character art, CG, logos, BGM). The app runs without them and shows its own placeholder art. If you have an asset pack, put it in `asset-pack/amadeus-original/` under the data directory (with `manifest.json` at its root) and restart the backend. The pack is distributed separately with its own notice. The pack holds only material from the original work; the app's own generated textures and illustrations ship in this repository and are listed in [assets/PROVENANCE.md](assets/PROVENANCE.md).

### License

The code in this repository is released under the [MIT License](LICENSE). Provider icons on the connections page come from [Lobe Icons](https://github.com/lobehub/lobe-icons) (MIT); provider names and marks belong to their owners. The installer bundles the Jina AI embedding model under CC BY-NC 4.0, so the installer as a whole is for **non-commercial use only**. Speech components include eSpeak NG (GPLv3); the corresponding source package, `amadeus-0.1.0-speech-sources.zip`, will accompany each version on GitHub Releases. See [third-party notices](THIRD_PARTY_NOTICES.md) for details.

STEINS;GATE, STEINS;GATE 0 and their characters, names and original assets belong to MAGES. Inc. and the respective rights holders. This repository does not include those assets and the project is not used for any commercial purpose. If a rights holder asks, we will act on it promptly.

---

## 日本語

> *世界線変動率 1.048596%――Amadeus システム、起動。*

自分の PC の中で、牧瀬紅莉栖と話せるデスクトップアプリだ。

彼女は日本語で答える。交わした言葉の記憶は、機関の目が届かないあなたの PC の中だけに刻まれる。何を覚えているかはいつでも覗けるし、会話ごと忘れさせることもできる。世界線は **STEINS;GATE** と **β** の二つ。会話も記憶も、決して交わらない。（SG 世界線のアマデウスは、オリジナルの紅莉栖が生きている世界で生まれた。本人の話をすると……少し妬くかもしれない）

- 日本語での会話（中国語字幕つき。英語・日本語 UI は開発予定。彼女はどのモードでも日本語で話す――英語モードでは英語字幕、日本語モードでは字幕なし）
- ローカル長期記憶：SQLite + jina-embeddings-v5-text-nano-retrieval（Jina AI、fp32・768 次元、CC BY-NC 4.0、revision `ac5d898c8d382b17167c33e5c8af644a3519b47d`。事前取得には上記の `hf download` コマンドを使用）。事実は常駐と必要時の二種類。常駐の事実は毎ターンプロンプトに入り、それ以外は必要なときだけモデルが `recall_memory` ツール（DeepSeek のみ）で思い出す。記憶アーカイブで閲覧・修正・削除できる
- DeepSeek、Zhipu GLM、OpenAI 互換 API（ローカルモデルを含む）に対応
- 音声合成（GPT-SoVITS）は任意

この音声は『STEINS;GATE』の牧瀬紅莉栖の声を模倣したものです。キャラクター、原作の録音、声優の実演に関する権利は、それぞれの権利者に帰属します。モデルページの Apache-2.0 は、アップロードした本人が許諾できる範囲にのみ適用され、キャラクター、原作の録音、実演に関する権利の許諾を意味するものではありません。個人かつ非商用の用途に限って使用し、声優へのなりすましや、公式と誤認されるようなコンテンツの制作には使わないでください。権利者の方が関連内容の削除を希望される場合は、GitHub で issue を作成してください。速やかに対応します。

UI はログイン画面を除いて全面的に作り直しているところで、スクリーンショットは完了後に差し替える。

デスクトップ版は **Windows 10/11 x64 のみ**に対応し、v0.1.0 は公開準備中で、まだ公開リリースはない。公開後は [GitHub Releases](https://github.com/meditecnic/AMADEUS/releases) からインストーラーを入手できる。署名がないため SmartScreen の警告が出るので、「詳細情報 → 実行」を選ぶ。WebView2 がなければインストーラーがネット経由で導入する。音声は任意。バックエンドはループバックのみで待ち受け、ポートはシステムが割り当てる。ブラウザーでの開発モードは Linux でも動く。

コードは MIT。インストーラーには CC BY-NC 4.0 の jina 埋め込みモデルが含まれるため、インストーラー全体は非商用利用に限る。音声コンポーネントには eSpeak NG（GPLv3）が含まれ、対応するソース `amadeus-0.1.0-speech-sources.zip` は各バージョンと一緒に Releases で公開する。詳細は [第三者ライセンス](THIRD_PARTY_NOTICES.md) を参照。

### よくある質問

- **SmartScreen の警告**：インストーラーには Authenticode 署名がない。SmartScreen が未知の発行元を警告した場合は、このリポジトリの GitHub Releases から取得したことと `SHA256SUMS.txt` のハッシュ一致を確認してから「詳細情報 → 実行」を選ぶ（更新パッケージの Tauri 署名は更新元の検証用であり、Windows のコード署名の代わりにはならない）。
- **ダウンロードの検証（`SHA256SUMS.txt`）**：`SHA256SUMS.txt` とインストーラーをダウンロードし、PowerShell で `Get-FileHash .\Amadeus_<バージョン>_x64-setup.exe -Algorithm SHA256` を実行する。`Hash` を `SHA256SUMS.txt` の該当ファイルの SHA-256 と（大文字・小文字を区別せず）比較し、完全一致を確認してから導入する。他の添付ファイルも同じコマンドで検証できる。
- **WebView2 の導入**：インストーラーは WebView2 のオンラインブートストラッパー（`embedBootstrapper`）を内蔵している。Microsoft Edge WebView2 Runtime が未導入の環境ではネット経由でダウンロード・導入するため、インターネット接続が必要になる。
- **ログの場所**：インストール版のバックエンドログ（`backend-stdout.log`、`backend-stderr.log`、起動失敗時の `backend-startup.log`）は標準で `%LOCALAPPDATA%\com.amadeus.desktop\logs` に出力される。`AMADEUS_DATA_DIR` を設定している場合は `<AMADEUS_DATA_DIR>\logs` に出力される。
- **不具合の報告**：[GitHub Issues](https://github.com/meditecnic/AMADEUS/issues) で問題を報告する際は、**API キー、トークン、会話内容は貼らないこと**。

開発時の起動方法は [本地开发运行](#本地开发运行) / [Run it](#run-it-development) を参照。**標準はローカル mock** で、Node.js だけで動き、Python バックエンド、API キー、実際のモデルや TTS は不要。[Local mock](#local-mock-default) の手順で `npm ci` と `npm run dev -- --port 1420 --strictPort` を実行し、http://localhost:1420/?scenario=streaming を開く。USER ID が `Salieri` と入力されるまで待ち、公開のローカルテスト用合言葉 `Gott würfelt nicht` でログインする。メッセージを送ると、日本語と中国語字幕の組が三つ、順番に届く。会話と記憶はテストデータで、開発サーバーの再起動でリセットされる。mock は開発専用で、製品ビルドには含まれず、`npm run preview` でも利用できない。

利用できる状態は [シナリオ一覧と切り替え方法](desktop/src/dev-mock/README.md) と [JSON ファイル](desktop/src/dev-mock/scenarios/)、確認手順は [ブラウザー検証ガイド](desktop/validation/README.md) を参照。

**実際のバックエンドを使う場合**は [Real backend](#real-backend) に従って Python サーバーを起動し、フロントエンド側の PowerShell で `$env:AMADEUS_DEV_MOCK = "0"` を設定してから Vite を起動する。合言葉は同じ `Gott würfelt nicht`――神はサイコロを振らない。ログイン後、右上の「系统 → 连接」で自分のモデル接続を設定する。初回の記憶書き込み時に記憶用の埋め込みモデルがダウンロードされる。音声は任意。

インストール版のデータは `%LOCALAPPDATA%\com.amadeus.desktop\data` に保存され、アンインストール時は標準で保持される（アプリデータ削除を選ぶとこのディレクトリも削除される）。保存先はユーザー環境変数 `AMADEUS_DATA_DIR` を設定してアプリを再起動すると変更でき、ソース実行とバックエンド実行ファイルの単独起動ではこの変数が空または未設定なら起動を拒否する。

非公式・非営利のファン作品。原作素材は一切含まない。STEINS;GATE の権利は MAGES. Inc. および各権利者に帰属する。

*El Psy Kongroo.*

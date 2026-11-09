# AMADEUS

一个可以在自己电脑上和牧濑红莉栖说话的桌面应用。

她使用日语回答，屏幕上同时有中文字幕。她会记住你们聊过的事，能看得见，也能让她忘掉。（Amadeus 的人格设定基本上就是参考官方小说和相关资料）本项目共有两条世界线：**STEINS;GATE** 和 **β**。两边的对话、记忆互不相通，界面也各是各的样子。（sg 线相当于是架空的，在现实中红莉栖还活着的情况下，诞生的 Amadeus；加了些设定，你在 sg 线提到真实红莉栖，Amadeus 还会争风吃醋。）

这是一个非商业的粉丝作品，和官方没有任何关系，仓库里也不包含任何原作素材。 · [English](#english) · [日本語](#日本語)

| | |
|---|---|
| ![SG worldline chat](assets/screenshots/sg-chat.png) | ![β worldline chat](assets/screenshots/beta-chat.png) |
| ![Worldline shift transition](assets/screenshots/worldline-shift.png) | ![Memory star map](assets/screenshots/memory-star-map.png) |

*上面的截图是在没放原作素材包的情况下截的，所以画面里是自制的占位图。目前除了登录页，界面正在整体重做，等这轮做完会换上新截图。*

## 现在做到哪了

从 6 月中旬算起，已经开发了大约五个月。

**Windows 安装包已经打出来了**，在一台干净的 Windows 虚拟机上跑过了完整的安装、对话、记忆和卸载验收，现在每次打包也都会在干净的 Windows 环境里自动跑一轮安装和启动检查。v0.1.0 正在做最后的准备，还没有公开发布。

目前已经能用的功能：

- 和她用日语多轮对话，中文字幕逐句跟上
- 长期记忆：她记住了什么随时能在档案页和记忆星图里查看，记错了可以改，不想让她记得的也可以随时让她忘掉（清空某段对话时，也能顺便把那段对话独有的记忆一起抹掉）
- 在 **STEINS;GATE** 和 **β** 两条世界线之间跃迁：在右上角「系统 → 世界线」里就能预览和切换，各自保留会话和草稿（赶时间的话，点一下鼠标或者按 Esc 可以跳过动画）
- 接入你自己的大模型：支持 DeepSeek、智谱 GLM，或任何兼容 OpenAI 接口的服务
- 可选的联网搜索（Tavily、Firecrawl）
- 用红莉栖的声音念出她的回答（需要另外跑 GPT-SoVITS，见下文「[语音](#语音)」）

所有的对话和记忆数据都只存在你自己的电脑里，除了你自己在设置里填的大模型服务（以及开启联网搜索时的搜索服务），不会发往任何别的地方。

英文和日文界面也在计划中——不管切换到哪种界面，她都还是说日语；英文界面下配英文字幕，日文界面下就不再显示字幕了。

## 安装与常见问题

桌面版目前只支持 **Windows 10/11 (64 位)**。等 v0.1.0 发布后，可以直接从 [GitHub Releases](https://github.com/meditecnic/AMADEUS/releases) 下载安装包。

几个安装和使用时可能会遇到的小问题：

- **SmartScreen 弹窗提示「未知发布者」**：个人项目还没有代码签名证书，所以 Windows 会拦截提示。只要确认安装包是从本仓库的 Releases 下载的、校验和也没问题，点「**更多信息 → 仍要运行**」就可以继续安装。（自动更新包带有公钥签名校验，用来确认更新来源。）
- **怎么核对下载的安装包有没有损坏**：发布页会同时提供 `SHA256SUMS.txt`。可以在 PowerShell 里运行 `Get-FileHash .\Amadeus_<版本>_x64-setup.exe -Algorithm SHA256`，对比算出来的 `Hash` 和 `SHA256SUMS.txt` 里的数值是否一致（不区分大小写）。
- **安装时需要联网吗**：如果你的电脑上还没有装过 Microsoft Edge WebView2 Runtime，安装程序会自动联网下载安装；已经装过的话就不需要了。
- **聊天记录、记忆和日志存在哪**：
  - 安装版的数据默认存在 `%LOCALAPPDATA%\com.amadeus.desktop\data`。卸载时默认会帮你保留数据（除非在卸载向导里主动勾选删除应用数据）。如果想换个位置存放，可以设置系统的用户环境变量 `AMADEUS_DATA_DIR` 后重启应用。
  - 如果原本配置的数据目录找不到，或者数据库启动自检异常，程序会直接停下提示，不会在原处新建空库把你的旧数据盖掉。
  - 运行日志默认在 `%LOCALAPPDATA%\com.amadeus.desktop\logs`（如果设了 `AMADEUS_DATA_DIR`，则在 `<AMADEUS_DATA_DIR>\logs`），里面有 `backend-stdout.log`、`backend-stderr.log` 和启动失败时的 `backend-startup.log`。
- **遇到 Bug 想反馈**：欢迎来 [GitHub Issues](https://github.com/meditecnic/AMADEUS/issues) 提问题，贴日志前记得看一眼，**千万不要把自己的 API 密钥、令牌或者私人聊天内容贴出来**。

## 本地开发运行

如果你想把源码拉下来自己跑，有两种方式：平时只看界面可以用默认的「本地 mock」，想跟她真聊就起「真实后端」。两种方式都是在浏览器里打开 http://localhost:1420 （浏览器开发模式在 Linux 上也能跑）。

```powershell
git clone https://github.com/meditecnic/AMADEUS.git
cd AMADEUS
```

### 1. 先看看界面（默认本地 mock）

只需要装好 **Node.js 22.13+**（20.19+ 也可以），不用配 Python 环境，也不用准备 API 密钥或语音服务。

在仓库根目录下打开 PowerShell：

```powershell
cd desktop
npm ci
Remove-Item Env:AMADEUS_DEV_MOCK -ErrorAction SilentlyContinue
npm run dev -- --port 1420 --strictPort
```

打开 http://localhost:1420/?scenario=streaming ，等 USER ID 自动输入完 `Salieri` 后，用公开的本地测试口令 **`Gott würfelt nicht`** 登录。（算是原作的一个小彩蛋吧，后续我也会做些调整）

随便发一句话，就能看到三组逐句流式出来的日语回复和中文字幕。这里的会话和记忆都是测试数据，服务重启后就会还原。其他预设状态可以翻看 [场景目录与切换说明](desktop/src/dev-mock/README.md)。

### 2. 接入模型真聊（真实后端）

真聊需要 **Python 3.11** 和你自己的大模型 API 密钥。Windows 上建虚拟环境之前，建议先装好这两个：

1. **[Microsoft Visual C++ 2015–2022 Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe)**（干净系统上如果缺运行库，第一次写记忆加载模型时会报错 `WinError 1114`）。
2. **[Python 3.11.9 (64 位安装包)](https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe)**。

先在 `backend/` 下建好虚拟环境并装好依赖（首次跑做一次就行）：

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..
```

然后给开发模式指定一个独立的数据目录（源码运行必须显式设置 `AMADEUS_DATA_DIR`，避免误碰正式安装版的数据），在 `desktop/` 下用 `npm run dev:real` 一条命令同时启动前后端（根目录的 `run_dev.bat` 也是跑它）：

```powershell
$env:AMADEUS_DATA_DIR = Join-Path $env:LOCALAPPDATA 'amadeus-dev-data'
Remove-Item Env:AMADEUS_DB_PATH -ErrorAction SilentlyContinue
cd desktop
npm ci
npm run dev:real
```

打开 http://localhost:1420 ，同样用 `Gott würfelt nicht` 登录（不区分大小写，`ü` 打成 `u` 或 `ue` 也行）。进去以后点右上角「**系统 → 连接**」，填好你的模型服务和 API 密钥并保存，连接测试通过就可以开始和她说话了。

第一次写入记忆时会自动下载记忆功能要用的模型，需要稍等一会儿。源码运行时 API 密钥默认只保存在内存里，重启后需要重填（Windows 下如果想在开发模式也存进系统凭据管理器，可以设环境变量 `AMADEUS_CREDENTIAL_BACKEND=windows`）。双终端手动启动、Linux/macOS 命令以及提前下载模型等详细说明可以看下方英文节的 [Run it](#run-it-development)。

## 原作素材包

本仓库**不包含**任何原作素材，比如立绘、CG、Logo、BGM。没有素材包也能正常使用，界面会换成自制画面。（版权问题，目前的解法就只能是素材包了）如果你有素材包，放到数据目录下的 `asset-pack/amadeus-original/`（根目录带 `manifest.json`）再重启应用即可。应用自己生成的底纹和插画随仓库发布，来源登记在 [assets/PROVENANCE.md](assets/PROVENANCE.md)。

## 语音

语音合成是可选功能，不配语音的话她的台词就直接显示成文字。语音通过本机的 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 服务完成，声音模型是 Hugging Face 上 bysq 训练的 [TTS-KurisuMakise](https://huggingface.co/bysq/TTS-KurisuMakise)。本仓库不附带、不下载、也不转发模型权重和参考音频，需要你自己准备。语音识别的后端已经有了（其实前端页面还没做，哈哈）。

这个声音模仿的是《命运石之门》里的牧濑红莉栖。角色、原作录音和声优的表演归各自的权利人所有。模型页上的 Apache-2.0 只覆盖上传者自己能授权的部分，不代表获得了角色、原作录音或表演的授权。请只在个人、非商业场景使用，不要用它冒充声优，也不要制作看起来像官方的内容。权利人如需移除相关内容，请在 GitHub 提 issue，我们会尽快处理。

## 许可与声明

代码使用 [MIT 许可](LICENSE)；安装包带 CC BY-NC 4.0 的 jina 嵌入模型，因此安装包整体仅限**非商业使用**。语音组件含 eSpeak NG（GPLv3），对应源码包 `amadeus-0.1.0-speech-sources.zip` 随每个版本在 Releases 发布，见 [第三方说明](THIRD_PARTY_NOTICES.md)。

STEINS;GATE、STEINS;GATE 0 及其角色、名称与原作素材归 MAGES. Inc. 及各自权利人所有。本仓库不包含这些素材，项目也不用于任何商业用途；权利人如有要求，我们会尽快处理。

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

```mermaid
flowchart LR
  subgraph Desktop["Desktop app (Tauri 2)"]
    UI["React 19 + TypeScript UI"]
  end
  subgraph Backend["Python backend (FastAPI, loopback, system-assigned port)"]
    WS["/ws/chat streaming"]
    Persona["Persona + prompt compiler"]
    Mem["Memory: SQLite + jina"]
    Tr["Subtitle translation"]
    TTS["TTS queue"]
  end
  LLM["LLM provider<br/>(DeepSeek / GLM / OpenAI-compatible)"]
  Sovits["GPT-SoVITS (optional)"]
  UI <--> WS
  WS --> Persona --> LLM
  Mem -- "resident facts + experiences" --> Persona
  WS --> Tr --> LLM
  WS --> TTS --> Sovits
```

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

The Windows installer stores data in `%LOCALAPPDATA%\com.amadeus.desktop\data`. Uninstalling keeps data by default; selecting the uninstaller's option to delete app data removes this default directory. To use another location, set the user environment variable `AMADEUS_DATA_DIR` and restart the app. Source runs and standalone runs of `amadeus-backend.exe` require a nonempty `AMADEUS_DATA_DIR` and refuse to start without it; there is no fallback data directory. If a configured data directory is missing or invalid, or if existing databases fail startup checks, startup stops instead of creating an empty database. Desktop backend logs (`backend-stdout.log`, `backend-stderr.log`, and `backend-startup.log` when startup fails) are written to `%LOCALAPPDATA%\com.amadeus.desktop\logs` by default, or `<AMADEUS_DATA_DIR>\logs` when `AMADEUS_DATA_DIR` is set.

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

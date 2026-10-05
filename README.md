# Amadeus System

Amadeus 是面向《命运石之门》爱好者的本地桌面 AI 对话产品：以牧濑红莉栖的人格进行日语对话，显示中文字幕，并让用户查看、纠正和遗忘长期记忆。目标与用户旅程见[中文 PRD](docs/amadeus-product-prd.zh-CN.md)；目前做到哪里见[工程状态](docs/amadeus-rebuild-status.md)。

当前仓库包含 React/TypeScript 桌面界面、Tauri 壳、Python FastAPI/WebSocket 后端以及 GPT-SoVITS 语音路径。**开发环境能运行，不等于已经有可交付的安装版。** 当前桌面壳尚未随包管理后端；Voice Pack 属于可选能力。

## 从哪里读起

| 想了解 | 文档 |
|---|---|
| 产品目标、首个 MVP 和用户旅程 | [中文 PRD](docs/amadeus-product-prd.zh-CN.md) |
| 当前实现与下一项工程动作 | [工程状态](docs/amadeus-rebuild-status.md) |
| 文档和专题入口 | [文档目录](docs/README.md) |
| 项目协作规则 | [AGENTS.md](AGENTS.md) 与 [Agent 操作目录](docs/agents/README.md) |
| 产品术语和已接受的形态决策 | [CONTEXT.md](CONTEXT.md) 与 [ADR 决策目录](docs/adr/README.md) |
| 上下文中断后的接续 | [恢复说明](docs/amadeus-context-recovery.md) |

产品方案与工程状态各有归属；历史任务合同和旧 Atlas 不能单独证明当前代码已经具备某项能力。

## 本地开发启动

需要 Python、Node.js。运行 Tauri/WebView2 壳还需要 Rust、MSVC Build Tools 和 WebView2；已配置的本机环境可以复用。以下命令用于开发，不负责安装版的后端生命周期。

```powershell
# 终端 1：后端
Set-Location backend
python -m venv .venv                    # 仅首次设置
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2：浏览器开发界面
Set-Location desktop
npm ci                                  # 仅首次设置或依赖更新
npm run dev

# 需要真实桌面壳时，在 desktop/ 中运行
npm run tauri -- dev
```

本地服务使用回环地址；桌面细节见 [desktop/README.md](desktop/README.md)。当前桌面产品以 `desktop/` 为主。打包与发布尚需独立产品验证。

Windows 默认把 API 密钥存入凭据管理器；非 Windows 默认只存内存，重启后需要重新填写。开发验证可显式设置 `AMADEUS_CREDENTIAL_BACKEND=memory`。

首次使用麦克风会从 sherpa-onnx 的 GitHub `asr-models` 发布下载 SenseVoice int8 包（约 163 MB）及 Silero VAD（约 644 KB），保存到用户数据目录的 `models/speech/`。下载通过固定 SHA-256 校验后才使用；SenseVoice 权重与代码采用不同许可，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。离线模型可用 `SENSEVOICE_MODEL`、`SENSEVOICE_TOKENS` 和 `AMADEUS_VAD_MODEL` 指定。

WS 仅接受本机开发界面和 Tauri 来源；无 Origin 的本机工具需显式设置 `AMADEUS_ALLOW_MISSING_WS_ORIGIN=1`。SoVITS 默认仅允许回环地址，确需远程端点时显式设置 `AMADEUS_ALLOW_REMOTE_SOVITS=1`。这些来源和地址限制不提供本机进程身份认证；本机令牌方案另行接入。

可选原作素材包放在用户数据目录（`AMADEUS_DATA_DIR`）下的 `asset-pack/amadeus-original/`，根目录需含 `manifest.json`。开发时也可在启动后端前设置 `$env:AMADEUS_ASSET_PACK_DIR = '<LOCAL_PATH>'` 指向外部样本；后端通过只读 `/api/asset-pack/…` 提供图片和音频。未安装时使用自制默认画面。主程序不包含该素材包，素材包单独分发并附非官方、非商用和应要求下架的声明。

## 目录

| 路径 | 用途 |
|---|---|
| `backend/` | API、对话、人格、记忆与语音调度 |
| `desktop/` | 主要用户界面与 Tauri 壳 |
| `docs/` | 产品目标、工程状态、设计决策与专题说明 |
| `.scratch/` | 工作中的任务记录和证据；历史冻结材料不可随意清理 |
| `.agents/`、`.codex/agents/` | 项目协作工具与角色配置，从属于用户指令和 `AGENTS.md` |

# Amadeus Desktop — Steins;Gate 0 风格 PC 工作站

Tauri 2 (Rust) + React 18 + TypeScript + Vite 桌面端，封装 Amadeus AI 对话系统。

## 快速开始

### 前置条件
- Node.js 18+
- Rust toolchain (rustup)
- 后端运行在 `http://127.0.0.1:8000`（FastAPI + WebSocket）

### 开发模式
```bash
cd desktop
npm install
npm run tauri dev
```
Vite 开发服务器运行在 `http://localhost:1420`，代理 `/ws` 和 `/api` 到后端 8000。

### 打包
```bash
npm run tauri build
```
产出 NSIS 安装包在 `src-tauri/target/release/bundle/nsis/`。

## 架构

```
App.tsx 路由: splash → login → transition → workstation
```

| 组件 | 职责 |
|---|---|
| BootSplash | 启动帧动画 |
| SalieriLogin | Salieri 访问码登录（Canvas 数字雨 + 打字机 USER ID） |
| LogoTransition | 登录成功后 logo1~39 @24fps 过渡动画 |
| Workstation | 主工作站（背景 + 终端 + 头像 + HUD + spinner） |
| AvatarViewer | 多图层动态口型差分（4 情绪 × 4 层 PNG + RMS 口型同步） |
| SpriteAnimator | 通用精灵图/序列帧动画器 |
| SettingsModal | 四标签设置弹窗（CONFIG 环 + BASIC/SOUND/TEXT/VOICE） |
| HudBadge | 右上角 HUD（日期/世界线/连接状态） |

| Hook/Service | 职责 |
|---|---|
| useAmadeusWS | WebSocket 客户端（emotion 捕获 + watchdog + 重连） |
| useAudioPlayer | WebAudio base64 队列播放 + RMS 音量追踪 |
| AudioService | Howler.js BGM/SFX 单例（渐入渐出 + localStorage） |

## 音频映射

| 场景 | 文件 |
|---|---|
| 登录前 BGM | `/audio/bgm/Messenger main theme.ogg` |
| 工作站 BGM | `/audio/bgm/Amadeus.ogg` |
| 发送消息 SFX | `/audio/sfx/连接成功，进入页面1.ogg` |
| 登录成功 SFX | `/audio/sfx/连接成功，进入页面2.ogg` |
| 打开设置 SFX | `/audio/sfx/提示音1.ogg` |
| 错误/超时 SFX | `/assets/audio/gah.ogg` |

## 静态资源

- `public/assets/images/` — kurisu_*.png 差分图 (103) + logo1~39 过渡动画
- `public/assets/ui/` — 背景/设置环/tab/spinner/HUD 等 UI 素材
- `public/audio/` — BGM + SFX
- `public/bg/` — 登录屏背景

## TTS Reference Audio Configuration

TTS (GPT-SoVITS) requires reference audio WAV files for each emotion. Place `.wav` files in `public/assets/audio/`:

| Emotion | File | prompt_text (actual spoken Japanese) |
|---|---|---|
| neutral | `正常语气1.wav` | そもそも、この小さな電子レンジの中で、ビッグバンに匹敵するエネルギーを発生していると定義するのは無理がある… |
| tsundere | `不耐烦，语气稍快.wav` | だいたい、弱い子で、気を遣いそうな女の子をおもちゃにして、調子に乗るなんて… |
| embarrassed | `参考音频 害羞.wav` | べ、別に、あんたのためじゃないんだから |
| intellectual | `正常语气2.wav` | 本実験計画は、気密保持のため、また臨機応変な計画名の変更を可能にするため… |
| angry | `愤怒到破音.wav` | 期待してくれてどうも！でも無理、横歩きに変更しろ！通過しろ！変更しろ！ |

These defaults can be overridden via environment variables:

| Variable | Default | Description |
|---|---|---|
| `AMADEUS_REF_NEUTRAL` | `.../audio/正常语气1.wav` | Path to neutral reference audio |
| `AMADEUS_REF_TSUNDERE` | `.../audio/不耐烦，语气稍快.wav` | Path to tsundere reference audio |
| `AMADEUS_REF_EMBARRASSED` | `.../audio/参考音频 害羞.wav` | Path to embarrassed reference audio |
| `AMADEUS_REF_INTELLECTUAL` | `.../audio/正常语气2.wav` | Path to intellectual reference audio |
| `AMADEUS_REF_ANGRY` | `.../audio/愤怒到破音.wav` | Path to angry reference audio |
| `AMADEUS_TTS_TIMEOUT` | `60.0` | TTS synthesis timeout in seconds |

If a reference audio file is missing, the backend logs a warning and disables that emotion (synthesis will raise a clear error when requested). WAV files should be short (3-10s) samples of Kurisu's voice matching the target emotion.

### GPT-SoVITS Startup Script

A batch script (`启动Amadeus专属API_已更新.bat`) is provided alongside the GPT-SoVITS runtime to automate startup and weight loading:

1. Starts the GPT-SoVITS API v2 on port 9880
2. Waits 8 seconds for warmup
3. Loads the Kurisu GPT weights (`红莉栖语音1.ckpt`)
4. Loads the Kurisu SoVITS weights (`红莉栖语音2.pth`)
5. Displays "Ready" when done

Run it from the GPT-SoVITS directory before starting the Amadeus backend.

## 端口与代理

`vite.config.ts` 配置：
- 开发服务器: `localhost:1420` (strictPort)
- 代理: `/ws` → `http://127.0.0.1:8000` (WebSocket), `/api` → `http://127.0.0.1:8000`

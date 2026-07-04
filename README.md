# Amadeus System

《命运石之门》牧濑红莉栖 AI 对话系统。

- **前端**: React + TypeScript + Vite（移动端 / 桌面 Tauri 端）
- **后端**: Python FastAPI + WebSocket (+ GPT-SoVITS TTS)
- **AI**: DeepSeek API

## 启动

```bash
# 后端
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 移动端前端
cd frontend
npm run dev

# 桌面端前端（PC 版）
cd desktop
npm run dev
```

@echo off
set PYTHONIOENCODING=utf-8
start cmd /k "cd backend && .\.venv\Scripts\python -m uvicorn app.main:app --reload"
start cmd /k "cd desktop && npm run dev -- --force"

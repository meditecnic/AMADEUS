@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
set "AMADEUS_DB_PATH="
rem Set AMADEUS_DATA_DIR to a separate development directory before running.
rem Check before launching reload's persistent parent or either terminal.
pushd "%~dp0backend" || exit /b 1
.\.venv\Scripts\python.exe -c "from app.db import get_data_root; get_data_root()"
if errorlevel 1 (
    popd
    echo Set AMADEUS_DATA_DIR to a separate development directory before running.
    pause
    exit /b 1
)
start "" cmd /k ".\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --reload"
popd
pushd "%~dp0desktop" || exit /b 1
start "" cmd /k "npm run dev -- --force"
popd
endlocal

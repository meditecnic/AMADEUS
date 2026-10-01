@echo off
cd /d "%~dp0"
echo ==========================================
echo   Amadeus Web Frontend Preview Launcher
echo ==========================================

echo [1/4] Installing dependencies...
call npm install

echo [2/4] Building production build...
call npm run build

echo [3/4] Launching browser to Mock Sandbox...
start http://localhost:5000/?mock=true

echo [4/4] Starting static server on port 5000...
npx -y serve -s dist -l 5000

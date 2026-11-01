@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
set "AMADEUS_DB_PATH="
rem The launcher validates AMADEUS_DATA_DIR before starting either process.
pushd "%~dp0desktop" || exit /b 1
call npm run dev:real -- %*
set "AMADEUS_DEV_EXIT=%ERRORLEVEL%"
popd
exit /b %AMADEUS_DEV_EXIT%

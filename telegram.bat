@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Football Telegram Menu

set LOCAL_MODE=true
set USE_MEMORY_CACHE=true
set APP_DEBUG=false
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set POISSON_ONLY_MODE=true
set DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
set DATABASE_URL_SYNC=sqlite:///./data/football_roi.db

echo.
echo  Telegram meni - /start za dugmad
echo  Za gasenje: stopbot.bat
echo.

python -m app.telegram.run_bot
if errorlevel 1 (
    echo.
    echo  [GRESKA] Telegram bot nije uspeo da krene.
    pause
)

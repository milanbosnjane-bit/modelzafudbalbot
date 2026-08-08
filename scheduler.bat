@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Football Scheduler

set LOCAL_MODE=true
set USE_MEMORY_CACHE=true
set APP_DEBUG=false
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set POISSON_ONLY_MODE=true
set DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
set DATABASE_URL_SYNC=sqlite:///./data/football_roi.db

echo.
echo  Football ROI Scheduler + Telegram meni
echo  (settle svake 2h, kvote, dnevni tipovi 08:00 srpsko / 06:00 UTC)
echo  Za gasenje: stopbot.bat
echo.

python -m app.services.scheduler
if errorlevel 1 (
    echo.
    echo  [GRESKA] Scheduler nije uspeo da krene.
    pause
)

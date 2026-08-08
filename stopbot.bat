@echo off
chcp 65001 >nul
cd /d "%~dp0"

title Football ROI Bot - Stop

echo.
echo  Gasim Telegram meni i scheduler...

taskkill /FI "WINDOWTITLE eq Football Telegram*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Football Scheduler*" /F >nul 2>&1

where docker >nul 2>&1
if not errorlevel 1 (
    docker info >nul 2>&1
    if not errorlevel 1 (
        echo  Gasim Docker servise...
        docker compose down >nul 2>&1
    )
)

echo.
echo  Bot je ugasen.
echo.
pause

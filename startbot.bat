@echo off
chcp 65001 >nul
cd /d "%~dp0"

title Football ROI Bot

echo.
echo  ============================================
echo   FOOTBALL ROI BOT - POKRETANJE
echo  ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  [GRESKA] Python nije instaliran.
    echo  Preuzmi sa https://www.python.org/downloads/
    echo  Pri instalaciji stikliraj Add Python to PATH.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo  [GRESKA] Nema .env fajla.
    echo  Kopiraj .env.example u .env i unesi kljuceve.
    echo.
    pause
    exit /b 1
)

if not exist "data" mkdir data
if not exist "data\models" mkdir data\models
if not exist "data\features" mkdir data\features

echo  [1/4] Proveravam Python pakete...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo  [GRESKA] Instalacija paketa nije uspela.
    pause
    exit /b 1
)

echo  [OK] Paketi spremni.
echo.

set LOCAL_MODE=true
set USE_MEMORY_CACHE=true
set APP_DEBUG=false
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set POISSON_ONLY_MODE=true
set PAPER_TRADING_ENABLED=true
set DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
set DATABASE_URL_SYNC=sqlite:///./data/football_roi.db

echo  [2/4] Kalibracija Dixon-Coles parametara (MLE)...
echo         Prvi put moze potrajati 1-2 min — sacekajte...
python -m app.calibrate_models --if-missing
if errorlevel 1 (
    echo  [UPOZORENJE] Kalibracija nije uspela - nastavljam sa default DC parametrima.
) else (
    echo  [OK] DC parametri spremni.
)
echo.

echo  [3/4] Pokrecem bot (full-build: povlaci meceve i generise pikove)...
echo         Ovo moze potrajati 30-60 sek...
echo.

python -m app.run_local --full-build
if errorlevel 1 (
    echo.
    echo  Pokretanje nije uspelo. Proveri .env i internet konekciju.
    pause
    exit /b 1
)

echo  [4/5] Pokrecem Telegram meni (dugmad + /start)...
start "Football Telegram" /min "%~dp0telegram.bat"

echo  [5/5] Pokrecem scheduler (settle, kvote, pickovi 08:00 srpsko)...
start "Football Scheduler" /min "%~dp0scheduler.bat"

echo.
echo  ============================================
echo   BOT JE POKRENUT
echo  ============================================
echo.
echo   Telegram meni: minimizovan prozor Football Telegram - posalji /start
echo   Scheduler:     minimizovan prozor Football Scheduler
echo.
echo   Tipovi danas:  vec upisani - ne pokreci startbot ponovo istog dana
echo   Za rebuild:    python -m app.run_local --full-build
echo   Za gasenje:    stopbot.bat
echo.
pause

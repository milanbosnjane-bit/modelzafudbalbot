@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo.
echo  ============================================
echo   PUSH NA GITHUB — milanbosnjane-bit
echo  ============================================
echo.

where gh >nul 2>&1
if errorlevel 1 (
    echo  [GRESKA] GitHub CLI nije instaliran.
    echo  winget install GitHub.cli
    pause
    exit /b 1
)

echo  [1/3] GitHub login (otvara browser)...
gh auth login -h github.com -p https -w

echo.
echo  [2/3] Kreiram repo i push...
gh repo create milanbosnjane-bit/modelzafudbalbot --public --source=. --remote=origin --push --description "Football ROI Bot + iOS app + GitHub Actions IPA"

if errorlevel 1 (
    echo.
    echo  Repo mozda vec postoji — pokusavam obican push...
    git remote remove origin 2>nul
    git remote add origin https://github.com/milanbosnjane-bit/modelzafudbalbot.git
    git push -u origin main
)

echo.
echo  [3/3] Gotovo!
echo  Repo: https://github.com/milanbosnjane-bit/modelzafudbalbot
echo  Actions: https://github.com/milanbosnjane-bit/modelzafudbalbot/actions
echo.
pause

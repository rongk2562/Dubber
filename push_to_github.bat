@echo off
chcp 65001 >nul
title Push v1.2.1 to GitHub (rongk2562/Dubber)
cd /d "%~dp0"
echo ========================================================
echo Committing and Pushing AI Movie Dubbing Studio v1.2.1...
echo Repository: https://github.com/rongk2562/Dubber.git
echo ========================================================
"C:\Program Files\Git\cmd\git.exe" add .
"C:\Program Files\Git\cmd\git.exe" commit -m "Release v1.2.1: Remove search box & align equal toolbar buttons"
"C:\Program Files\Git\cmd\git.exe" tag -f v1.2.1
"C:\Program Files\Git\cmd\git.exe" push -u origin main --tags
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Push failed or requires GitHub sign-in.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo [+] Successfully pushed v1.2.1 to https://github.com/rongk2562/Dubber !
pause

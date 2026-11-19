@echo off
chcp 65001 >nul
title Push v1.4.0 to GitHub (rongk2562/Dubber)
cd /d "%~dp0"
echo ========================================================
echo Committing and Pushing AI Movie Dubbing Studio v1.4.0...
echo Repository: https://github.com/rongk2562/Dubber.git
echo ========================================================
"C:\Program Files\Git\cmd\git.exe" add .
"C:\Program Files\Git\cmd\git.exe" commit -m "Release v1.4.0: Batch Mode, Masking/Audio Mix, OpenAI & Speedups"
"C:\Program Files\Git\cmd\git.exe" tag -f v1.4.0
"C:\Program Files\Git\cmd\git.exe" push -u origin main
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Push to main failed or requires GitHub sign-in.
    pause
    exit /b %ERRORLEVEL%
)
"C:\Program Files\Git\cmd\git.exe" push -f origin refs/tags/v1.4.0
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Push failed or requires GitHub sign-in.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo [+] Successfully pushed v1.4.0 to https://github.com/rongk2562/Dubber !
pause

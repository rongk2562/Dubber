@echo off
chcp 65001 >nul
title Building Inno Setup Installer - AI Movie Dubbing Studio
cd /d "%~dp0"
echo =================================================================
echo Compiling Inno Setup Installer (with _internal, FFmpeg & Whisper)
echo =================================================================
"C:\Program Files\Inno Setup 7\ISCC.exe" "d:\summray_mo\installer.iss"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Inno Setup compilation failed with code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo [+] Success! Your Setup Installer is ready at:
echo     d:\summray_mo\dist\AI_Movie_Dubbing_Studio_Setup_v1.4.0.exe
pause


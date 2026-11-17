@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title Building AI Movie Dubbing Studio (.EXE)
cd /d "%~dp0"
echo ========================================================
echo Building Standalone Windows .EXE for Other PCs...
echo ========================================================
python build_exe.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Build encountered an error (Code %ERRORLEVEL%).
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo [+] Done! Your portable folder is ready at:
echo     dist\AI_Movie_Dubbing_Studio\AI_Movie_Dubbing_Studio.exe
pause


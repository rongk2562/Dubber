@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title AI Movie Translator & Auto Dubbing Studio
cd /d "%~dp0"
python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application stopped with error code %ERRORLEVEL%.
    pause
)

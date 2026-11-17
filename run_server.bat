@echo off
title Mongoose Update Server - AI Movie Dubbing Studio
cd /d "%~dp0server"
echo ========================================================
echo Starting Mongoose & MongoDB Version Control Server...
echo Database URI: mongodb://localhost:27017/aidub_updates
echo ========================================================
node server.js
pause

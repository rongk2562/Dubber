@echo off
title AI Dub Studio - Internet Tunnel
echo ========================================================
echo Starting Internet Public HTTPS Tunnel for Port 5000...
echo (Allows any PC worldwide to connect to your update server)
echo ========================================================
npx -y localtunnel --port 5000
pause

@echo off
setlocal
cd /d "%~dp0"

echo Starting Ben2 Bridges at http://127.0.0.1:8000/index.html?v=20260728-2
echo Keep this window open while using the map.
echo Press Ctrl+C or close this window to stop the server.
echo.

start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 1; Start-Process 'http://127.0.0.1:8000/index.html?v=20260728-2'"
python -m http.server 8000 --bind 127.0.0.1

if errorlevel 1 (
  echo.
  echo The map server could not start.
  pause
)

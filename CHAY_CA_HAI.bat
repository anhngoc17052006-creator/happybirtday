@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "FB Auto Post Backend" cmd /k "call CHAY_WEB_WINDOWS.bat"
echo Dang doi backend san sang...
for /L %%i in (1,1,180) do (
  powershell -NoProfile -Command "if (Test-NetConnection 127.0.0.1 -Port 5000 -InformationLevel Quiet) { exit 0 } else { exit 1 }" >nul 2>nul
  if not errorlevel 1 goto ready
  timeout /t 2 /nobreak >nul
)
echo Backend chua san sang sau 6 phut. Hay kiem tra cua so backend.
pause
exit /b 1
:ready
call CHAY_CLOUDFLARE_TUNNEL.bat

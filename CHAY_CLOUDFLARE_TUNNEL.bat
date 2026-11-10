@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "fb_web_config.bat" (
  echo Thieu fb_web_config.bat. Hay tao tu file .example truoc.
  pause
  exit /b 1
)
call "%~dp0fb_web_config.bat"
if "%FB_WEB_USER%"=="" goto config_error
if "%FB_WEB_PASSWORD%"=="" goto config_error
if "%FB_WEB_PASSWORD%"=="THAY_BANG_MAT_KHAU_RIENG_DAI_IT_NHAT_20_KY_TU" goto config_error
where cloudflared >nul 2>nul
if errorlevel 1 (
  if exist "%~dp0cloudflared.exe" (
    set "CLOUDFLARED=%~dp0cloudflared.exe"
    goto run_tunnel
  )
  echo Khong tim thay cloudflared.
  echo Tai ban Windows tu:
  echo https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
  echo Dat file cloudflared.exe vao cung thu muc nay hoac them vao PATH.
  pause
  exit /b 1
)
set "CLOUDFLARED=cloudflared"
:run_tunnel
echo Dang tao duong dan HTTPS. Cho dong nay hien URL trycloudflare.com.
"%CLOUDFLARED%" tunnel --url http://127.0.0.1:5000
pause
exit /b 0
:config_error
echo Hay sua FB_WEB_USER va FB_WEB_PASSWORD trong fb_web_config.bat.
pause
exit /b 1

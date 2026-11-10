@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "fb_web_config.bat" (
  echo CHUA CO CAU HINH MAT KHAU.
  echo Hay copy fb_web_config.bat.example thanh fb_web_config.bat va sua mat khau.
  pause
  exit /b 1
)
call "%~dp0fb_web_config.bat"
if "%FB_WEB_USER%"=="" goto config_error
if "%FB_WEB_PASSWORD%"=="" goto config_error
if "%FB_WEB_PASSWORD%"=="THAY_BANG_MAT_KHAU_RIENG_DAI_IT_NHAT_20_KY_TU" goto config_error
set "PY="
py -3.12 --version >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3.13 --version >nul 2>nul && set "PY=py -3.13"
if not defined PY set "PY=python"
echo Dang su dung: %PY%
%PY% --version
%PY% -m pip install --upgrade pip
if errorlevel 1 goto error
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto error
%PY% -m playwright install chromium
if errorlevel 1 goto error
echo.
echo FB Auto Post dang chay tai http://127.0.0.1:5000
echo Tai khoan web: %FB_WEB_USER%
echo Giu cua so nay mo khi su dung.
start "" http://127.0.0.1:5000
%PY% app.py
pause
exit /b 0
:config_error
echo Hay sua FB_WEB_USER va FB_WEB_PASSWORD trong fb_web_config.bat.
pause
exit /b 1
:error
echo Cai dat that bai. Hay cai Python 3.12/3.13 va kiem tra Internet.
pause
exit /b 1

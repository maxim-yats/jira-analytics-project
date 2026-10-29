@echo off
setlocal
cd /d "%~dp0\.."
if not exist config.json (
  copy config.example.json config.json >nul
  echo Created config.json from the example. Review settings, then run this script again.
  exit /b 0
)
py -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%
py libs\main.py --config config.json %*
exit /b %errorlevel%

@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar-servidor-windows.ps1"
if errorlevel 1 (
  echo.
  echo A instalacao falhou. Confira a mensagem acima.
  pause
  exit /b 1
)
pause

@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\codex-router.ps1" run %*
exit /b %ERRORLEVEL%


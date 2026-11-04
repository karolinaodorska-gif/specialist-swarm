@echo off
rem Double-click launcher for start.ps1 (works without changing the PowerShell execution policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause

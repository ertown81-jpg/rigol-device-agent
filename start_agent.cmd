@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_agent.ps1"
if errorlevel 1 (
    echo.
    echo Failed to start RIGOL Device Agent.
    pause
)

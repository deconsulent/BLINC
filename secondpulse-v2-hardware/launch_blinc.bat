@echo off
setlocal
title BLINC Diagnostic Console Launcher
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\launch_blinc.ps1" %*

if errorlevel 1 (
    echo.
    echo [!] Launcher encountered an error.
    pause
)


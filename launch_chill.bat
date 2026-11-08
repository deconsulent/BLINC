@echo off
setlocal
title CHILL Diagnostic Console Launcher
cd /d "%~dp0"

echo =====================================================================
echo    CHILL : Cells Housed In Low-stress Life
echo    Climathon 2026 - Edge Hardware Diagnostic Console
echo =====================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_blinc.ps1" %*

if errorlevel 1 (
    echo.
    echo [!] Launcher encountered an error.
    pause
)

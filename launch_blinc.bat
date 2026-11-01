@echo off
setlocal
title BLINC Diagnostic Console Launcher
cd /d "%~dp0"

echo =====================================================================
echo    BLINC : Battery Life ^& Intelligence Node Controller
echo    Climathon 2026 - Edge Hardware Diagnostic Console
echo =====================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_blinc.ps1" %*

if errorlevel 1 (
    echo.
    echo [!] Launcher encountered an error.
    pause
)

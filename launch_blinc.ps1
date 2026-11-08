# CHILL System Launcher - Cells Housed In Low-stress Life
# Climathon 2026 - Edge Hardware Diagnostic Console

param(
    [string]$Port = "COM18",
    [int]$Baud = 115200,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host " ===================================================================== " -ForegroundColor Cyan
Write-Host "   CHILL : Cells Housed In Low-stress Life" -ForegroundColor Green
Write-Host "   Climathon 2026 - Circular Stationary Battery Storage System" -ForegroundColor Gray
Write-Host " ===================================================================== " -ForegroundColor Cyan
Write-Host ""

# Determine working directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path "$ScriptDir\secondpulse-v2-hardware") {
    $WorkDir = "$ScriptDir\secondpulse-v2-hardware"
}
else {
    $WorkDir = $ScriptDir
}
Set-Location $WorkDir

# Check Python installation
$PythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $PythonCmd) {
    Write-Host " [!] ERROR: Python not found in system PATH." -ForegroundColor Red
    Write-Host " Please install Python 3.10+ and add it to your PATH." -ForegroundColor Yellow
    Pause
    exit 1
}

# Scan Serial Ports
Write-Host " [*] Scanning for hardware COM ports..." -ForegroundColor Yellow
$DetectedPort = & python -c "import serial.tools.list_ports; all_p = serial.tools.list_ports.comports(); usb_p = [p.device for p in all_p if 'bluetooth' not in p.description.lower() and 'bth' not in (p.hwid or '').lower()]; print('$Port' if '$Port' in [p.device for p in all_p] else (usb_p[0] if usb_p else 'NONE'))" 2>$null

if ($DetectedPort -and $DetectedPort -ne "NONE") {
    $TargetPort = $DetectedPort.Trim()
    Write-Host " [OK] Hardware Serial Port Bound: $TargetPort at $($Baud) baud" -ForegroundColor Green
}
else {
    $TargetPort = $Port
    Write-Host " [i] Target Serial Port: $TargetPort at $($Baud) baud" -ForegroundColor Cyan
    Write-Host "     (Daemon will automatically attach when ESP32-S3 is plugged in)" -ForegroundColor Gray
}

$env:SERIAL_PORT = $TargetPort
$env:BAUD_RATE = "$Baud"

# Clean up any stale instances holding ports 8000 or 8501
Write-Host " [*] Ensuring network ports 8000 and 8501 are available..." -ForegroundColor Gray
Get-NetTCPConnection -LocalPort 8000, 8501 -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    catch {}
}

# Launch FastAPI Hardware Daemon (Port 8000)
Write-Host " [*] Starting BLINC Hardware Daemon on http://localhost:8000..." -ForegroundColor Cyan
$BackendJob = Start-Process python -ArgumentList "-m uvicorn backend.hardware_daemon:app --host 0.0.0.0 --port 8000" -PassThru -NoNewWindow

# Launch Streamlit Frontend (Port 8501)
Write-Host " [*] Starting Streamlit UI on http://localhost:8501..." -ForegroundColor Cyan
$FrontendJob = Start-Process python -ArgumentList "-m streamlit run frontend\app.py --server.port 8501 --server.headless true" -PassThru -WindowStyle Hidden

# Wait for backend health check
Write-Host " [*] Waiting for hardware daemon initialization..." -ForegroundColor Yellow
$MaxRetries = 15
$Online = $false
for ($i = 0; $i -lt $MaxRetries; $i++) {
    Start-Sleep -Milliseconds 600
    try {
        $res = Invoke-RestMethod -Uri "http://localhost:8000/api/status" -TimeoutSec 1 -ErrorAction Stop
        if ($res.solution -eq "CHILL" -or $res.solution -eq "BLINC") {
            $Online = $true
            break
        }
    }
    catch {}
}

if ($Online) {
    Write-Host " [OK] CHILL Core Daemon Online & Operational!" -ForegroundColor Green
}
else {
    Write-Host " [i] Hardware daemon starting up in background..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host " --------------------------------------------------------------------- " -ForegroundColor DarkGray
Write-Host "   Active Console Endpoints:" -ForegroundColor White
Write-Host "   -> Native Apple UI (Recommended): http://localhost:8000" -ForegroundColor Green
Write-Host "   -> Streamlit Dashboard:           http://localhost:8501" -ForegroundColor Cyan
Write-Host "   -> Live Telemetry API:            http://localhost:8000/api/status" -ForegroundColor Gray
Write-Host " --------------------------------------------------------------------- " -ForegroundColor DarkGray
Write-Host "   Press Ctrl+C in this window to stop all services cleanly." -ForegroundColor Yellow
Write-Host ""

if (-not $NoBrowser) {
    Start-Process "http://localhost:8000"
}

# Keep script running and gracefully clean up processes on exit
try {
    while ($true) {
        if ($BackendJob.HasExited) {
            Write-Host " [!] Hardware daemon stopped." -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 2
    }
}
finally {
    Write-Host ""
    Write-Host " [*] Shutting down BLINC services..." -ForegroundColor Yellow
    if ($BackendJob -and -not $BackendJob.HasExited) {
        Stop-Process -Id $BackendJob.Id -Force -ErrorAction SilentlyContinue
    }
    if ($FrontendJob -and -not $FrontendJob.HasExited) {
        Stop-Process -Id $FrontendJob.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host " [OK] All BLINC processes terminated cleanly." -ForegroundColor Green
}

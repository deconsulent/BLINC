# Wrapper to root launch_blinc.ps1
$ParentScript = Join-Path $PSScriptRoot "..\launch_blinc.ps1"
if (Test-Path $ParentScript) {
    & $ParentScript @args
} else {
    Write-Host "Error: Root launch script not found at $ParentScript" -ForegroundColor Red
}


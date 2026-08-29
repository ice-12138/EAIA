$ErrorActionPreference = 'Stop'

$projectRoot = 'E:\code\EAIA'
$frontendRoot = Join-Path $projectRoot 'frontend'
$logRoot = Join-Path $projectRoot 'logs'
$backendOut = Join-Path $logRoot 'backend.out.log'
$backendErr = Join-Path $logRoot 'backend.err.log'
$frontendOut = Join-Path $logRoot 'frontend.out.log'
$frontendErr = Join-Path $logRoot 'frontend.err.log'

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Stop-PortProcess([int]$port) {
    $connections = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    $processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($processId in $processIds) {
        if ($processId -and $processId -ne $PID) {
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
    }
}

Stop-PortProcess 8000
Stop-PortProcess 5173
Start-Sleep -Milliseconds 800

Remove-Item -LiteralPath $backendOut, $backendErr, $frontendOut, $frontendErr -Force -ErrorAction SilentlyContinue

Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/d', '/c', "conda run -n EAIA python web_api.py 1>`"$backendOut`" 2>`"$backendErr`"" `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden

Start-Process -FilePath 'npm.cmd' `
    -ArgumentList 'run', 'dev', '--', '--host', '127.0.0.1' `
    -WorkingDirectory $frontendRoot `
    -RedirectStandardOutput $frontendOut `
    -RedirectStandardError $frontendErr `
    -WindowStyle Hidden

$backendReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
        Write-Host "Backend:  http://127.0.0.1:8000/ [$($health.StatusCode)]"
        $backendReady = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $backendReady) {
    Write-Warning 'Backend health check failed after waiting 30 seconds.'
}

$frontendReady = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    try {
        $frontend = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5173/' -TimeoutSec 2
        Write-Host "Frontend: http://127.0.0.1:5173/ [$($frontend.StatusCode)]"
        $frontendReady = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $frontendReady) {
    Write-Warning 'Frontend health check failed after waiting 15 seconds.'
}

Write-Host "Logs:     $logRoot"

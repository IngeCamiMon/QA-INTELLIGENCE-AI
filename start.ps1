param([switch]$Restart)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$frontend = Join-Path $root "frontend"
$python = Join-Path $root ".venv\Scripts\python.exe"
$apiPidFile = Join-Path $root ".qa-api.pid"
$webPidFile = Join-Path $root ".qa-web.pid"

function Test-LocalPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(400)) { return $false }
        $client.EndConnect($pending)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Test-HttpService([string]$Uri) {
    try {
        $response = Invoke-WebRequest -Uri $Uri -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Stop-TrackedService([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return }
    try {
        $raw = (Get-Content $PidFile -Raw).Trim()
        if ($raw -match '^\d+$') {
            $processId = [int]$raw
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            $metadataMatches = $process -and $process.StartTime.ToUniversalTime() -le (Get-Item $PidFile).LastWriteTimeUtc.AddSeconds(2)
        } else {
            $metadata = $raw | ConvertFrom-Json
            $processId = [int]$metadata.id
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            $metadataMatches = $process -and $process.StartTime.ToUniversalTime().Ticks -eq [long]$metadata.startedAtTicks
        }
        if ($metadataMatches) {
            & taskkill.exe /PID $process.Id /T /F | Out-Null
            Write-Host "Detenido proceso administrado (PID $($process.Id))." -ForegroundColor Yellow
        } elseif ($process) {
            Write-Warning "El PID registrado ya pertenece a otra instancia; no la detuve."
        }
    } catch {
        Write-Warning "No pude validar el proceso anterior en $PidFile; no lo detuve."
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Save-ProcessMetadata([string]$PidFile, $Process) {
    $metadata = @{ id = $Process.Id; startedAtTicks = $Process.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json -Compress
    Set-Content -LiteralPath $PidFile -Value $metadata -NoNewline
}

if ($Restart) {
    Stop-TrackedService $apiPidFile
    Stop-TrackedService $webPidFile
    Start-Sleep -Seconds 1
    if ((Test-LocalPort 8000) -or (Test-LocalPort 3000)) {
        throw "Hay un servicio en 8000/3000 que no administra start.ps1. Deténlo con Ctrl+C en su terminal y vuelve a ejecutar .\start.ps1 -Restart."
    }
}

if (-not (Test-Path $python)) {
    throw "No existe .venv. Instala primero el proyecto con: py -3.11 -m venv .venv y .\.venv\Scripts\python.exe -m pip install -e `".[dev]`""
}
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    throw "Faltan dependencias del frontend. Ejecuta npm ci dentro de la carpeta frontend."
}

if (Test-HttpService "http://127.0.0.1:8000/health") {
    Write-Host "API ya está activa en http://127.0.0.1:8000" -ForegroundColor Green
} elseif (Test-LocalPort 8000) {
    throw "El puerto 8000 ya está ocupado por otro proceso. Libéralo o cambia el puerto antes de iniciar la API."
} else {
    $process = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "qa_intelligence.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $root -PassThru
    Save-ProcessMetadata $apiPidFile $process
    Write-Host "Iniciando API en http://127.0.0.1:8000" -ForegroundColor Cyan
}

if (Test-HttpService "http://127.0.0.1:3000/") {
    Write-Host "Dashboard ya está activo en http://127.0.0.1:3000" -ForegroundColor Green
} elseif (Test-LocalPort 3000) {
    throw "El puerto 3000 ya está ocupado por otro proceso. Libéralo o cambia el puerto antes de iniciar Next.js."
} else {
    $process = Start-Process -FilePath $env:ComSpec `
        -ArgumentList @("/k", "npm.cmd run dev") `
        -WorkingDirectory $frontend -PassThru
    Save-ProcessMetadata $webPidFile $process
    Write-Host "Iniciando dashboard en http://127.0.0.1:3000" -ForegroundColor Cyan
}

Write-Host "Si es la primera ejecución, espera unos segundos y abre http://127.0.0.1:3000" -ForegroundColor White

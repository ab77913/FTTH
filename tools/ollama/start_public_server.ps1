$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = if ($env:OLLAMA_CHAT_PORT) { [int]$env:OLLAMA_CHAT_PORT } else { 5000 }
$Logs = Join-Path $Root "logs"
$ServerLog = Join-Path $Logs "server.log"
$ServerErrLog = Join-Path $Logs "server.err.log"
$NgrokLog = Join-Path $Logs "ngrok.log"
$NgrokErrLog = Join-Path $Logs "ngrok.err.log"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Find-CommandPath {
    param([string]$Name)

    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\$Name.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\ngrok\ngrok.exe"),
        "C:\Program Files\ngrok\ngrok.exe",
        "C:\ngrok\ngrok.exe"
    )

    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }

    if ($Name -eq "ngrok") {
        $wingetNgrok = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Recurse -Filter "ngrok.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($wingetNgrok) { return $wingetNgrok.FullName }
    }

    return $null
}

function Ensure-Ngrok {
    $ngrok = Find-CommandPath "ngrok"
    if ($ngrok) { return $ngrok }

    $winget = Find-CommandPath "winget"
    if (-not $winget) {
        throw "ngrok is not installed and winget was not found. Install ngrok from https://ngrok.com/download, then run this file again."
    }

    Write-Host "ngrok was not found. Installing ngrok with winget..."
    & $winget install --id Ngrok.Ngrok --exact --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install ngrok. Install ngrok manually from https://ngrok.com/download, then run this file again."
    }

    $ngrok = Find-CommandPath "ngrok"
    if (-not $ngrok) {
        throw "ngrok installed, but ngrok.exe was not found in this shell. Close this window and run start_server.bat again."
    }

    return $ngrok
}

function Update-NgrokIfPossible {
    param([string]$NgrokPath)

    Write-Host "Checking ngrok agent update..."
    & $NgrokPath update | Out-File -FilePath $NgrokLog -Append -Encoding utf8
}

function Wait-ForHttp {
    param(
        [string]$Url,
        [int]$Seconds = 30
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    return $false
}

function Get-NgrokPublicUrl {
    try {
        $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2
        $https = $tunnels.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($https) { return $https.public_url }

        $first = $tunnels.tunnels | Select-Object -First 1
        if ($first) { return $first.public_url }
    } catch {
        return $null
    }

    return $null
}

function Stop-ChildProcess {
    param($Process)
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

$python = Find-CommandPath "python"
if (-not $python) {
    $python = Find-CommandPath "py"
}
if (-not $python) {
    throw "Python was not found. Install Python or add it to PATH, then run start_server.bat again."
}

$ngrok = Ensure-Ngrok
Update-NgrokIfPossible $ngrok

if ($env:NGROK_AUTHTOKEN) {
    Write-Host "Configuring ngrok authtoken from NGROK_AUTHTOKEN..."
    & $ngrok config add-authtoken $env:NGROK_AUTHTOKEN | Out-Null
}

Write-Host ""
Write-Host "Starting Flask server on http://localhost:$Port ..."
$env:OLLAMA_CHAT_PORT = "$Port"
$server = Start-Process -FilePath $python -ArgumentList @("server.py") -WorkingDirectory $Root -RedirectStandardOutput $ServerLog -RedirectStandardError $ServerErrLog -WindowStyle Hidden -PassThru

if (-not (Wait-ForHttp "http://127.0.0.1:$Port/" 45)) {
    Write-Host "The Flask server did not answer yet. Last server log lines:"
    if (Test-Path $ServerLog) { Get-Content $ServerLog -Tail 40 }
    if (Test-Path $ServerErrLog) { Get-Content $ServerErrLog -Tail 40 }
    Stop-ChildProcess $server
    exit 1
}

Write-Host "Starting ngrok public tunnel..."
$ngrokArgs = @("http", "$Port", "--log=stdout")
if ($env:NGROK_DOMAIN) {
    $ngrokArgs += @("--domain", $env:NGROK_DOMAIN)
}

$tunnel = Start-Process -FilePath $ngrok -ArgumentList $ngrokArgs -WorkingDirectory $Root -RedirectStandardOutput $NgrokLog -RedirectStandardError $NgrokErrLog -WindowStyle Hidden -PassThru

$publicUrl = $null
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    if ($tunnel.HasExited) { break }
    $publicUrl = Get-NgrokPublicUrl
    if ($publicUrl) { break }
}

Write-Host ""
Write-Host "============================================================"
Write-Host "Ollama Chat is running"
Write-Host "Local URL:  http://localhost:$Port"

if ($publicUrl) {
    Write-Host "Public URL: $publicUrl"
    Write-Host "API URL:    $publicUrl/api/v1/"
    Write-Host ""
    Write-Host "Share the Public URL with anyone who should access this app."
} else {
    Write-Host "Public URL: not available yet"
    Write-Host ""
    Write-Host "ngrok usually requires a free account authtoken before it can create tunnels."
    Write-Host "Run this once, then start again:"
    Write-Host "  ngrok config add-authtoken YOUR_TOKEN"
    Write-Host ""
    Write-Host "ngrok log:"
    if (Test-Path $NgrokLog) { Get-Content $NgrokLog -Tail 40 }
    if (Test-Path $NgrokErrLog) { Get-Content $NgrokErrLog -Tail 40 }
}

Write-Host "============================================================"
Write-Host ""
Write-Host "Logs:"
Write-Host "  Server: $ServerLog"
Write-Host "  Server errors: $ServerErrLog"
Write-Host "  ngrok:  $NgrokLog"
Write-Host "  ngrok errors: $NgrokErrLog"
Write-Host ""
Write-Host "Press Ctrl+C or close this window to stop the server and tunnel."

try {
    while ($true) {
        if ($server.HasExited) {
            Write-Host "Server process stopped. Check $ServerLog"
            break
        }
        if ($tunnel.HasExited) {
            Write-Host "ngrok process stopped. Check $NgrokLog"
            break
        }
        Start-Sleep -Seconds 2
    }
} finally {
    Stop-ChildProcess $tunnel
    Stop-ChildProcess $server
}

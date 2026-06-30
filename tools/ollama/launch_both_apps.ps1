$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$FtthBat = "C:\Users\Admin\FTTH_PRODUCTION\start_app.bat"
$FtthDir = Split-Path -Parent $FtthBat
$ChatScript = Join-Path $Root "start_office_server.ps1"
$OfficeIp = "172.19.64.7"
$FtthUrl = "http://$OfficeIp/"
$ChatUrl = "http://$OfficeIp:8082/"

function Test-Http {
    param(
        [string]$Url,
        [int]$TimeoutSec = 2
    )

    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-ForHttp {
    param(
        [string]$Url,
        [int]$Seconds = 120,
        [string]$Name = "service"
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http $Url 2) {
            return $true
        }
        Start-Sleep -Seconds 2
    }

    Write-Host "WARNING: $Name did not answer at $Url within $Seconds seconds."
    return $false
}

function Stop-ChatOnly {
    $nginx = $null
    if (Test-Path "C:\nginx\nginx.exe") {
        $nginx = "C:\nginx\nginx.exe"
    } else {
        $nginx = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter nginx.exe -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
    }

    if ($nginx) {
        $prefix = Join-Path $Root ".runtime\nginx-chat"
        $conf = Join-Path $Root ".runtime\nginx-office.conf"
        if (Test-Path $conf) {
            try { & $nginx -p "$prefix\" -c $conf -s stop 2>$null } catch {}
        }
    }

    Get-NetTCPConnection -LocalPort 5100,8082 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

if (-not (Test-Path $FtthBat)) {
    throw "FTTH launcher was not found: $FtthBat"
}
if (-not (Test-Path $ChatScript)) {
    throw "Chat launcher was not found: $ChatScript"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "Starting both applications from one machine"
Write-Host "FTTH:        $FtthUrl"
Write-Host "Ollama Chat: $ChatUrl"
Write-Host "============================================================"
Write-Host ""

$ftthAlreadyUp = (Test-Http "http://127.0.0.1/" 2) -and (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)

if ($ftthAlreadyUp) {
    Write-Host "FTTH already appears to be running. Leaving it untouched."
} else {
    Write-Host "Starting FTTH first. Its launcher stops all nginx processes during startup, so chat starts after FTTH is ready."
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", "`"$FtthBat`"") -WorkingDirectory $FtthDir
    Wait-ForHttp "http://127.0.0.1/" 180 "FTTH" | Out-Null
}

Write-Host ""
Write-Host "Starting/restarting Ollama Chat on port 8082..."
Stop-ChatOnly
Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", $ChatScript) -WorkingDirectory $Root
Wait-ForHttp "http://127.0.0.1:8082/" 60 "Ollama Chat" | Out-Null

Write-Host ""
Write-Host "============================================================"
Write-Host "Both applications should now be available:"
Write-Host "FTTH App:     $FtthUrl"
Write-Host "Ollama Chat:  $ChatUrl"
Write-Host "Chat API:     $($ChatUrl)api/v1/"
Write-Host "============================================================"
Write-Host ""
Write-Host "Important: do not press a key in the FTTH window unless you want to stop FTTH."
Write-Host "The FTTH shutdown step stops all nginx processes, so it can also take chat offline."
Write-Host ""
Write-Host "Press any key in this combined launcher window to close this helper."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

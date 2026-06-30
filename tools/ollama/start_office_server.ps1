$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = if ($env:OLLAMA_CHAT_PORT) { [int]$env:OLLAMA_CHAT_PORT } else { 5100 }
$NginxPort = if ($env:OLLAMA_CHAT_NGINX_PORT) { [int]$env:OLLAMA_CHAT_NGINX_PORT } else { 8082 }
$Logs = Join-Path $Root "logs"
$Runtime = Join-Path $Root ".runtime"
$NginxPrefix = Join-Path $Runtime "nginx-chat"
$ServerLog = Join-Path $Logs "server.log"
$ServerErrLog = Join-Path $Logs "server.err.log"
$NginxLog = Join-Path $Logs "nginx.access.log"
$NginxErrLog = Join-Path $Logs "nginx.error.log"
$GeneratedNginxConf = Join-Path $Runtime "nginx-office.conf"

New-Item -ItemType Directory -Force -Path @(
    $Logs,
    $Runtime,
    $NginxPrefix,
    (Join-Path $NginxPrefix "logs"),
    (Join-Path $NginxPrefix "temp"),
    (Join-Path $NginxPrefix "temp\client_body_temp"),
    (Join-Path $NginxPrefix "temp\proxy_temp"),
    (Join-Path $NginxPrefix "temp\fastcgi_temp"),
    (Join-Path $NginxPrefix "temp\uwsgi_temp"),
    (Join-Path $NginxPrefix "temp\scgi_temp")
) | Out-Null

function Find-CommandPath {
    param([string]$Name)

    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\$Name.exe"),
        "C:\nginx\nginx.exe",
        "C:\Program Files\nginx\nginx.exe",
        "C:\Program Files\freenginx\nginx.exe"
    )

    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }

    $packages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $packages) {
        $found = Get-ChildItem $packages -Recurse -Filter "$Name.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    }

    return $null
}

function Ensure-Nginx {
    $nginx = Find-CommandPath "nginx"
    if ($nginx) { return $nginx }

    $winget = Find-CommandPath "winget"
    if (-not $winget) {
        throw "nginx is not installed and winget was not found. Install nginx, then run start_server.bat again."
    }

    Write-Host "nginx was not found. Installing nginx with winget..."
    & $winget install --id freenginx.nginx --exact --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install nginx. Install nginx manually, then run start_server.bat again."
    }

    $nginx = Find-CommandPath "nginx"
    if (-not $nginx) {
        throw "nginx installed, but nginx.exe was not found in this shell. Close this window and run start_server.bat again."
    }

    return $nginx
}

function ConvertTo-UInt32Ip {
    param([string]$Address)
    $bytes = [System.Net.IPAddress]::Parse($Address).GetAddressBytes()
    [array]::Reverse($bytes)
    return [BitConverter]::ToUInt32($bytes, 0)
}

function ConvertFrom-UInt32Ip {
    param([uint32]$Address)
    $bytes = [BitConverter]::GetBytes($Address)
    [array]::Reverse($bytes)
    return [System.Net.IPAddress]::new($bytes).ToString()
}

function Get-OfficeNetwork {
    $preferred = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.InterfaceAlias -notmatch "vEthernet|WSL|Loopback|Bluetooth" -and
            $_.PrefixLength -le 30
        } |
        Sort-Object @{ Expression = { if ($_.IPAddress -like "172.19.*") { 0 } else { 1 } } }, InterfaceMetric |
        Select-Object -First 1

    if (-not $preferred) {
        throw "Could not find an office IPv4 address. Connect to the office network and run start_server.bat again."
    }

    $ipNum = ConvertTo-UInt32Ip $preferred.IPAddress
    $mask = if ($preferred.PrefixLength -eq 0) { [uint32]0 } else { [uint32]([uint32]::MaxValue -shl (32 - $preferred.PrefixLength)) }
    $network = ConvertFrom-UInt32Ip ([uint32]($ipNum -band $mask))
    $allowedCidr = "$network/$($preferred.PrefixLength)"
    if ($preferred.IPAddress -like "172.*") {
        $allowedCidr = "172.16.0.0/12"
    } elseif ($preferred.IPAddress -like "10.*") {
        $allowedCidr = "10.0.0.0/8"
    } elseif ($preferred.IPAddress -like "192.168.*") {
        $allowedCidr = "192.168.0.0/16"
    }

    [pscustomobject]@{
        IP = $preferred.IPAddress
        Prefix = $preferred.PrefixLength
        CIDR = $allowedCidr
        Interface = $preferred.InterfaceAlias
    }
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

function Stop-ChildProcess {
    param($Process)
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

function Stop-PortListeners {
    param([int]$PortToStop)

    Get-NetTCPConnection -LocalPort $PortToStop -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
}

function Stop-ExistingNginx {
    param([string]$NginxPath, [string]$Prefix)
    try {
        & $NginxPath -p "$Prefix\" -c $GeneratedNginxConf -s stop 2>$null
        Start-Sleep -Seconds 1
    } catch {
    }

    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "nginx.exe" -and
            $_.CommandLine -and
            $_.CommandLine -like "*nginx-office.conf*"
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Write-NginxConfig {
    param(
        [string]$AllowedCidr,
        [string]$UploadsPath,
        [string]$AccessLog,
        [string]$ErrorLog,
        [string]$NginxRoot
    )

    $uploads = ($UploadsPath -replace "\\", "/").TrimEnd("/") + "/"
    $access = $AccessLog -replace "\\", "/"
    $errors = $ErrorLog -replace "\\", "/"
    $mimeTypes = (Join-Path $NginxRoot "conf\mime.types") -replace "\\", "/"

@"
worker_processes 1;

events {
    worker_connections 1024;
}

http {
    include       "$mimeTypes";
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 120s;

    access_log "$access";
    error_log  "$errors" warn;

    upstream flask_app {
        server 127.0.0.1:$Port;
        keepalive 32;
    }

    server {
        listen $NginxPort;
        server_name _;

        allow 127.0.0.1;
        allow $AllowedCidr;
        deny all;

        client_max_body_size 100M;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_connect_timeout 60s;

        location / {
            proxy_pass http://flask_app;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host `$host;
            proxy_set_header X-Real-IP `$remote_addr;
            proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto `$scheme;

            add_header Access-Control-Allow-Origin "*" always;
            add_header Access-Control-Allow-Methods "GET, POST, DELETE, PATCH, OPTIONS" always;
            add_header Access-Control-Allow-Headers "Content-Type, Authorization, X-API-Key" always;

            if (`$request_method = OPTIONS) {
                return 204;
            }
        }

        location /uploads/ {
            alias $uploads;
            expires 7d;
            add_header Cache-Control "public";
        }
    }
}
"@ | Set-Content -Path $GeneratedNginxConf -Encoding ASCII
}

function Ensure-FirewallRule {
    param([int]$PortToOpen)

    $ruleName = "Ollama Office Chat nginx $PortToOpen"
    try {
        $existing = & netsh advfirewall firewall show rule name="$ruleName" 2>$null
        if ($LASTEXITCODE -eq 0 -and ($existing -join "`n") -match $ruleName) {
            return
        }

        & netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow protocol=TCP localport=$PortToOpen profile=domain,private | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Windows Firewall rule added for TCP port $PortToOpen."
        } else {
            Write-Host "Could not add Windows Firewall rule automatically. Run this launcher as Administrator if office laptops cannot connect."
        }
    } catch {
        Write-Host "Could not add Windows Firewall rule automatically. Run this launcher as Administrator if office laptops cannot connect."
    }
}

$python = Find-CommandPath "python"
if (-not $python) { $python = Find-CommandPath "py" }
if (-not $python) {
    throw "Python was not found. Install Python or add it to PATH, then run start_server.bat again."
}

$nginx = Ensure-Nginx
$nginxRoot = Split-Path -Parent $nginx
$office = Get-OfficeNetwork
if ($env:OFFICE_ALLOWED_CIDR) {
    $office.CIDR = $env:OFFICE_ALLOWED_CIDR
}

Write-NginxConfig -AllowedCidr $office.CIDR -UploadsPath (Join-Path $Root "uploads") -AccessLog $NginxLog -ErrorLog $NginxErrLog -NginxRoot $nginxRoot

Write-Host ""
Write-Host "Office network detected: $($office.CIDR) on $($office.Interface)"
Stop-ExistingNginx -NginxPath $nginx -Prefix $NginxPrefix
Stop-PortListeners -PortToStop $Port
Write-Host "Starting Flask privately on http://127.0.0.1:$Port ..."
$env:OLLAMA_CHAT_HOST = "127.0.0.1"
$env:OLLAMA_CHAT_PORT = "$Port"
$server = Start-Process -FilePath $python -ArgumentList @("server.py") -WorkingDirectory $Root -RedirectStandardOutput $ServerLog -RedirectStandardError $ServerErrLog -WindowStyle Hidden -PassThru

if (-not (Wait-ForHttp "http://127.0.0.1:$Port/" 45)) {
    Write-Host "The Flask server did not answer yet. Last server log lines:"
    if (Test-Path $ServerLog) { Get-Content $ServerLog -Tail 40 }
    if (Test-Path $ServerErrLog) { Get-Content $ServerErrLog -Tail 40 }
    Stop-ChildProcess $server
    exit 1
}

Ensure-FirewallRule -PortToOpen $NginxPort
Write-Host "Starting nginx on office URL http://$($office.IP):$NginxPort/ ..."
& $nginx -p "$NginxPrefix\" -c $GeneratedNginxConf -t
if ($LASTEXITCODE -ne 0) {
    Write-Host "nginx configuration test failed. Last nginx error log lines:"
    if (Test-Path $NginxErrLog) { Get-Content $NginxErrLog -Tail 60 }
    Stop-ChildProcess $server
    exit 1
}

& $nginx -p "$NginxPrefix\" -c $GeneratedNginxConf
if ($LASTEXITCODE -ne 0) {
    Write-Host "nginx could not start. Last nginx error log lines:"
    if (Test-Path $NginxErrLog) { Get-Content $NginxErrLog -Tail 60 }
    Stop-ChildProcess $server
    exit 1
}

if (-not (Wait-ForHttp "http://127.0.0.1:$NginxPort/" 20)) {
    Write-Host "nginx started, but did not answer on port $NginxPort. Last nginx error log lines:"
    if (Test-Path $NginxErrLog) { Get-Content $NginxErrLog -Tail 60 }
    Stop-ChildProcess $server
    exit 1
}

Write-Host ""
Write-Host "============================================================"
Write-Host "Ollama Chat is running for the office network only"
Write-Host "Office URL: http://$($office.IP):$NginxPort/"
Write-Host "API URL:    http://$($office.IP):$NginxPort/api/v1/"
Write-Host "Allowed:    $($office.CIDR)"
Write-Host "Blocked:    non-office networks"
Write-Host "============================================================"
Write-Host ""
Write-Host "Logs:"
Write-Host "  Server: $ServerLog"
Write-Host "  Server errors: $ServerErrLog"
Write-Host "  nginx access: $NginxLog"
Write-Host "  nginx errors: $NginxErrLog"
Write-Host ""
Write-Host "Press Ctrl+C or close this window to stop the server and nginx."

try {
    while ($true) {
        if ($server.HasExited) {
            Write-Host "Server process stopped. Check $ServerLog"
            break
        }
        Start-Sleep -Seconds 2
    }
} finally {
    try { & $nginx -p "$NginxPrefix\" -c $GeneratedNginxConf -s stop 2>$null } catch {}
    Stop-ChildProcess $server
}

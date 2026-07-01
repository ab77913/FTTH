param(
    [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [switch]$StopDocker
)

$ErrorActionPreference = 'SilentlyContinue'

try {
    $ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path
} catch {
    $ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

function Stop-ProcessIdList {
    param(
        [int[]]$ProcessIds,
        [string]$Reason
    )

    $ProcessIds |
        Where-Object { $_ -and $_ -ne $PID } |
        Sort-Object -Unique |
        ForEach-Object {
            $proc = Get-Process -Id $_ -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host ("       stopping {0} pid={1} ({2})" -f $proc.ProcessName, $_, $Reason)
                Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
            }
        }
}

function Test-ProjectCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    return $CommandLine.IndexOf($ProjectDir, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

Write-Host "       FTTH stop helper project: $ProjectDir"

$portPids = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -and $_.OwningProcess -ne 0 } |
    Select-Object -ExpandProperty OwningProcess -Unique
Stop-ProcessIdList -ProcessIds $portPids -Reason "port 8000"

$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $cmd = [string]$_.CommandLine
        $inProject = Test-ProjectCommandLine $cmd
        (
            $inProject -and (
                $cmd -like '*api_server.py*' -or
                $cmd -like '*scripts\celery_dev_worker.py*' -or
                $cmd -like '*scripts/celery_dev_worker.py*' -or
                $cmd -like '*celery_worker.py*' -or
                $cmd -like '*data_ingestion.worker.celery_app*'
            )
        ) -or (
            $cmd -like '*data_ingestion.worker.celery_app*' -and
            $cmd -like '*ftth_pipeline@*'
        )
    }
Stop-ProcessIdList -ProcessIds ($processes | Select-Object -ExpandProperty ProcessId) -Reason "FTTH python service"

$nginxProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq 'nginx.exe' -and (
            $_.ExecutablePath -like 'C:\nginx\*' -or
            $_.CommandLine -like '*C:\nginx*'
        )
    }
Stop-ProcessIdList -ProcessIds ($nginxProcesses | Select-Object -ExpandProperty ProcessId) -Reason "FTTH nginx"

if ($StopDocker) {
    Push-Location -LiteralPath $ProjectDir
    try {
        docker compose stop redis rabbitmq | Out-Host
    } finally {
        Pop-Location
    }
}

Start-Sleep -Milliseconds 800

$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

Get-Content -LiteralPath (Join-Path $project 'start_app.bat') | ForEach-Object {
    if ($_ -match '^set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$') {
        Set-Item -Path ('Env:' + $matches[1]) -Value $matches[2].TrimEnd('"')
    }
}

$env:PROJECT_DIR = $project
$env:PYTHONPATH = $project

$python = Join-Path $project '.venv311\Scripts\python.exe'
Set-Location -LiteralPath $project
& $python api_server.py *> (Join-Path $project 'logs\api_server.log')

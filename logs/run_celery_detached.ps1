$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

Get-Content -LiteralPath (Join-Path $project 'start_app.bat') | ForEach-Object {
    if ($_ -match '^set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$') {
        Set-Item -Path ('Env:' + $matches[1]) -Value $matches[2].TrimEnd('"')
    }
}

$env:PROJECT_DIR = $project
$env:PYTHONPATH = $project
$env:FTTH_ENABLE_PADDLE_OCR = '0'
$env:FTTH_ENABLE_PADDLEOCR_SCAN = '0'

$python = Join-Path $project '.venv311\Scripts\python.exe'
Set-Location -LiteralPath $project
& $python -m celery -A data_ingestion.worker.celery_app worker --pool=solo --loglevel=warning --logfile=logs\celery_worker.log -n 'ftth_pipeline@%h'

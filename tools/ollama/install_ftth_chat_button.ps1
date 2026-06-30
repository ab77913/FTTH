$ErrorActionPreference = "Stop"

$FtthIndex = "C:\Users\Admin\FTTH_PRODUCTION\static\index.html"
$ChatPort = if ($env:OLLAMA_CHAT_NGINX_PORT) { [int]$env:OLLAMA_CHAT_NGINX_PORT } else { 8082 }

if (-not (Test-Path $FtthIndex)) {
    throw "FTTH UI file was not found: $FtthIndex"
}

$html = Get-Content -Path $FtthIndex -Raw
$chatUrlLine = '    const OLLAMA_CHAT_URL = `${window.location.protocol}//${window.location.hostname}:PORT/`;' -replace 'PORT', "$ChatPort"
$marker = "Open Ollama Chat"

if ($html -match [regex]::Escape($marker)) {
    $html = [regex]::Replace(
        $html,
        'const OLLAMA_CHAT_URL = `\$\{window\.location\.protocol\}//\$\{window\.location\.hostname\}:\d+/`;',
        ('const OLLAMA_CHAT_URL = `${window.location.protocol}//${window.location.hostname}:PORT/`;' -replace 'PORT', "$ChatPort")
    )
    Set-Content -Path $FtthIndex -Value $html -Encoding UTF8
    Write-Host "FTTH chat button already exists. Updated it to port $ChatPort."
    exit 0
}

$backup = Join-Path (Split-Path -Parent $FtthIndex) ("index.before-ollama-chat-button-{0}.html" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
Copy-Item -Path $FtthIndex -Destination $backup

$apiNeedle = "    const API = '/api';"
if ($html -notlike "*$apiNeedle*") {
    throw "Could not find API constant in FTTH index.html. Backup was created at: $backup"
}
$html = $html.Replace($apiNeedle, "$apiNeedle`r`n$chatUrlLine")

$chatButton = @'
              <button
                type="button"
                onClick={() => window.open(OLLAMA_CHAT_URL, '_blank', 'noopener,noreferrer')}
                className="app-button w-10 h-10 flex items-center justify-center hover:bg-slate-200 dark:hover:bg-dark-700 rounded-xl text-slate-500 dark:text-slate-300 border border-transparent hover:border-slate-300 dark:hover:border-dark-600"
                title="Open Ollama Chat">
                AI
                <span className="sr-only">Open Ollama Chat</span>
              </button>
'@

$settingsPattern = '(?m)^(?<indent>\s*)<button onClick=\{\(\) => setShowSettings\(true\)\} className="app-button w-10 h-10 flex items-center justify-center hover:bg-slate-200 dark:hover:bg-dark-700 rounded-xl text-slate-500 dark:text-slate-300 border border-transparent hover:border-slate-300 dark:hover:border-dark-600" title="API keys and tokens">.*?</button>\s*$'
$settingsMatch = [regex]::Match($html, $settingsPattern)
if (-not $settingsMatch.Success) {
    throw "Could not find the FTTH settings button. Backup was created at: $backup"
}

$html = $html.Substring(0, $settingsMatch.Index) +
    $settingsMatch.Value +
    "`r`n" +
    $chatButton +
    $html.Substring($settingsMatch.Index + $settingsMatch.Length)
Set-Content -Path $FtthIndex -Value $html -Encoding UTF8

Write-Host "Installed FTTH chat button."
Write-Host "Backup created:"
Write-Host "  $backup"
Write-Host ""
Write-Host "Button opens:"
Write-Host "  http://<same-host>:$ChatPort/"
Write-Host ""
Write-Host "Start both apps with:"
Write-Host "  C:\Users\Admin\Documents\ollama_launch\launch_both_apps.bat"

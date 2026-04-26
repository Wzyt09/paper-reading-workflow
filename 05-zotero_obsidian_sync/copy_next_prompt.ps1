param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot "config.json")
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = Join-Path $repoRoot "03-tools\pdf_tools\.venv\Scripts\python.exe"
$prep = Join-Path $PSScriptRoot "prepare_codex_prompts.py"
$queueDir = Join-Path $PSScriptRoot "manual_queue"

& $python $prep --config $ConfigPath --limit 1 | Out-Null

$nextPrompt = Get-ChildItem $queueDir -Filter *.prompt.txt | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $nextPrompt) {
    throw "No prompt files found in $queueDir"
}

$content = Get-Content -Raw $nextPrompt.FullName
Set-Clipboard -Value $content
Write-Host "Prompt copied to clipboard:`n$($nextPrompt.FullName)"

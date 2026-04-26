param(
  [string]$Python = "py"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root "03-tools\pdf_tools\.venv"
$PythonExe = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
  Write-Host "[setup] Creating virtual environment: $Venv"
  & $Python -3 -m venv $Venv
}

Write-Host "[setup] Upgrading pip"
& $PythonExe -m pip install --upgrade pip

Write-Host "[setup] Installing requirements"
& $PythonExe -m pip install -r (Join-Path $Root "requirements.txt")

$Config = Join-Path $Root "05-zotero_obsidian_sync\config.json"
$Example = Join-Path $Root "05-zotero_obsidian_sync\config.example.json"
if (-not (Test-Path $Config) -and (Test-Path $Example)) {
  Write-Host "[setup] Creating local config from config.example.json"
  Copy-Item $Example $Config
}

Write-Host "[setup] Done."
Write-Host "Next:"
Write-Host "  1. Edit 05-zotero_obsidian_sync\config.json"
Write-Host "  2. Set a model API key environment variable"
Write-Host "  3. Run 05-zotero_obsidian_sync\paper_sync_gui.cmd"

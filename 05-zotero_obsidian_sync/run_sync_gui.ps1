$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir ".."))
$guiScript = Join-Path $scriptDir "paper_sync_gui.py"
$stateDir = Join-Path $scriptDir ".state"
$launcherLog = Join-Path $stateDir "gui_launcher.log"

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

function Write-LauncherLog {
    param(
        [string]$Message
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $launcherLog -Value "[$timestamp] $Message" -Encoding UTF8
}

function Find-LocalUvPython {
    param(
        [string]$ExeName
    )

    $uvRoot = Join-Path $env:APPDATA "uv\python"
    if (-not (Test-Path $uvRoot)) {
        return $null
    }

    return Get-ChildItem -Path $uvRoot -Recurse -Filter $ExeName -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}

function Get-LauncherCandidates {
    return @(
        (Join-Path $workspaceRoot "03-tools\pdf_tools\.venv\Scripts\python.exe"),
        (Join-Path $workspaceRoot "03-tools\pdf_tools\.venv\Scripts\pythonw.exe"),
        (Find-LocalUvPython -ExeName "python.exe"),
        (Find-LocalUvPython -ExeName "pythonw.exe")
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
}

foreach ($launcher in Get-LauncherCandidates) {
    try {
        Write-LauncherLog "Trying launcher: $launcher"
        Push-Location $workspaceRoot
        try {
            & $launcher $guiScript
            $exitCode = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($exitCode -ne 0) {
            Write-LauncherLog "Launcher exited with error: $launcher (exit=$exitCode)"
            continue
        }
        Write-LauncherLog "GUI exited cleanly with: $launcher"
        exit 0
    } catch {
        Write-LauncherLog "Launcher failed: $launcher :: $($_.Exception.Message)"
    }
}

Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(
    "Paper Sync Manager 启动失败。`r`n请查看日志：`r`n$launcherLog",
    "Paper Sync Manager",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Error
) | Out-Null
exit 1

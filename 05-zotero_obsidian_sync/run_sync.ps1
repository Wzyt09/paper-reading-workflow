param(
    [ValidateSet("Once", "Watch", "SetupBbt")]
    [string]$Mode = "Once",

    [string]$ConfigPath = (Join-Path $PSScriptRoot "config.json"),

    [int]$IntervalSeconds = 0
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvPython = Join-Path $repoRoot "03-tools\pdf_tools\.venv\Scripts\python.exe"
$scriptPath = Join-Path $PSScriptRoot "sync_pipeline.py"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Python not found at $venvPython"
}

$args = @($scriptPath)
switch ($Mode) {
    "Watch" {
        $args += "watch"
        if ($IntervalSeconds -gt 0) {
            $args += @("--interval", $IntervalSeconds.ToString())
        }
    }
    "SetupBbt" {
        $args += "setup-bbt"
    }
    default {
        $args += "once"
    }
}

$args += @("--config", $ConfigPath)

& $venvPython @args
exit $LASTEXITCODE

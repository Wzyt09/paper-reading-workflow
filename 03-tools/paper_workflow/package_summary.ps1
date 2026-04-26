param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$SummaryMd,

    [Parameter(Mandatory = $true)]
    [string[]]$PdfPaths,

    [string]$SpecPath,

    [string]$OutDir,

    [int]$Dpi = 170,

    [switch]$Clean,

    [switch]$AppendAutoMaterials
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$toolsRoot = Join-Path $repoRoot "03-tools"
if (-not (Test-Path -LiteralPath $toolsRoot)) {
    $toolsRoot = Join-Path $repoRoot "tools"
}
$venvPython = Join-Path $toolsRoot "pdf_tools\.venv\Scripts\python.exe"
$scriptPath = Join-Path $scriptDir "package_summary.py"

$args = @($scriptPath, $SummaryMd, "--dpi", $Dpi.ToString(), "--pdf")
$args += $PdfPaths
if ($OutDir) {
    $args += @("--outdir", $OutDir)
}
if ($SpecPath) {
    $args += @("--spec", $SpecPath)
}
if ($Clean) {
    $args += "--clean"
}
if ($AppendAutoMaterials) {
    $args += "--append-auto-materials"
}

if (Test-Path -LiteralPath $venvPython) {
    & $venvPython @args
} else {
    python @args
}

exit $LASTEXITCODE

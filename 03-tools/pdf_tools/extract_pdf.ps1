param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$PdfPath,

    [Parameter(Position = 1)]
    [string]$OutDir,

    [int]$Dpi = 170,

    [switch]$Clean,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $scriptDir

$uvArgs = @("run", "python", ".\\extract_pdf.py", $PdfPath, "--dpi", $Dpi.ToString())
if ($OutDir) {
    $uvArgs += @("--outdir", $OutDir)
}
if ($Clean) {
    $uvArgs += "--clean"
}
if ($ExtraArgs) {
    $uvArgs += $ExtraArgs
}

& uv @uvArgs
$exitCode = $LASTEXITCODE

Pop-Location
exit $exitCode

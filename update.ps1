[CmdletBinding()]
param(
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not $SkipPull) {
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "git pull --ff-only failed. Commit or stash conflicting local changes, then retry."
    }
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the Python 3.12 virtual environment."
    }
}

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}

& $python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "Project installation failed."
}

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
}

Write-Host "Project update complete." -ForegroundColor Green
Write-Host "Start the app with:"
Write-Host "  .\.venv\Scripts\python.exe -m uvicorn stage0_sim.api.app:app --reload"

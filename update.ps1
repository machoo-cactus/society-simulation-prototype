[CmdletBinding()]
param(
    [switch]$Pull,
    [string]$Python = $env:PYTHON
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if ($Pull) {
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "git pull --ff-only failed. Commit or stash conflicting local changes, then retry."
    }
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    $candidates = [System.Collections.Generic.List[object]]::new()
    if ($Python) {
        $candidates.Add([pscustomobject]@{ Command = $Python; Arguments = @() })
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in "3.14", "3.13", "3.12") {
            $candidates.Add(
                [pscustomobject]@{
                    Command = "py"
                    Arguments = @("-$version")
                }
            )
        }
    }
    foreach ($name in "python3.14", "python3.13", "python3.12", "python3", "python") {
        if (Get-Command $name -ErrorAction SilentlyContinue) {
            $candidates.Add([pscustomobject]@{ Command = $name; Arguments = @() })
        }
    }

    $selected = $null
    foreach ($candidate in $candidates) {
        try {
            $candidateArguments = $candidate.Arguments
            & $candidate.Command @candidateArguments -c `
                "import sys; raise SystemExit(sys.version_info < (3, 12))"
            if ($LASTEXITCODE -eq 0) {
                $selected = $candidate
                break
            }
        }
        catch {
            continue
        }
    }
    if ($null -eq $selected) {
        throw "Python 3.12 or newer is required. Set PYTHON to an interpreter path."
    }

    $selectedArguments = $selected.Arguments
    & $selected.Command @selectedArguments -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the Python 3.12-or-newer virtual environment."
    }
}

& $venvPython -c "import sys; raise SystemExit(sys.version_info < (3, 12))"
if ($LASTEXITCODE -ne 0) {
    throw ".venv must use Python 3.12 or newer."
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}

& $venvPython -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "Project installation failed."
}

New-Item -ItemType Directory -Force `
    "data\characters", "data\scenarios", "data\elements", "data\runs" |
    Out-Null

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
}

Write-Host "Environment refresh complete." -ForegroundColor Green
if (-not $Pull) {
    Write-Host "Source was not pulled; rerun with -Pull for git pull --ff-only."
}
Write-Host "Start the app with:"
Write-Host "  .\.venv\Scripts\python.exe -m uvicorn stage0_sim.api.app:app --reload"

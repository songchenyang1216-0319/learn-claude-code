[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "== learn-claude-code Windows setup ==" -ForegroundColor Cyan

$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $python = @("py", "-3.12")
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python 3.12 was not found. Install it from https://www.python.org/downloads/windows/"
    }
    $version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($version -ne "3.12") {
        throw "Python 3.12 is required, but python points to $version."
    }
    $python = @("python")
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    if ($python.Count -eq 2) {
        & $python[0] $python[1] -m venv .venv
    } else {
        & $python[0] -m venv .venv
    }
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "Installing dependencies..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
$env:PYTHONUTF8 = "1"

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example." -ForegroundColor Yellow
}

Write-Host "Checking Bash..." -ForegroundColor Yellow
& $venvPython -c "from shell_runner import find_bash; p=find_bash(); assert p, 'Git Bash not found'; print(f'Bash: {p}')"

if (-not $SkipTests) {
    Write-Host "Running tests..." -ForegroundColor Yellow
    & $venvPython -m unittest discover -s tests -p "test_*.py"
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "1. Open .env and enter your SenseNova API key."
Write-Host "2. Start the first lesson with:"
Write-Host "   .\.venv\Scripts\python.exe s01_agent_loop\code.py" -ForegroundColor Cyan

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (!(Test-Path ".venv")) {
  python -m venv .venv
}

. .venv/Scripts/Activate.ps1
pip install -e ".[dev]"

python -m sbh_demo


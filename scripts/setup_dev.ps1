$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venv = Join-Path $project ".venv"
if (-not (Test-Path $venv)) { py -3.12 -m venv $venv }
& (Join-Path $venv "Scripts\python.exe") -m pip install --disable-pip-version-check -e "${project}[deploy,test]"

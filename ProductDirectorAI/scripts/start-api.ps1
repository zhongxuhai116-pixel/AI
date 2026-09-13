$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw 'Python environment is missing. Run scripts/setup.ps1 first.'
}
. (Join-Path $PSScriptRoot 'load-env.ps1')
Set-Location -LiteralPath $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot 'apps\api'
& $python -m uvicorn productdirector_api.main:app --host 127.0.0.1 --port 8000

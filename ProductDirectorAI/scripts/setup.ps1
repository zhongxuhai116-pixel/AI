$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundledPython = 'C:\Users\dell\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$bundledNpm = 'C:\Users\dell\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\npm.cmd'
$python = if (Test-Path -LiteralPath $bundledPython) { $bundledPython } else { (Get-Command python -ErrorAction Stop).Source }
$npm = if (Test-Path -LiteralPath $bundledNpm) { $bundledNpm } else { (Get-Command npm -ErrorAction Stop).Source }
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
  & $python -m venv (Join-Path $projectRoot '.venv')
}
& $venvPython -m pip install -r (Join-Path $projectRoot 'apps\api\requirements.txt')
Set-Location -LiteralPath (Join-Path $projectRoot 'apps\web')
& $npm install
Write-Output 'Setup complete. Blender and FFmpeg availability will be shown on the Settings page.'

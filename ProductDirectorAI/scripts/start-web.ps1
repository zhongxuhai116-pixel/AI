$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$node = (Get-Command node -ErrorAction Stop).Source
. (Join-Path $PSScriptRoot 'load-env.ps1')
$vite = Join-Path $projectRoot 'apps\web\node_modules\vite\bin\vite.js'
if (-not (Test-Path -LiteralPath $vite)) {
  throw 'Web dependencies are missing. Run scripts/setup.ps1 first.'
}
Set-Location -LiteralPath (Join-Path $projectRoot 'apps\web')
& $node $vite --host 127.0.0.1 --port 4173 --strictPort

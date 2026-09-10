$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundledNode = 'C:\Users\dell\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$node = if (Test-Path -LiteralPath $bundledNode) { $bundledNode } else { (Get-Command node -ErrorAction Stop).Source }
$vite = Join-Path $projectRoot 'apps\web\node_modules\vite\bin\vite.js'
if (-not (Test-Path -LiteralPath $vite)) {
  throw 'Web dependencies are missing. Run scripts/setup.ps1 first.'
}
Set-Location -LiteralPath (Join-Path $projectRoot 'apps\web')
& $node $vite --host 127.0.0.1 --port 4173 --strictPort

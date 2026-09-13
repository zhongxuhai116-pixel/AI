$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$missing = @()
foreach ($tool in @('node','npm.cmd','ffmpeg','ffprobe','blender')) {
  $command = Get-Command $tool -ErrorAction SilentlyContinue
  if ($command) { Write-Output "PASS $tool" } else { Write-Output "MISSING $tool"; $missing += $tool }
}
$python = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $python) {
  & $python -c "import fastapi,uvicorn,PIL,numpy,OpenEXR; print('PASS API imports')"
  if ($LASTEXITCODE -ne 0) { $missing += 'API dependencies' }
} else { $missing += 'Python venv' }
. (Join-Path $PSScriptRoot 'load-env.ps1')
if ($env:PRODUCTDIRECTOR_OWNER_TOKEN.Length -lt 32) { $missing += 'Owner key (run setup.ps1)' }
if ($missing.Count) { throw ('Not ready: ' + ($missing -join ', ')) }
Write-Output 'Local prerequisites passed. Provider readiness still requires a real image/video generation check.'

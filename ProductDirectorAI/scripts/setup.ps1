param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& $Python -c "import sys; assert (3,10) <= sys.version_info[:2] <= (3,12), 'Use Python 3.10-3.12'"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python; see docs/INSTALL.md.' }
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
& node -e "if(![22,24].includes(Number(process.versions.node.split('.')[0]))) process.exit(1)"
if ($LASTEXITCODE -ne 0) { throw 'Install Node.js 22.x or 24.x before setup.' }
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
  & $Python -m venv (Join-Path $projectRoot '.venv')
  if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
& $venvPython -m pip install -r (Join-Path $projectRoot 'apps\api\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Python dependency conflict' }
Push-Location (Join-Path $projectRoot 'apps\web')
try {
  & $npm ci
  if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
  & $npm run build
  if ($LASTEXITCODE -ne 0) { throw 'Web build failed' }
} finally { Pop-Location }
$config = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $config)) {
  & $venvPython -c "import secrets,pathlib,sys; pathlib.Path(sys.argv[1]).write_text('PRODUCTDIRECTOR_OWNER_TOKEN='+secrets.token_urlsafe(36)+'\nPRODUCTDIRECTOR_WORKER_TOKEN='+secrets.token_urlsafe(36)+'\nPRODUCTDIRECTOR_COMFYUI_URL=http://127.0.0.1:8188\n',encoding='utf-8')" $config
  if ($LASTEXITCODE -ne 0) { throw 'Local configuration creation failed' }
}
Write-Output 'Installed. Local login key is in .env; keep this file private. Run scripts/start-api.ps1 and scripts/start-web.ps1.'

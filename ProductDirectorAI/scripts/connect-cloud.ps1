param(
  [Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+$')][string]$Server,
  [Parameter(Mandatory=$true)][string]$IdentityFile,
  [ValidateRange(1024,65535)][int]$LocalPort = 4173
)
$ErrorActionPreference = 'Stop'
$keyPath = (Resolve-Path -LiteralPath $IdentityFile).Path
Write-Output "Keep this window open, then visit http://127.0.0.1:$LocalPort/"
& ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -i $keyPath -L "127.0.0.1:${LocalPort}:127.0.0.1:4173" $Server
if ($LASTEXITCODE -ne 0) { throw 'SSH tunnel failed. Check SSH access, firewall and local port.' }

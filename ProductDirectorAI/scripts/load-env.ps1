$configPath = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
if (Test-Path -LiteralPath $configPath) {
  foreach ($line in Get-Content -LiteralPath $configPath -Encoding UTF8) {
    if ($line -match '^\s*(PRODUCTDIRECTOR_[A-Z0-9_]+|PD_H3_[A-Z0-9_]+)=(.*)$') {
      $name = $Matches[1]; $value = $Matches[2].Trim()
      if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
      }
    }
  }
}

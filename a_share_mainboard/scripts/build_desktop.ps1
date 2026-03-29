param(
    [string]$PythonExe = "python",
    [string]$AppName = "AShareTradingAgentsLite"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EntryScript = Join-Path $ProjectRoot "scripts\run_desktop.py"
$DistRoot = Join-Path $ProjectRoot "dist"
$BuildRoot = Join-Path $ProjectRoot "build"

Write-Host "Project root: $ProjectRoot"
Write-Host "Installing desktop packager dependencies..."
& $PythonExe -m pip install --upgrade pyinstaller

if (Test-Path $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}

if (Test-Path (Join-Path $DistRoot $AppName)) {
    Remove-Item -LiteralPath (Join-Path $DistRoot $AppName) -Recurse -Force
}

Write-Host "Building desktop app..."
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name $AppName `
    --paths $ProjectRoot `
    --paths (Join-Path $ProjectRoot "src") `
    --add-data "$ProjectRoot\config;config" `
    --add-data "$ProjectRoot\src\ai\prompts;src\ai\prompts" `
    $EntryScript

$AppRoot = Join-Path $DistRoot $AppName
$DataRoot = Join-Path $AppRoot "data"
New-Item -ItemType Directory -Path (Join-Path $DataRoot "raw") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $DataRoot "ods") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $DataRoot "mart") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $DataRoot "logs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $DataRoot "reports") -Force | Out-Null

if (Test-Path (Join-Path $ProjectRoot ".env.example")) {
    Copy-Item `
        -LiteralPath (Join-Path $ProjectRoot ".env.example") `
        -Destination (Join-Path $AppRoot ".env.example") `
        -Force
}

Write-Host "Desktop package ready: $AppRoot"

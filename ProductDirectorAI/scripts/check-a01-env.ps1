param(
    [string]$ReportPath = ".\docs\reports\A01_ENVIRONMENT_BASELINE.md"
)

$ErrorActionPreference = "Continue"

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedReport = Join-Path $projectRoot $ReportPath
$report = New-Object System.Collections.Generic.List[string]

function Append([string]$Text) {
    $report.Add($Text)
    Write-Output $Text
}

function Find-Command([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Command-Version([string]$CmdPath) {
    if (-not $CmdPath) { return "NOT FOUND" }
    try {
        & $CmdPath --version 2>$null | Select-Object -First 1
    }
    catch {
        "ERR: $($_.Exception.Message)"
    }
}

Append "# A01 环境基线快照（运行生成）"
Append ""
Append "生成时间：$timestamp"
Append ""

# A01-01
$pythonPath = $null
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) { $pythonPath = $venvPython }
if (-not $pythonPath) {
    $pythonPath = Find-Command "python"
}

if ($pythonPath) {
    Append "Python: $pythonPath"
    try {
        $pythonVersion = & $pythonPath --version 2>&1
        Append "Python 版本: $pythonVersion"
    }
    catch {
        Append "Python 版本: ERR"
    }
    Append "python -m pip 版本: $((Command-Version $pythonPath))"
    try {
        & $pythonPath -m pip install -r (Join-Path $projectRoot 'apps\api\requirements.txt') | Out-Null
        Append "requirements 安装: PASS"
    }
    catch {
        Append "requirements 安装: FAIL"
    }
    try {
        & $pythonPath -m compileall (Join-Path $projectRoot 'apps\api') | Out-Null
        Append "compileall apps/api: PASS"
    }
    catch {
        Append "compileall apps/api: FAIL"
    }
}
else {
    Append "Python: NOT FOUND"
}

# Tools
Append ""

$npmPath = Find-Command "npm"
if (-not $npmPath) {
    $npmPath = "C:\Users\dell\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\npm.cmd"
    if (-not (Test-Path $npmPath)) { $npmPath = $null }
}
Append ("npm: " + ($(if ($npmPath) { $npmPath } else { "NOT FOUND" })))
Append ("npx: " + ($(if (Find-Command "npx") { Find-Command "npx" } else { "NOT FOUND" })))

$nodePath = Find-Command "node"
Append ("node: " + ($(if ($nodePath) { $nodePath } else { "NOT FOUND" })))
if ($nodePath) {
    Append ("node 版本: " + (Command-Version $nodePath))
}
else {
    Append "node 版本: NOT FOUND"
}

$blenderPath = Find-Command "blender"
if ($blenderPath) {
    Append "blender: $blenderPath"
    Append ("blender 版本: " + (Command-Version $blenderPath))
} else {
    Append "blender: NOT FOUND"
}

$ffmpegPath = Find-Command "ffmpeg"
if ($ffmpegPath) {
    Append "ffmpeg: $ffmpegPath"
    Append ("ffmpeg 版本: " + (Command-Version $ffmpegPath))
} else {
    Append "ffmpeg: NOT FOUND"
}

$ffprobePath = Find-Command "ffprobe"
if ($ffprobePath) {
    Append "ffprobe: $ffprobePath"
    Append ("ffprobe 版本: " + (Command-Version $ffprobePath))
} else {
    Append "ffprobe: NOT FOUND"
}

Append ""
Append "依赖文件:"
Append ("apps/web/package.json: " + (Test-Path (Join-Path $projectRoot 'apps\web\package.json')))
Append ("apps/web/package-lock.json: " + (Test-Path (Join-Path $projectRoot 'apps\web\package-lock.json')))
Append ("apps/web/node_modules: " + (Test-Path (Join-Path $projectRoot 'apps\web\node_modules')))

if ($npmPath) {
    Push-Location (Join-Path $projectRoot 'apps\web')
    try {
        $buildResult = & $npmPath run build 2>&1
        Append ""
        if ($LASTEXITCODE -eq 0) {
            Append "npm run build: PASS（命令返回 0）"
            Append (($buildResult | Select-Object -First 2 | Out-String).Trim())
        } else {
            Append "npm run build: FAIL（命令返回码 $LASTEXITCODE）"
            Append ($buildResult | Select-Object -First 5 | Out-String).Trim()
        }

        $sitesResult = & $npmPath run test:sites 2>&1
        Append ""
        if ($LASTEXITCODE -eq 0) {
            Append "npm run test:sites: PASS（命令返回 0）"
            Append (($sitesResult | Select-Object -First 2 | Out-String).Trim())
        } else {
            Append "npm run test:sites: FAIL（命令返回码 $LASTEXITCODE）"
            Append ($sitesResult | Select-Object -First 10 | Out-String).Trim()
        }
    }
    catch {
        Append ""
        Append "npm build/sites: FAIL（需人工复核）"
        Append $_.Exception.Message
    }
    finally {
        Pop-Location
    }
}

New-Item -ItemType Directory -Path (Split-Path $resolvedReport -Parent) -Force | Out-Null
Set-Content -Path $resolvedReport -Value ($report -join "`r`n") -Encoding UTF8
Write-Output ("A01 snapshot written: " + $resolvedReport)

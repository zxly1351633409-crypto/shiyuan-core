param(
    [string]$UserRoot = $env:USERPROFILE
)

$ErrorActionPreference = 'Stop'

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$archiveRoot = Join-Path $UserRoot ".shiyuan-company-disabled\$timestamp"
New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null

$companyRoot = Join-Path $UserRoot '.shiyuan-company'
$bridgeWork = Join-Path $companyRoot 'state\work'
$bridgePidPath = Join-Path $bridgeWork 'live-activity.pid'
$bridgeStopPath = Join-Path $bridgeWork 'live-activity.stop'
if (Test-Path -LiteralPath $bridgePidPath) {
    $bridgePid = 0
    [int]::TryParse((Get-Content -LiteralPath $bridgePidPath -Raw).Trim(), [ref]$bridgePid) | Out-Null
    [IO.File]::WriteAllText($bridgeStopPath, 'stop', [Text.UTF8Encoding]::new($false))
    for ($attempt = 0; $attempt -lt 24 -and (Test-Path -LiteralPath $bridgePidPath); $attempt++) {
        Start-Sleep -Milliseconds 250
    }
    if ((Test-Path -LiteralPath $bridgePidPath) -and $bridgePid -gt 0) {
        $ownedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $bridgePid" -ErrorAction SilentlyContinue
        if ($ownedProcess -and $ownedProcess.CommandLine -match 'live_activity_bridge\.py' -and $ownedProcess.CommandLine -like "*$companyRoot*") {
            Stop-Process -Id $bridgePid -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 300
        }
    }
}

$configPath = Join-Path $UserRoot '.codex\config.toml'
if (Test-Path -LiteralPath $configPath) {
    Copy-Item -LiteralPath $configPath -Destination (Join-Path $archiveRoot 'config.toml.pre-uninstall.bak')
    $config = Get-Content -LiteralPath $configPath -Raw
    $markerPattern = '(?ms)^# BEGIN SHIYUAN_COMPANY_SAFE\r?\n.*?^# END SHIYUAN_COMPANY_SAFE\r?\n?'
    $updated = [regex]::Replace($config, $markerPattern, '').TrimEnd() + "`r`n"
    [IO.File]::WriteAllText($configPath, $updated, [Text.UTF8Encoding]::new($false))
}

$ownedTargets = @(
    $companyRoot,
    (Join-Path $UserRoot '.hanako\plugins\shiyuan-company-safe')
)
foreach ($target in $ownedTargets) {
    if (-not (Test-Path -LiteralPath $target)) { continue }
    $resolved = (Resolve-Path -LiteralPath $target).Path
    $profileRoot = (Resolve-Path -LiteralPath $UserRoot).Path
    if (-not $resolved.StartsWith($profileRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝移动用户目录外的路径：$resolved"
    }
    Move-Item -LiteralPath $resolved -Destination $archiveRoot
}

Write-Host "公司安全版已从配置中停用，组件归档到：$archiveRoot"
Write-Host '交接箱没有删除；如需处理其中内容，请先按公司制度确认。重启 Codex/Hana 后生效。'

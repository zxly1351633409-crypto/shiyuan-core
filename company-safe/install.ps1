param(
    [switch]$Codex,
    [switch]$Hana,
    [string]$UserRoot = $env:USERPROFILE,
    [string]$DocumentsRoot = [Environment]::GetFolderPath('MyDocuments'),
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'

if (-not $Codex -and -not $Hana) {
    $Codex = $true
    $Hana = Test-Path -LiteralPath (Join-Path $UserRoot '.hanako')
}

$packageRoot = $PSScriptRoot
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$installRoot = Join-Path $UserRoot '.shiyuan-company'
$stateRoot = Join-Path $installRoot 'state'
$outbox = Join-Path $DocumentsRoot '十元交接箱'

$verifyScript = Join-Path $packageRoot 'verify-package.ps1'
if (-not (Test-Path -LiteralPath $verifyScript)) {
    throw '安装包缺少 verify-package.ps1，拒绝继续。'
}
& $verifyScript -PackageRoot $packageRoot

New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outbox -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $packageRoot 'VERSION.txt') -Destination (Join-Path $installRoot 'VERSION.txt') -Force
Copy-Item -LiteralPath (Join-Path $packageRoot 'company-policy.md') -Destination (Join-Path $installRoot 'company-policy.md') -Force
Copy-Item -LiteralPath (Join-Path $packageRoot 'confirmed-memory.md') -Destination (Join-Path $installRoot 'confirmed-memory.md') -Force

$installedBodies = [System.Collections.Generic.List[string]]::new()
$codexBackup = ''

if ($Codex) {
    if (-not $PythonPath) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCommand) { $PythonPath = $pythonCommand.Source }
    }
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
        throw '未找到 python.exe；公司安全版 Codex MCP 需要 Python 3.10+。请先让 IT 安装或只运行 .\install.ps1 -Hana。'
    }
    & $PythonPath -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
    if ($LASTEXITCODE -ne 0) {
        throw 'Python 版本低于 3.10；请先让 IT 升级或只安装 Hana。'
    }
    $codexSource = Join-Path $packageRoot 'codex'
    $codexTarget = Join-Path $installRoot 'codex'
    New-Item -ItemType Directory -Path $codexTarget -Force | Out-Null
    Copy-Item -Path (Join-Path $codexSource '*') -Destination $codexTarget -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $packageRoot 'company-policy.md') -Destination (Join-Path $codexTarget 'company-policy.md') -Force
    Copy-Item -LiteralPath (Join-Path $packageRoot 'confirmed-memory.md') -Destination (Join-Path $codexTarget 'confirmed-memory.md') -Force

    $codexHome = Join-Path $UserRoot '.codex'
    $configPath = Join-Path $codexHome 'config.toml'
    New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
    $config = if (Test-Path -LiteralPath $configPath) { Get-Content -LiteralPath $configPath -Raw } else { '' }
    if (Test-Path -LiteralPath $configPath) {
        $codexBackup = "$configPath.$timestamp.pre-shiyuan-company.bak"
        Copy-Item -LiteralPath $configPath -Destination $codexBackup
    }
    $markerPattern = '(?ms)^# BEGIN SHIYUAN_COMPANY_SAFE\r?\n.*?^# END SHIYUAN_COMPANY_SAFE\r?\n?'
    $config = [regex]::Replace($config, $markerPattern, '').TrimEnd()
    $tomlPython = $PythonPath.Replace("'", "''")
    $tomlMcp = (Join-Path $codexTarget 'company_mcp.py').Replace("'", "''")
    $tomlHook = (Join-Path $codexTarget 'company_hook.py').Replace("'", "''")
    $tomlOutbox = $outbox.Replace("'", "''")
    $tomlState = $stateRoot.Replace("'", "''")
    $hookStartCommand = ('"{0}" "{1}" SessionStart "{2}"' -f $PythonPath, (Join-Path $codexTarget 'company_hook.py'), $stateRoot).Replace("'", "''")
    $hookPromptCommand = ('"{0}" "{1}" UserPromptSubmit "{2}"' -f $PythonPath, (Join-Path $codexTarget 'company_hook.py'), $stateRoot).Replace("'", "''")
    $hookStopCommand = ('"{0}" "{1}" Stop "{2}"' -f $PythonPath, (Join-Path $codexTarget 'company_hook.py'), $stateRoot).Replace("'", "''")
    $block = @"
# BEGIN SHIYUAN_COMPANY_SAFE
[mcp_servers.shiyuan_company_safe]
command = '$tomlPython'
args = ['$tomlMcp']
startup_timeout_sec = 15
enabled = true

[mcp_servers.shiyuan_company_safe.env]
SHIYUAN_COMPANY_OUTBOX = '$tomlOutbox'
SHIYUAN_COMPANY_STATE = '$tomlState'

[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"

[[hooks.SessionStart.hooks]]
type = "command"
command = '$hookStartCommand'
timeout = 5
additionalContextLimit = 12000

[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = '$hookPromptCommand'
timeout = 5
additionalContextLimit = 12000

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = '$hookStopCommand'
timeout = 5
# END SHIYUAN_COMPANY_SAFE
"@
    $updated = ($config + "`r`n`r`n" + $block.Trim() + "`r`n").TrimStart()
    $temporary = "$configPath.tmp"
    [IO.File]::WriteAllText($temporary, $updated, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $configPath -Force
    $installedBodies.Add('Codex')
    Write-Host 'Codex 公司安全模式已安装。首次启动新任务时请核对并信任 Hook。'
}

if ($Hana) {
    $hanaSource = Join-Path $packageRoot 'hana'
    $hanaHome = Join-Path $UserRoot '.hanako'
    $hanaTarget = Join-Path $hanaHome 'plugins\shiyuan-company-safe'
    New-Item -ItemType Directory -Path $hanaTarget -Force | Out-Null
    Copy-Item -Path (Join-Path $hanaSource '*') -Destination $hanaTarget -Recurse -Force
    $serverInfoPath = Join-Path $hanaHome 'server-info.json'
    if (Test-Path -LiteralPath $serverInfoPath) {
        try {
            $serverInfo = Get-Content -LiteralPath $serverInfoPath -Raw | ConvertFrom-Json
            $headers = @{ Authorization = "Bearer $($serverInfo.token)" }
            $body = @{ path = $hanaSource; allowDowngrade = $false } | ConvertTo-Json
            Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($serverInfo.port)/api/plugins/install" -Headers $headers -ContentType 'application/json' -Body $body | Out-Null
            Write-Host 'Hana 公司安全模式已通过本地 Hana Server 加载。'
        } catch {
            Write-Warning 'Hana 本地接口未加载插件；文件已经复制，请重启 Hana 后检查插件列表。'
        }
    } else {
        Write-Host 'Hana 插件文件已复制；启动或重启 Hana 后加载。'
    }
    $installedBodies.Add('Hana')
}

$reportPath = Join-Path $installRoot 'INSTALL_REPORT.md'
$report = @"
# 十元公司安全版安装报告

- 时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')
- 版本：$((Get-Content -LiteralPath (Join-Path $packageRoot 'VERSION.txt') -Raw).Trim())
- 身体：$($installedBodies -join '、')
- 模式：公司安全离线模式
- 家庭 Core：未连接
- 自动上传：未配置
- 可见对话本地归档：开启（凭据脱敏）
- 私有推理保存：关闭
- 本地增量记忆：$stateRoot
- 交接箱：$outbox
- Codex 配置备份：$(if ($codexBackup) { $codexBackup } else { '未安装 Codex' })

安装脚本已完成文件校验。请重启对应应用并新建会话，确认回复末尾出现 `🐳 十元·公司安全模式`。
"@
[IO.File]::WriteAllText($reportPath, $report.Trim() + "`r`n", [Text.UTF8Encoding]::new($false))

Write-Host "本地交接箱：$outbox"
Write-Host "本地增量记忆：$stateRoot"
Write-Host "安装报告：$reportPath"
Write-Host '安装包没有配置任何家庭 NAS、Core Token 或企业微信自动上传。'

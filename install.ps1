[CmdletBinding()]
param(
    [ValidateSet('Personal', 'Company')]
    [string]$Mode = 'Personal',
    [string]$AssistantName = '',
    [switch]$Codex,
    [switch]$Hana,
    [switch]$ReplaceCompanyMode,
    [switch]$SkipCoreStart,
    [switch]$SkipDependencyInstall,
    [string]$CoreUrl = 'http://127.0.0.1:8710',
    [string]$CoreToken = '',
    [string]$UserRoot = $env:USERPROFILE,
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$newline = [Environment]::NewLine

if (-not $Codex -and -not $Hana) {
    $Codex = $true
    $Hana = Test-Path -LiteralPath (Join-Path $UserRoot '.hanako')
}

if ($Mode -eq 'Company') {
    Write-Host '即将安装公司安全离线模式；它不会连接个人 Core 或家庭 NAS。' -ForegroundColor Yellow
    $companyInstaller = Join-Path $repoRoot 'company-safe\install.ps1'
    if (-not (Test-Path -LiteralPath $companyInstaller)) {
        throw '当前发行版不包含公司安全模式。'
    }
    $companyArguments = @{
        UserRoot = $UserRoot
        PythonPath = $PythonPath
    }
    if ($Codex) {
        $companyArguments.Codex = $true
    }
    if ($Hana) {
        $companyArguments.Hana = $true
    }
    & $companyInstaller @companyArguments
    exit $LASTEXITCODE
}

if (-not $AssistantName -and -not [Console]::IsInputRedirected) {
    $AssistantName = Read-Host '请给你的个人助手起一个名字'
}
if (-not $AssistantName) {
    $AssistantName = '我的助手'
}
$AssistantName = $AssistantName.Trim()
if ($AssistantName.Length -lt 1 -or $AssistantName.Length -gt 32 -or $AssistantName -match '[\r\n<>:"/\\|?*]') {
    throw '助手名称必须为 1-32 个字符，且不能包含换行或 Windows 文件名保留字符。'
}

try {
    $coreUri = [Uri]$CoreUrl
} catch {
    throw 'CoreUrl 不是有效 URL。'
}
if ($coreUri.Scheme -notin @('http', 'https')) {
    throw 'CoreUrl 只允许 http 或 https。'
}
$isLocalCore = $coreUri.Host -in @('127.0.0.1', 'localhost', '::1')

$codexConfig = Join-Path $UserRoot '.codex\config.toml'
$companyMarker = '(?ms)^# BEGIN SHIYUAN_COMPANY_SAFE\r?\n.*?^# END SHIYUAN_COMPANY_SAFE\r?\n?'
$existingCodexConfig = if (Test-Path -LiteralPath $codexConfig) {
    Get-Content -LiteralPath $codexConfig -Raw
} else {
    ''
}
if ($existingCodexConfig -match $companyMarker) {
    if (-not $ReplaceCompanyMode) {
        throw '检测到十元公司安全模式。请先备份并卸载它，或重新运行并添加 -ReplaceCompanyMode。'
    }
    $companyUninstaller = Join-Path $repoRoot 'company-safe\uninstall.ps1'
    if (-not (Test-Path -LiteralPath $companyUninstaller)) {
        throw '找不到公司模式卸载脚本，无法安全迁移。'
    }
    & $companyUninstaller -UserRoot $UserRoot
}

if (-not $PythonPath) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $PythonPath = $pythonCommand.Source
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw '未找到 Python 3.10+。请先安装 Python，或通过 -PythonPath 指定 python.exe。'
}
& $PythonPath -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) {
    throw 'Python 版本低于 3.10。'
}

$envPath = Join-Path $repoRoot 'core.env'
if (Test-Path -LiteralPath $envPath) {
    $envText = Get-Content -LiteralPath $envPath -Raw
    if (-not $CoreToken -and $envText -match '(?m)^SHIYUAN_CORE_TOKEN=(.+)$') {
        $CoreToken = $Matches[1].Trim()
    }
} else {
    $envText = Get-Content -LiteralPath (Join-Path $repoRoot 'core.env.example') -Raw
}
if (-not $CoreToken) {
    if (-not $isLocalCore) {
        throw '连接远程 Core 时必须显式提供 -CoreToken。'
    }
    $bytes = New-Object byte[] 48
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($bytes)
    } finally {
        $random.Dispose()
    }
    $CoreToken = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
if ($CoreToken.Length -lt 32) {
    throw 'CoreToken 至少需要 32 个字符。'
}

if ($isLocalCore) {
    $envText = [regex]::Replace(
        $envText,
        '(?m)^SHIYUAN_ASSISTANT_NAME=.*$',
        "SHIYUAN_ASSISTANT_NAME=$AssistantName"
    )
    if ($envText -notmatch '(?m)^SHIYUAN_ASSISTANT_NAME=') {
        $envText = "SHIYUAN_ASSISTANT_NAME=$AssistantName$newline" + $envText
    }
    $envText = [regex]::Replace(
        $envText,
        '(?m)^SHIYUAN_CORE_TOKEN=.*$',
        "SHIYUAN_CORE_TOKEN=$CoreToken"
    )
    $envTemp = "$envPath.tmp"
    [IO.File]::WriteAllText($envTemp, $envText.Trim() + $newline, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $envTemp -Destination $envPath -Force

    if (-not $SkipCoreStart) {
        $docker = Get-Command docker.exe -ErrorAction SilentlyContinue
        if (-not $docker) {
            throw '个人 Core 默认使用 Docker。未找到 Docker；安装 Docker Desktop 后重试，或连接已有 Core 并使用 -SkipCoreStart。'
        }
        & $docker.Source compose -f (Join-Path $repoRoot 'docker-compose.yml') up -d --build
        if ($LASTEXITCODE -ne 0) {
            throw 'Docker Core 启动失败。'
        }
        $healthy = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            try {
                $health = Invoke-RestMethod -Method Get -Uri "$CoreUrl/health" -TimeoutSec 2
                if ($health.ok) {
                    $healthy = $true
                    break
                }
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $healthy) {
            throw 'Core 已启动，但健康检查在限定时间内没有通过。'
        }
    }
}

$installRoot = Join-Path $UserRoot '.shiyuan'
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
$installedBodies = [System.Collections.Generic.List[string]]::new()
$codexBackup = ''

if ($Codex) {
    $codexTarget = Join-Path $installRoot 'codex-hook'
    New-Item -ItemType Directory -Path $codexTarget -Force | Out-Null
    Copy-Item -Path (Join-Path $repoRoot 'connectors\codex-hook\*') -Destination $codexTarget -Recurse -Force

    if ($SkipDependencyInstall) {
        $hookPython = $PythonPath
    } else {
        $venvRoot = Join-Path $installRoot 'venv'
        & $PythonPath -m venv $venvRoot
        $hookPython = Join-Path $venvRoot 'Scripts\python.exe'
        & $hookPython -m pip install --disable-pip-version-check -r (Join-Path $codexTarget 'requirements.txt')
        if ($LASTEXITCODE -ne 0) {
            throw 'Codex Hook 依赖安装失败。'
        }
    }

    $clientConfig = [ordered]@{
        core_url = $CoreUrl.TrimEnd('/')
        token = $CoreToken
        body = 'codex'
        device = [Environment]::MachineName
        assistant_name = $AssistantName
        timeout_seconds = 2.0
        replay_timeout_seconds = 12.0
        capture_messages = $true
    }
    $clientPath = Join-Path $installRoot 'client.json'
    [IO.File]::WriteAllText(
        $clientPath,
        ($clientConfig | ConvertTo-Json -Depth 5) + $newline,
        [Text.UTF8Encoding]::new($false)
    )

    $codexHome = Join-Path $UserRoot '.codex'
    New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
    if (Test-Path -LiteralPath $codexConfig) {
        $codexBackup = "$codexConfig.$timestamp.pre-personal-core.bak"
        Copy-Item -LiteralPath $codexConfig -Destination $codexBackup
        $configText = Get-Content -LiteralPath $codexConfig -Raw
    } else {
        $configText = ''
    }
    $personalMarker = '(?ms)^# BEGIN SHIYUAN_PERSONAL_CORE\r?\n.*?^# END SHIYUAN_PERSONAL_CORE\r?\n?'
    $configText = [regex]::Replace($configText, $personalMarker, '').TrimEnd()
    $tomlPython = $hookPython.Replace("'", "''")
    $tomlMcp = (Join-Path $codexTarget 'mcp_server.py').Replace("'", "''")
    $hookStart = ('"{0}" "{1}" SessionStart' -f $hookPython, (Join-Path $codexTarget 'codex_hook.py')).Replace("'", "''")
    $hookPrompt = ('"{0}" "{1}" UserPromptSubmit' -f $hookPython, (Join-Path $codexTarget 'codex_hook.py')).Replace("'", "''")
    $hookStop = ('"{0}" "{1}" Stop' -f $hookPython, (Join-Path $codexTarget 'codex_hook.py')).Replace("'", "''")
    $hookEnd = ('"{0}" "{1}" SessionEnd' -f $hookPython, (Join-Path $codexTarget 'codex_hook.py')).Replace("'", "''")
    $block = @"
# BEGIN SHIYUAN_PERSONAL_CORE
[mcp_servers.shiyuan_personal_core]
command = '$tomlPython'
args = ['$tomlMcp']
startup_timeout_sec = 15
enabled = true

[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"

[[hooks.SessionStart.hooks]]
type = "command"
command = '$hookStart'
timeout = 8
additionalContextLimit = 24000

[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = '$hookPrompt'
timeout = 8
additionalContextLimit = 24000

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = '$hookStop'
timeout = 8

[[hooks.SessionEnd]]

[[hooks.SessionEnd.hooks]]
type = "command"
command = '$hookEnd'
timeout = 8
# END SHIYUAN_PERSONAL_CORE
"@
    $updated = ($configText + $newline + $newline + $block.Trim() + $newline).TrimStart()
    $configTemp = "$codexConfig.tmp"
    [IO.File]::WriteAllText($configTemp, $updated, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $configTemp -Destination $codexConfig -Force
    $installedBodies.Add('Codex')
}

if ($Hana) {
    $hanaHome = Join-Path $UserRoot '.hanako'
    $hanaSource = Join-Path $repoRoot 'connectors\hana-hook'
    $hanaTarget = Join-Path $hanaHome 'plugins\shiyuan-hook'
    New-Item -ItemType Directory -Path $hanaTarget -Force | Out-Null
    Copy-Item -Path (Join-Path $hanaSource '*') -Destination $hanaTarget -Recurse -Force

    $hanaConfigPath = Join-Path $hanaHome 'plugin-data\shiyuan-hook\config.json'
    New-Item -ItemType Directory -Path (Split-Path -Parent $hanaConfigPath) -Force | Out-Null
    $hanaConfig = [ordered]@{
        coreUrl = $CoreUrl.TrimEnd('/')
        token = $CoreToken
        body = 'hana'
        device = [Environment]::MachineName
        assistantName = $AssistantName
        timeoutMs = 1800
        replayTimeoutMs = 12000
        captureMessages = $true
    }
    [IO.File]::WriteAllText(
        $hanaConfigPath,
        ($hanaConfig | ConvertTo-Json -Depth 5) + $newline,
        [Text.UTF8Encoding]::new($false)
    )

    $serverInfoPath = Join-Path $hanaHome 'server-info.json'
    if (Test-Path -LiteralPath $serverInfoPath) {
        try {
            $serverInfo = Get-Content -LiteralPath $serverInfoPath -Raw | ConvertFrom-Json
            $headers = @{ Authorization = "Bearer $($serverInfo.token)" }
            $body = @{ path = $hanaSource; allowDowngrade = $false } | ConvertTo-Json
            Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($serverInfo.port)/api/plugins/install" -Headers $headers -ContentType 'application/json' -Body $body | Out-Null
        } catch {
            Write-Warning 'Hana 本地接口暂未加载插件；文件和配置已经安装，重启 Hana 后生效。'
        }
    }
    $installedBodies.Add('Hana')
}

$reportPath = Join-Path $installRoot 'INSTALL_REPORT.md'
$report = @"
# 个人助手 Core 安装报告

- 时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')
- 助手名称：$AssistantName
- 模式：个人 Core
- Core 地址：$($CoreUrl.TrimEnd('/'))
- Core Token：已配置（不在报告中显示）
- 身体：$($installedBodies -join '、')
- 自动形成候选记忆：开启
- 候选自动确认：关闭
- Codex 配置备份：$(if ($codexBackup) { $codexBackup } else { '未安装 Codex，或原配置不存在' })

重启已安装的身体并新建会话。在线时，回复末尾应显示 🐳 ${AssistantName}在线，不应显示“公司安全模式”。
"@
[IO.File]::WriteAllText($reportPath, $report.Trim() + $newline, [Text.UTF8Encoding]::new($false))

Write-Host "个人助手“$AssistantName”安装完成。" -ForegroundColor Green
Write-Host '模式：个人 Core（不是公司安全模式）'
Write-Host "安装报告：$reportPath"
Write-Host '请重启 Codex/Hana，并在新会话中询问“当前是什么模式？”进行验收。'

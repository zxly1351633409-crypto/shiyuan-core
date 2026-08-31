param(
    [string]$PackageRoot = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'

$manifestPath = Join-Path $PackageRoot 'MANIFEST.sha256'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw '缺少 MANIFEST.sha256，无法验证安装包完整性。'
}

$checked = 0
foreach ($line in Get-Content -LiteralPath $manifestPath -Encoding UTF8) {
    if (-not $line.Trim()) { continue }
    if ($line -notmatch '^([A-Fa-f0-9]{64})  (.+)$') {
        throw "无效的清单行：$line"
    }
    $expected = $Matches[1].ToUpperInvariant()
    $relative = $Matches[2].Replace('/', [IO.Path]::DirectorySeparatorChar)
    $packageFull = [IO.Path]::GetFullPath($PackageRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $target = [IO.Path]::GetFullPath((Join-Path $PackageRoot $relative))
    if (-not $target.StartsWith($packageFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "清单路径越过安装包边界：$relative"
    }
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        throw "安装包缺少文件：$relative"
    }
    $stream = [IO.File]::OpenRead($target)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $actual = [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace('-', '').ToUpperInvariant()
    } finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
    if ($actual -ne $expected) {
        throw "安装包文件校验失败：$relative"
    }
    $checked++
}

if ($checked -lt 10) {
    throw "清单文件数量异常：$checked"
}

$version = (Get-Content -LiteralPath (Join-Path $PackageRoot 'VERSION.txt') -Raw).Trim()
Write-Host "安装包校验通过：$checked 个文件，版本 $version。"

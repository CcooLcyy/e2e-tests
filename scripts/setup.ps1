param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & python -m venv (Join-Path $repoRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败' }
}
& $pythonPath -m pip install -r (Join-Path $repoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '安装测试依赖失败' }

$runtimeRoots = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\EdgeWebView\Application'),
    (Join-Path $env:ProgramFiles 'Microsoft\EdgeWebView\Application'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\EdgeWebView\Application')
)
$runtimes = foreach ($runtimeRoot in $runtimeRoots) {
    if (Test-Path -LiteralPath $runtimeRoot) {
        Get-ChildItem -LiteralPath $runtimeRoot -Directory | Where-Object {
            $_.Name -match '^\d+\.\d+\.\d+\.\d+$' -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'msedgewebview2.exe'))
        }
    }
}
$runtime = $runtimes | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
if (-not $runtime) { throw '未找到已安装的 WebView2 Runtime' }
$managerPath = Join-Path $repoRoot '.venv\Lib\site-packages\selenium\webdriver\common\windows\selenium-manager.exe'
$managerOutput = & $managerPath --browser webview2 --browser-version $runtime.Name --cache-path (Join-Path $repoRoot '.tools') --avoid-browser-download --avoid-stats --timeout 45 --output JSON
if ($LASTEXITCODE -ne 0) { throw '获取匹配的 WebView2 驱动失败' }
$driverResult = ($managerOutput -join "`n") | ConvertFrom-Json
Write-Host "WebView2 版本：$($runtime.Name)"
Write-Host "驱动路径：$($driverResult.result.driver_path)"

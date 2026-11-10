$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonVersion = '3.13.17'
$pythonSha256 = '9E35319BFFEDAB8B36AD44D8F377CB4F1733BCEC257A0B6D04D94F01486DDB38'
$bundleRoot = Join-Path $repoRoot 'src-tauri/.bundle'
$runtime = Join-Path $bundleRoot 'runtime'

& python -c 'import platform, sys; sys.exit(not (sys.platform == "win32" and sys.version_info[:2] == (3, 13) and platform.machine() == "AMD64"))'
if ($LASTEXITCODE -ne 0) { throw 'Staging requires Windows x64 CPython 3.13 on PATH' }

New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
$bundleDirectory = Get-Item -LiteralPath $bundleRoot
if ($bundleDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Bundle output directory must not be a link'
}
$staging = Join-Path $bundleRoot ([guid]::NewGuid().ToString('N'))
$newRuntime = Join-Path $staging 'runtime'
$pythonRoot = Join-Path $newRuntime 'python'
$suiteRoot = Join-Path $newRuntime 'suite'
$archive = Join-Path $staging 'python.zip'
New-Item -ItemType Directory -Path $pythonRoot -Force | Out-Null
try {
    $url = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip"
    Invoke-WebRequest -Uri $url -OutFile $archive
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $pythonSha256) {
        throw 'Embeddable CPython archive SHA256 mismatch'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $pythonRoot
    $sitePackages = Join-Path $pythonRoot 'Lib/site-packages'
    & python -m pip --isolated --disable-pip-version-check install --only-binary=:all: --no-compile --target $sitePackages -r (Join-Path $PSScriptRoot 'requirements-windows.lock')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to stage the Python wheels' }
    $seleniumManager = Join-Path $sitePackages 'selenium/webdriver/common/windows/selenium-manager.exe'
    if (-not (Test-Path -LiteralPath $seleniumManager -PathType Leaf)) {
        throw 'The bundled Selenium Manager executable is missing'
    }

    # The embedded interpreter resolves these paths relative to its own executable.
    Set-Content -LiteralPath (Join-Path $pythonRoot 'python313._pth') -Encoding ascii -Value @(
        'python313.zip', '.', '..\suite', 'Lib\site-packages'
    )

    foreach ($directory in @('testbench', 'tests')) {
        $destination = Join-Path $suiteRoot $directory
        New-Item -ItemType Directory -Path $destination | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $repoRoot $directory) -File -Filter '*.py' |
            Copy-Item -Destination $destination
    }
    foreach ($relativePath in @(
        'desktop_demo.py', 'windows_privileges.py', 'pytest.ini', 'requirements.txt',
        'tests/case-order.json', 'docs/examples/agc-direction-block.json',
        'docs/examples/huawei-rtu.example.json', 'docs/examples/modbus-rtu.example.json'
    )) {
        $destination = Join-Path $suiteRoot $relativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $repoRoot $relativePath) -Destination $destination
    }

    $bundledPython = Join-Path $pythonRoot 'python.exe'
    $smokeData = Join-Path $staging 'smoke-data'
    $responses = @(
        '{"id":1,"method":"bootstrap"}'
        '{"id":2,"method":"shutdown"}'
    ) | & $bundledPython -X utf8 -B -u -m testbench.bridge --root $suiteRoot --data-root $smokeData
    if ($LASTEXITCODE -ne 0 -or @($responses).Count -ne 2) {
        throw 'Bundled Python bridge did not complete its smoke check'
    }
    $bootstrap = $responses[0] | ConvertFrom-Json
    $shutdown = $responses[1] | ConvertFrom-Json
    if (-not $bootstrap.ok -or -not $bootstrap.result.installed -or
        @($bootstrap.result.cases).Count -eq 0 -or -not $shutdown.result.closed) {
        throw "Bundled Python could not discover the installed cases: $($bootstrap.error)"
    }
    Set-Content -LiteralPath (Join-Path $newRuntime '.mskdsp-testbench-runtime') -Encoding ascii -Value $pythonVersion

    [string[]]$pythonFiles = @(Get-ChildItem -LiteralPath $pythonRoot -File -Recurse -Force |
        ForEach-Object { [IO.Path]::GetRelativePath($pythonRoot, $_.FullName) })
    [Array]::Sort($pythonFiles, [StringComparer]::Ordinal)
    $manifest = foreach ($relativePath in $pythonFiles) {
        $fileHash = (Get-FileHash -LiteralPath (Join-Path $pythonRoot $relativePath) -Algorithm SHA256).Hash
        "$fileHash $relativePath"
    }
    $manifestBytes = [Text.Encoding]::UTF8.GetBytes(($manifest -join "`n"))
    $fingerprint = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($manifestBytes))
    Set-Content -LiteralPath (Join-Path $newRuntime 'python-fingerprint.nsh') -Encoding ascii `
        -Value ('!define TESTBENCH_PYTHON_FINGERPRINT "{0}"' -f $fingerprint)

    # Replace an earlier stage only within the expected, non-linked output directory.
    if (Test-Path -LiteralPath $runtime) {
        $oldRuntime = Get-Item -LiteralPath $runtime
        if (-not $oldRuntime.PSIsContainer -or $oldRuntime.Parent.FullName -ne $bundleDirectory.FullName) {
            throw 'Unexpected previous runtime location'
        }
        if ($oldRuntime.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Previous runtime directory must not be a link'
        }
        Remove-Item -LiteralPath $oldRuntime.FullName -Recurse -Force
    }
    Move-Item -LiteralPath $newRuntime -Destination $runtime
} finally {
    if (Test-Path -LiteralPath $staging) {
        $stagingDirectory = Get-Item -LiteralPath $staging
        if ($stagingDirectory.Parent.FullName -eq $bundleDirectory.FullName -and
            -not ($stagingDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Remove-Item -LiteralPath $stagingDirectory.FullName -Recurse -Force
        }
    }
}

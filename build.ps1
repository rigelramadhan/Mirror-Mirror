param([string]$Python = '.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Push-Location $PSScriptRoot
try {
    $iconPath = Join-Path $PSScriptRoot 'mirror_mirror.ico'
    $staticPath = Join-Path $PSScriptRoot 'static'
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name MirrorMirrorHardened --icon $iconPath `
        --distpath dist/hardened --workpath build/hardened --specpath build/hardened `
        --add-data "$staticPath;static" `
        --hidden-import engineio.async_drivers.threading --hidden-import pystray._win32 `
        webrtc_browser.py
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
    Copy-Item -LiteralPath LICENSE -Destination dist/hardened/LICENSE.txt
    Copy-Item -LiteralPath README.md -Destination dist/hardened/README.md
    Copy-Item -LiteralPath packaging/START-HERE.txt -Destination dist/hardened/START-HERE.txt
    Copy-Item -LiteralPath static/vendor/socket.io-LICENSE.txt -Destination dist/hardened/socket.io-LICENSE.txt
    & $Python -c 'import importlib.metadata as m; print("\n".join(sorted(d.metadata["Name"]+"=="+d.version for d in m.distributions())))' |
        Set-Content -LiteralPath dist/hardened/build-environment.txt -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Could not record build environment' }
    git describe --always --dirty | Set-Content -LiteralPath dist/hardened/source-revision.txt -Encoding ascii
    $exeHash = Get-FileHash -LiteralPath dist/hardened/MirrorMirrorHardened.exe -Algorithm SHA256
    ($exeHash.Hash.ToLower() + '  MirrorMirrorHardened.exe') |
        Set-Content -LiteralPath dist/hardened/SHA256SUMS.txt -Encoding ascii
    # Deliberately list every packaged file: no TLS keys, profiles, or test data.
    Compress-Archive -LiteralPath dist/hardened/MirrorMirrorHardened.exe,dist/hardened/LICENSE.txt,`
        dist/hardened/README.md,dist/hardened/START-HERE.txt,dist/hardened/socket.io-LICENSE.txt,`
        dist/hardened/build-environment.txt,dist/hardened/source-revision.txt,dist/hardened/SHA256SUMS.txt `
        -DestinationPath dist/MirrorMirror-hardened-win64.zip -Force
    Write-Output 'Built dist/MirrorMirror-hardened-win64.zip'
} finally {
    Pop-Location
}

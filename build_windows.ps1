param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$buildDirectory = Join-Path $root "build"
$distDirectory = Join-Path $root "dist"
$entryPoint = Join-Path $root "src\main.py"
$applicationDirectory = $distDirectory
if (-not $OneFile) {
    $applicationDirectory = Join-Path $distDirectory "BinanceMarketConsole"
}
$targetExecutable = Join-Path $applicationDirectory "BinanceMarketConsole.exe"

if (-not (Test-Path $python)) {
    & py -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Không tạo được môi trường Python ảo."
    }
}

$runningApplication = Get-CimInstance Win32_Process `
    -Filter "Name = 'BinanceMarketConsole.exe'" |
    Where-Object { $_.ExecutablePath -eq $targetExecutable }
if ($runningApplication) {
    $processIds = ($runningApplication | ForEach-Object { $_.ProcessId }) -join ", "
    throw "Cannot overwrite the build output while BinanceMarketConsole.exe is running (PID: $processIds). Close the application and rerun build_windows.ps1."
}

New-Item -ItemType Directory -Path $buildDirectory -Force | Out-Null
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Không cập nhật được pip."
}

& $python -m pip install -r (Join-Path $root "requirements-build.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Không cài được các dependency để build."
}

$packageMode = "--onedir"
if ($OneFile) {
    $packageMode = "--onefile"
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    $packageMode `
    --name BinanceMarketConsole `
    --paths (Join-Path $root "src") `
    --distpath $distDirectory `
    --workpath $buildDirectory `
    --specpath $buildDirectory `
    $entryPoint

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build thất bại."
}

$envExample = Join-Path $root ".env.example"
if (Test-Path $envExample) {
    Copy-Item $envExample (Join-Path $applicationDirectory ".env.example") -Force
}

if ($OneFile) {
    Write-Output "Ứng dụng: $(Join-Path $distDirectory 'BinanceMarketConsole.exe')"
} else {
    Write-Output "Ứng dụng: $(Join-Path $distDirectory 'BinanceMarketConsole\BinanceMarketConsole.exe')"
    Write-Output "Khi phát hành, hãy gửi toàn bộ thư mục dist\BinanceMarketConsole."
}

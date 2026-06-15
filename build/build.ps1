param(
    [string]$Version = "0.1.0",
    [string]$FfmpegDir = "",
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$DistRoot = Join-Path $RepoRoot "dist"
$WorkRoot = Join-Path $RepoRoot "build\work"
$BuildOutputRoot = Join-Path $RepoRoot "build\output"
$ToolHome = Join-Path $RepoRoot "build\home"
$ToolConfig = Join-Path $RepoRoot "build\pyinstaller"
$SpecPath = Join-Path $RepoRoot "build\TekkenVodHelper.spec"
$PortableName = "TekkenVodHelper-v$Version-windows-portable"
$PortableDir = Join-Path $DistRoot $PortableName
$ZipPath = Join-Path $DistRoot "$PortableName.zip"

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    throw "PyInstaller was not found. Install it with: python -m pip install pyinstaller"
}

New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BuildOutputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ToolHome | Out-Null
New-Item -ItemType Directory -Force -Path $ToolConfig | Out-Null

$env:HOME = $ToolHome
$env:USERPROFILE = $ToolHome
$env:PYINSTALLER_CONFIG_DIR = $ToolConfig

pyinstaller --clean --noconfirm --workpath $WorkRoot --distpath $BuildOutputRoot $SpecPath

if (Test-Path -LiteralPath $PortableDir) {
    Get-ChildItem -LiteralPath $PortableDir -Force | Remove-Item -Recurse -Force
} else {
    New-Item -ItemType Directory -Force -Path $PortableDir | Out-Null
}

$ExePath = Join-Path $BuildOutputRoot "TekkenVodHelper.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "Expected build output was not found: $ExePath"
}
Copy-Item -LiteralPath $ExePath -Destination (Join-Path $PortableDir "TekkenVodHelper.exe") -Force

if ($FfmpegDir) {
    $ResolvedFfmpegDir = Resolve-Path $FfmpegDir
    $RequiredTools = @("ffmpeg.exe", "ffprobe.exe")
    $PortableFfmpegDir = Join-Path $PortableDir "ffmpeg"
    New-Item -ItemType Directory -Force -Path $PortableFfmpegDir | Out-Null

    foreach ($Tool in $RequiredTools) {
        $ToolPath = Join-Path $ResolvedFfmpegDir $Tool
        if (-not (Test-Path -LiteralPath $ToolPath)) {
            throw "Missing $Tool in $ResolvedFfmpegDir"
        }
        Copy-Item -LiteralPath $ToolPath -Destination (Join-Path $PortableFfmpegDir $Tool) -Force
    }
}

$ReadmePath = Join-Path $PortableDir "README.txt"
@"
Tekken VOD Helper v$Version

Extract this folder and run TekkenVodHelper.exe.

FFmpeg:
- If this package includes an ffmpeg folder, exports and previews should work without separate FFmpeg setup.
- If not, install FFmpeg separately or choose ffmpeg.exe and ffprobe.exe in Settings.

VLC:
- Install VLC media player separately to use embedded video playback with sound.
"@ | Set-Content -LiteralPath $ReadmePath -Encoding UTF8

if (-not $NoZip) {
    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Compress-Archive -Path $PortableDir -DestinationPath $ZipPath -Force
    Write-Host "Release asset: $ZipPath"
}

Write-Host "Portable folder: $PortableDir"

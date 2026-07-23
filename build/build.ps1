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

$PreviousBundledFfmpegDir = $env:TEKKEN_VOD_HELPER_FFMPEG_DIR
try {
    if ($FfmpegDir) {
        $ResolvedFfmpegDir = Resolve-Path $FfmpegDir
        foreach ($Tool in @("ffmpeg.exe", "ffprobe.exe")) {
            $ToolPath = Join-Path $ResolvedFfmpegDir $Tool
            if (-not (Test-Path -LiteralPath $ToolPath)) {
                throw "Missing $Tool in $ResolvedFfmpegDir"
            }
        }
        $env:TEKKEN_VOD_HELPER_FFMPEG_DIR = $ResolvedFfmpegDir
    } else {
        Remove-Item Env:\TEKKEN_VOD_HELPER_FFMPEG_DIR -ErrorAction SilentlyContinue
    }

    pyinstaller --clean --noconfirm --workpath $WorkRoot --distpath $BuildOutputRoot $SpecPath
} finally {
    if ($null -eq $PreviousBundledFfmpegDir) {
        Remove-Item Env:\TEKKEN_VOD_HELPER_FFMPEG_DIR -ErrorAction SilentlyContinue
    } else {
        $env:TEKKEN_VOD_HELPER_FFMPEG_DIR = $PreviousBundledFfmpegDir
    }
}

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

$ReadmePath = Join-Path $PortableDir "README.txt"
@"
Tekken VOD Helper v$Version

Run TekkenVodHelper.exe.

FFmpeg:
- If this build was created with -FfmpegDir, FFmpeg is embedded in TekkenVodHelper.exe.
- If not, install FFmpeg separately or choose ffmpeg.exe and ffprobe.exe in Settings.

Portraits:
- Character portraits are embedded in TekkenVodHelper.exe.

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

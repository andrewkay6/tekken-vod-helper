param(
    [string]$InputDir = "C:\Users\ak\Videos",
    [string]$OutputDir = "",
    [ValidateSet("h264_nvenc", "libx264")]
    [string]$Encoder = "h264_nvenc",
    [ValidateSet("p1", "p2", "p3", "p4", "p5", "p6", "p7")]
    [string]$NvencPreset = "p5",
    [int]$VideoBitrateMbps = 35,
    [int]$MaxrateMbps = 60,
    [int]$Cq = 18,
    [int]$X264Crf = 16,
    [switch]$Recurse
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg was not found on PATH."
}

if (-not (Test-Path -LiteralPath $InputDir -PathType Container)) {
    throw "Input directory does not exist: $InputDir"
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $InputDir "youtube_1440p"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$extensions = @(".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v")
$searchOptions = @{
    LiteralPath = $InputDir
    File = $true
}

if ($Recurse) {
    $searchOptions.Recurse = $true
}

$videos = Get-ChildItem @searchOptions |
    Where-Object { $extensions -contains $_.Extension.ToLowerInvariant() }

if (-not $videos) {
    Write-Host "No videos found in $InputDir"
    exit 0
}

foreach ($video in $videos) {
    if ($video.DirectoryName -eq (Resolve-Path -LiteralPath $OutputDir).Path) {
        continue
    }

    $outputFile = Join-Path $OutputDir "$($video.BaseName)_1440p.mp4"

    if (Test-Path -LiteralPath $outputFile) {
        Write-Host "Skipping existing: $outputFile"
        continue
    }

    Write-Host "Encoding: $($video.FullName)"
    Write-Host "Output:   $outputFile"

    $commonArgs = @(
        "-hide_banner",
        "-y",
        "-i", $video.FullName,
        "-vf", "scale=2560:1440:flags=lanczos:force_original_aspect_ratio=decrease,pad=2560:1440:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-c:a", "copy",
        "-movflags", "+faststart"
    )

    if ($Encoder -eq "h264_nvenc") {
        $videoArgs = @(
            "-c:v", "h264_nvenc",
            "-preset", $NvencPreset,
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", $Cq,
            "-b:v", "$($VideoBitrateMbps)M",
            "-maxrate", "$($MaxrateMbps)M",
            "-bufsize", "$($MaxrateMbps * 2)M"
        )
    } else {
        $videoArgs = @(
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", $X264Crf
        )
    }

    & ffmpeg @commonArgs @videoArgs $outputFile

    if ($LASTEXITCODE -ne 0) {
        if ((Test-Path -LiteralPath $outputFile) -and ((Get-Item -LiteralPath $outputFile).Length -eq 0)) {
            Remove-Item -LiteralPath $outputFile
        }

        if ($Encoder -eq "h264_nvenc") {
            Write-Host ""
            Write-Host "NVENC failed. If FFmpeg reported 'Driver does not support the required nvenc API version', update your NVIDIA driver to 570.0+ or rerun with CPU encoding:"
            Write-Host ".\scripts\upscale-to-1440p.ps1 -Encoder libx264"
            Write-Host ""
        }

        throw "ffmpeg failed for: $($video.FullName)"
    }
}

param(
    [switch]$Desktop,
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "PowerShell 5 or newer is required."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonw = Join-Path $VenvDir "Scripts\pythonw.exe"
$Requirements = Join-Path $RepoRoot "requirements.txt"
$IconPath = Join-Path $RepoRoot "tekken_vod_helper\kwtekken-icon.ico"
$ShortcutName = "Tekken VOD Helper.lnk"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Creating virtual environment..."
    python -m venv $VenvDir
}

if (-not $SkipDependencies) {
    Write-Host "Installing Python dependencies..."
    & $VenvPython -m pip install -r $Requirements
}

if (Test-Path -LiteralPath $VenvPythonw) {
    $TargetPath = $VenvPythonw
} else {
    $TargetPath = $VenvPython
}

if (-not (Test-Path -LiteralPath $TargetPath)) {
    throw "Could not find Python in the virtual environment: $TargetPath"
}

if (-not (Test-Path -LiteralPath $IconPath)) {
    throw "Could not find shortcut icon: $IconPath"
}

function New-AppShortcut {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($Path)
    $Shortcut.TargetPath = $TargetPath
    $Shortcut.Arguments = "-m tekken_vod_helper"
    $Shortcut.WorkingDirectory = $RepoRoot
    $Shortcut.IconLocation = "$IconPath,0"
    $Shortcut.Description = "Launch Tekken VOD Helper from this clone."
    $Shortcut.Save()
}

$StartMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) "KWTekken"
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
$StartMenuShortcut = Join-Path $StartMenuDir $ShortcutName
New-AppShortcut -Path $StartMenuShortcut
Write-Host "Start Menu shortcut: $StartMenuShortcut"

if ($Desktop) {
    $DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) $ShortcutName
    New-AppShortcut -Path $DesktopShortcut
    Write-Host "Desktop shortcut: $DesktopShortcut"
}

Write-Host "Done. Launch Tekken VOD Helper from the Start Menu."

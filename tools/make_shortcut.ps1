# Puts an "AI Dungeon Master" shortcut on the Desktop, pointing at Play.cmd with the
# d20 icon. Run it again after moving the folder - the shortcut stores an absolute path.
#
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
#
# -StartMenu also adds it to the Start menu, so it turns up when you type "dungeon".

param(
    [switch]$StartMenu,
    [string]$Name = "AI Dungeon Master"
)

$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root "Play.cmd"
$icon   = Join-Path $root "static\favicon.ico"

if (-not (Test-Path $target)) { throw "Play.cmd is missing from $root" }
if (-not (Test-Path $icon)) {
    Write-Host "No icon yet - drawing one." -ForegroundColor DarkGray
    # same reasoning as Play.cmd: `py -3` can point at an install whose files are gone,
    # so ask each candidate its version rather than trusting that the command exists
    $python = @("python", "py") | Where-Object {
        try { & $_ -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null; $LASTEXITCODE -eq 0 }
        catch { $false }
    } | Select-Object -First 1
    if (-not $python) { throw "No working Python 3.10+ found to draw the icon with" }
    & $python (Join-Path $PSScriptRoot "make_icon.py")
    if (-not (Test-Path $icon)) { throw "Couldn't create $icon" }
}

function New-GameShortcut([string]$Path) {
    $shell = New-Object -ComObject WScript.Shell
    $link  = $shell.CreateShortcut($Path)
    $link.TargetPath       = $target
    $link.WorkingDirectory = $root
    $link.IconLocation     = "$icon,0"
    $link.Description      = "Play AI Dungeon Master"
    # a normal window, not minimised: on the first run it shows the install progress,
    # and if anything goes wrong that window is the only place it gets said
    $link.WindowStyle      = 1
    $link.Save()
    Write-Host "Created $Path" -ForegroundColor Green
}

New-GameShortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "$Name.lnk")

if ($StartMenu) {
    $dir = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    New-GameShortcut (Join-Path $dir "$Name.lnk")
}

Write-Host ""
Write-Host "Double-click the d20 on your Desktop to play." -ForegroundColor Cyan

# Hunyuan 3D Multiview skill - one-line installer (Windows PowerShell)
# Usage:  irm https://raw.githubusercontent.com/hwdemtv/hunyuan-3d-multiview/main/install.ps1 | iex
$ErrorActionPreference = "Stop"

$name = "hunyuan-3d-multiview"
$dest = Join-Path $env:USERPROFILE ".workbuddy\skills\$name"
$tmp  = Join-Path $env:TEMP "$name.zip"
$ext  = Join-Path $env:TEMP "$name-src"

Write-Host ">> Downloading $name ..." -ForegroundColor Cyan
Invoke-WebRequest -Uri "https://github.com/$name/archive/refs/heads/main.zip" -OutFile $tmp -UseBasicParsing
if (Test-Path $ext) { Remove-Item $ext -Recurse -Force }
Expand-Archive -Path $tmp -DestinationPath $ext -Force
$src = Join-Path $ext "$name-main"

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $src "SKILL.md") $dest -Force
New-Item -ItemType Directory -Force -Path (Join-Path $dest "scripts") | Out-Null
Copy-Item (Join-Path $src "scripts\*") (Join-Path $dest "scripts") -Force

Remove-Item $tmp, $ext -Recurse -Force
Write-Host ">> Installed to $dest" -ForegroundColor Green
Write-Host ">> Restart WorkBuddy, then say: 用 hunyuan-3d-multiview 技能把这张图变成 3D 手办"

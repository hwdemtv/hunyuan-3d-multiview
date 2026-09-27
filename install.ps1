# Hunyuan 3D Multiview skill - one-line installer (Windows PowerShell)
# Usage:  irm https://raw.githubusercontent.com/hwdemtv/hunyuan-3d-multiview/main/install.ps1 | iex
$ErrorActionPreference = "Stop"

$owner = "hwdemtv"
$name = "hunyuan-3d-multiview"
$dest = Join-Path $env:USERPROFILE ".workbuddy\skills\$name"
$tmp  = Join-Path $env:TEMP "$name.zip"
$ext  = Join-Path $env:TEMP "$name-src"

Write-Host ">> Downloading $owner/$name ..." -ForegroundColor Cyan
Invoke-WebRequest -Uri "https://github.com/$owner/$name/archive/refs/heads/main.zip" -OutFile $tmp -UseBasicParsing
if (Test-Path $ext) { Remove-Item $ext -Recurse -Force }
Expand-Archive -Path $tmp -DestinationPath $ext -Force
$src = Join-Path $ext "$name-main"

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $src "SKILL.md") $dest -Force
Copy-Item (Join-Path $src "scripts") $dest -Recurse -Force
Copy-Item (Join-Path $src "references") $dest -Recurse -Force
if (Test-Path (Join-Path $src "vendor")) {
  Copy-Item (Join-Path $src "vendor") $dest -Recurse -Force
}

Remove-Item $tmp, $ext -Recurse -Force
Write-Host ">> Installed to $dest" -ForegroundColor Green
Write-Host ">> Restart WorkBuddy, then say: 用 hunyuan-3d-multiview 技能把这张图变成 3D 手办"

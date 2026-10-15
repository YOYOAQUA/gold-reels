# Gold Reels installer for Windows 10/11. Everything goes into one folder (default %USERPROFILE%\GoldReels).
$ErrorActionPreference = "Stop"

$Studio = if ($env:GOLD_REELS_HOME) { $env:GOLD_REELS_HOME } else { Join-Path $env:USERPROFILE "GoldReels" }
$Skill = Split-Path -Parent $PSScriptRoot
$env:Path = (Join-Path $env:USERPROFILE ".local\bin") + ";" + $env:Path

Write-Host "[1/5] Python manager (uv)"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
}

Write-Host "[2/5] Studio folder: $Studio"
foreach ($d in "input", "projects", "output", ".tmp") {
  New-Item -ItemType Directory -Force -Path (Join-Path $Studio $d) | Out-Null
}

Write-Host "[3/5] Studio Python"
$Py = Join-Path $Studio ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { uv venv --python 3.11 (Join-Path $Studio ".venv") }

Write-Host "[4/5] Video engine + Hebrew speech-to-text"
uv pip install --python $Py "faster-whisper>=1.1" "imageio-ffmpeg>=0.5"

$Wrapper = "@echo off`r`nchcp 65001 >nul`r`nset PYTHONUTF8=1`r`nset GOLD_REELS_HOME=$Studio`r`n`"$Py`" `"$Skill\scripts\gr.py`" %*`r`n"
[System.IO.File]::WriteAllText((Join-Path $Studio "gr.cmd"), $Wrapper, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "[5/5] Hebrew model (~1.6GB, first time only) + self-test"
& (Join-Path $Studio "gr.cmd") setup

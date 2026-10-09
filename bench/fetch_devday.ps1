# Download the test recording the bench scripts and bench/configs/devday.json use (OpenAI DevDay 2026 Keynote, about
# 53 min, 1080p) into data\. YouTube may ask to sign in from some networks: put a cookies.txt at .secret\youtube-cookies.txt.
#   pwsh -File bench\fetch_devday.ps1
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location (Split-Path $PSScriptRoot -Parent)
$cookies = if (Test-Path .secret\youtube-cookies.txt) { @('--cookies', '.secret\youtube-cookies.txt') } else { @() }
"[{0:yyyy-MM-dd HH:mm:ss}] start" -f (Get-Date)
uv run python -m yt_dlp --js-runtimes node --remote-components ejs:github @cookies `
    -f '137+251/bv*[height<=1080]+ba/b' --merge-output-format mkv --newline --no-progress `
    --write-info-json --write-subs --write-auto-subs --sub-langs 'en.*' `
    -o 'data\devday2026-keynote.%(ext)s' 'https://www.youtube.com/watch?v=Fls_onRviPM'
"[{0:yyyy-MM-dd HH:mm:ss}] exit={1}" -f (Get-Date), $LASTEXITCODE

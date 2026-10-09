# M2 shell test: MediaMTX + ingest (fake live from the DevDay file) + playout (10 s delay for the test) + a recorder
# pulling the RTMP output back. Long run, log to a file:
#   pwsh -NoProfile -File bench\run_m2_test.ps1 > .tmp\m2_test.log 2>&1
$ErrorActionPreference = 'Continue'
$P = Split-Path $PSScriptRoot -Parent
Set-Location $P
$env:PYTHONIOENCODING = 'utf-8'
$uv = (Get-Command uv).Source
$T = 150; $DELAY = 10
# leftovers of an earlier run hold the buffer files open
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'livedub\.(ingest|playout)' -or ($_.Name -eq 'ffmpeg.exe' -and $_.CommandLine -match 'live-dub|live/dub') } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
New-Item -ItemType Directory -Force "$P\.tmp\m2" | Out-Null
Remove-Item "$P\.tmp\m2\*" -Recurse -Force -ErrorAction SilentlyContinue

& $uv run python bench\make_test_events.py outputs\m0\mt_v2_B.json outputs\m0\tts\m1v2\result.json .tmp\m2\events.jsonl

# MediaMTX runs as its own long-lived service (scripts\start_mediamtx.ps1); started from here it would inherit
# this script's log handle and keep the log file locked for the next run.
if (-not (Get-Process mediamtx -ErrorAction SilentlyContinue)) { 'mediamtx is not running'; exit 1 }
"[{0:HH:mm:ss}] start ingest + playout" -f (Get-Date)
$ing = Start-Process -FilePath $uv -ArgumentList @('run', 'python', '-m', 'livedub.ingest', 'data\devday2026-keynote.mkv', '.tmp\m2\buf', '--ss', '1440', '--t', "$T") `
    -RedirectStandardOutput "$P\.tmp\m2\ingest.log" -RedirectStandardError "$P\.tmp\m2\ingest.err" -PassThru -WindowStyle Hidden
Start-Sleep 1
$play = Start-Process -FilePath $uv -ArgumentList @('run', 'python', '-m', 'livedub.playout', '.tmp\m2\buf', '.tmp\m2\events.jsonl', 'rtmp://127.0.0.1:1935/live/dub', '--delay', "$DELAY") `
    -RedirectStandardOutput "$P\.tmp\m2\playout.log" -RedirectStandardError "$P\.tmp\m2\playout.err" -PassThru -WindowStyle Hidden
Start-Sleep ($DELAY + 6)
"[{0:HH:mm:ss}] start recorder" -f (Get-Date)
& ffmpeg -hide_banner -loglevel warning -rw_timeout 15000000 -i rtmp://127.0.0.1:1935/live/dub -c copy -t ($T - 20) "$P\.tmp\m2\rec.mp4"
"[{0:HH:mm:ss}] recorder done rc=$LASTEXITCODE" -f (Get-Date)
$play.WaitForExit(60000) | Out-Null
"[{0:HH:mm:ss}] all done" -f (Get-Date)

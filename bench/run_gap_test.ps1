# Gap test: ingest --live (a file paced with --re) -> playout (clock-driven, --gc) -> mp4, no processor.
# At -KillAt seconds the ingest's ffmpeg is killed; ingest restarts it (the file plays again from --ss) with PT
# continuing past the gap. Expect: playout keeps going, slate over the gap, output length = wall time.
# Long run (log to a file):
#   pwsh -NoProfile -File bench\run_gap_test.ps1 > .tmp\gap.log 2>&1
param([int]$T = 150, [int]$KillAt = 60, [int]$D = 20, [string]$Dir = '.tmp\gap')
$ErrorActionPreference = 'Continue'
$P = Split-Path $PSScriptRoot -Parent
Set-Location $P
$env:PYTHONIOENCODING = 'utf-8'
$uv = (Get-Command uv).Source
$L = Join-Path $P $Dir
Remove-Item $L -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $L | Out-Null
New-Item -ItemType File -Force "$L\events.jsonl" | Out-Null
$start = { param($name, $argv) Start-Process -FilePath $uv -ArgumentList $argv -RedirectStandardOutput "$L\$name.log" -RedirectStandardError "$L\$name.err" -PassThru -WindowStyle Hidden }
"[{0:HH:mm:ss}] start T={1} kill at {2} D={3}" -f (Get-Date), $T, $KillAt, $D
$ing = & $start 'ingest' @('run', 'python', '-m', 'livedub.ingest', 'data\devday2026-keynote.mkv', "$Dir\buf", '--ss', '1440', '--t', "$T", '--live', '--re')
Start-Sleep 1
$play = & $start 'playout' @('run', 'python', '-m', 'livedub.playout', "$Dir\buf", "$Dir\events.jsonl", "$Dir\out.mp4", '--delay', "$D", '--gc')
Start-Sleep $KillAt
$Dq = [regex]::Escape($Dir)
$ff = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'ffmpeg.exe' -and $_.CommandLine -match $Dq }
"[{0:HH:mm:ss}] killing ingest ffmpeg: {1}" -f (Get-Date), (($ff | ForEach-Object ProcessId) -join ',')
$ff | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
$ing.WaitForExit(($T + 30) * 1000) | Out-Null
"[{0:HH:mm:ss}] ingest done" -f (Get-Date)
$play.WaitForExit(($D + 60) * 1000) | Out-Null
"[{0:HH:mm:ss}] playout done" -f (Get-Date)

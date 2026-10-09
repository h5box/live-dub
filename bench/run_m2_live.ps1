# M2 live test: fake live (DevDay from 24:00) -> processor (ASR service + speakers + Hy-MT2 + dubbing service)
# -> playout (D from the config) -> MediaMTX, with a recorder. Needs MediaMTX and the ASR service already running
# (python -m livedub.server.services start mediamtx / asr); without the dubbing service it runs subtitles only. Long run, log to a file:
#   pwsh -NoProfile -File bench\run_m2_live.ps1 > .tmp\m2_live.log 2>&1
param([int]$T = 180, [int]$SS = 1440, [string]$Config = 'bench\configs\devday.json', [string]$Dir = '.tmp\live')
$ErrorActionPreference = 'Continue'
$P = Split-Path $PSScriptRoot -Parent
Set-Location $P
$env:PYTHONIOENCODING = 'utf-8'
$uv = (Get-Command uv).Source
$D = (Get-Content $Config -Raw | ConvertFrom-Json).delay_s

$Dq = [regex]::Escape($Dir)  # only this test's processes: another test may run in another -Dir
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "livedub\.(ingest|playout|processor) .*$Dq" -or ($_.Name -eq 'ffmpeg.exe' -and $_.CommandLine -match $Dq) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
if (-not (Get-Process mediamtx -ErrorAction SilentlyContinue)) { 'mediamtx is not running'; exit 1 }
try { $h = Invoke-RestMethod -TimeoutSec 3 http://127.0.0.1:8091/health } catch { 'asr service is not running'; exit 1 }
try { $h = Invoke-RestMethod -TimeoutSec 3 http://127.0.0.1:8092/health } catch { 'dubbing service is not running: subtitles only' }
$L = Join-Path $P $Dir
$name = Split-Path $Dir -Leaf
Remove-Item $L -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $L | Out-Null

"[{0:HH:mm:ss}] start: T={1}s D={2}s" -f (Get-Date), $T, $D
$start = { param($name, $argv) Start-Process -FilePath $uv -ArgumentList $argv -RedirectStandardOutput "$L\$name.log" -RedirectStandardError "$L\$name.err" -PassThru -WindowStyle Hidden }
$ing = & $start 'ingest' @('run', 'python', '-m', 'livedub.ingest', 'data\devday2026-keynote.mkv', "$Dir\buf", '--ss', "$SS", '--t', "$T")
Start-Sleep 1
$play = & $start 'playout' @('run', 'python', '-m', 'livedub.playout', "$Dir\buf", "$Dir\events.jsonl", "rtmp://127.0.0.1:1935/live/$name", '--delay', "$D")
$proc = & $start 'processor' @('run', 'python', '-m', 'livedub.processor', "$Dir\buf", "$Dir\events.jsonl", $Config, '--clips', "$Dir\clips")
Start-Sleep ($D + 6)
"[{0:HH:mm:ss}] start recorder" -f (Get-Date)
# memory / disk every 2 minutes while the recorder runs
$mon = Start-Job -ArgumentList $L, $Dq -ScriptBlock { param($L, $Dq)
    while ($true) {
        $ps = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "livedub\.(playout|processor) .*$Dq" -and $_.Name -eq 'python.exe' }
        $row = [ordered]@{ t = (Get-Date -f 'HH:mm:ss') }
        foreach ($x in $ps) { $k = if ($x.CommandLine -match 'playout') { 'playout_mb' } else { 'processor_mb' }; $row[$k] = [int]($x.WorkingSetSize / 1MB) }
        $row['buf_mb'] = [int]((Get-ChildItem "$L\buf" -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB)
        ($row | ConvertTo-Json -Compress) | Add-Content "$L\mem.jsonl"
        Start-Sleep 120
    } }
& ffmpeg -hide_banner -loglevel warning -rw_timeout 20000000 -i rtmp://127.0.0.1:1935/live/$name -c copy -t ($T - 10) "$L\rec.mp4"
"[{0:HH:mm:ss}] recorder done rc=$LASTEXITCODE" -f (Get-Date)
Stop-Job $mon; Remove-Job $mon
$play.WaitForExit(90000) | Out-Null
$proc.WaitForExit(30000) | Out-Null
"[{0:HH:mm:ss}] all done" -f (Get-Date)

# Live test: a real live source (the config's "src", or -Src) -> ingest --live -> processor -> playout (--gc) -> MediaMTX,
# with a recorder of the pushed stream and memory / disk sampling. Needs MediaMTX and the ASR service running; the
# dubbing service is optional (subtitles only without it). Long run, log to a file:
#   pwsh -NoProfile -File bench\run_live.ps1 -T 3600 > .tmp\live_run.log 2>&1
param([int]$T = 600, [string]$Config = 'bench\configs\dw-news.json', [string]$Src = '', [string]$Dir = '.tmp\live_run',
      [string]$Cookies = '', [switch]$NoRecord, [switch]$Keep)
$ErrorActionPreference = 'Continue'
$P = Split-Path $PSScriptRoot -Parent
Set-Location $P
$env:PYTHONIOENCODING = 'utf-8'
$uv = (Get-Command uv).Source
$cfg = Get-Content $Config -Raw | ConvertFrom-Json
$D = $cfg.delay_s
if (-not $Src) { $Src = $cfg.src }
if (-not $Cookies -and $cfg.cookies) { $Cookies = $cfg.cookies }

$Dq = [regex]::Escape($Dir)
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

"[{0:HH:mm:ss}] start: T={1}s D={2}s src={3}" -f (Get-Date), $T, $D, ($Src -replace '\?.*$', '')
$start = { param($n, $argv) Start-Process -FilePath $uv -ArgumentList $argv -RedirectStandardOutput "$L\$n.log" -RedirectStandardError "$L\$n.err" -PassThru -WindowStyle Hidden }
$ingArgs = @('run', 'python', '-m', 'livedub.ingest', $Src, "$Dir\buf", '--live', '--t', "$T")
if ($Cookies) { $ingArgs += @('--cookies', $Cookies) }
if ($cfg.max_height) { $ingArgs += @('--max-height', "$($cfg.max_height)") }
if ($Keep) { $ingArgs += @('--keep', "$Dir\keep") }  # the source as pulled, for bench/av_drift.py against rec.mp4
$ing = & $start 'ingest' $ingArgs
Start-Sleep 1
$playArgs = @('run', 'python', '-m', 'livedub.playout', "$Dir\buf", "$Dir\events.jsonl", "rtmp://127.0.0.1:1935/live/$name", '--delay', "$D", '--gc', '--tag', "`"$($cfg.tag)`"")
foreach ($k in 'sub_bottom', 'sub_box', 'tag_corner') { if ($null -ne $cfg.$k) { $playArgs += @('--' + $k.Replace('_', '-'), "$($cfg.$k)") } }
$play = & $start 'playout' $playArgs
$proc = & $start 'processor' @('run', 'python', '-m', 'livedub.processor', "$Dir\buf", "$Dir\events.jsonl", $Config, '--clips', "$Dir\clips")
$mon = Start-Job -ArgumentList $L, $Dq -ScriptBlock { param($L, $Dq)
    while ($true) {
        $ps = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "livedub\.(playout|processor) .*$Dq" -and $_.Name -eq 'python.exe' }
        $row = [ordered]@{ t = (Get-Date -f 'HH:mm:ss') }
        foreach ($x in $ps) { $k = if ($x.CommandLine -match 'playout') { 'playout_mb' } else { 'processor_mb' }; $row[$k] = [int]($x.WorkingSetSize / 1MB) }
        $row['buf_mb'] = [int]((Get-ChildItem "$L\buf" -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB)
        $row['clips_mb'] = [int]((Get-ChildItem "$L\clips" -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB)
        ($row | ConvertTo-Json -Compress) | Add-Content "$L\mem.jsonl"
        Start-Sleep 120
    } }
Start-Sleep ($D + 8)
if (-not $NoRecord) {
    for ($i = 0; $i -lt 30; $i++) {
        if ($play.HasExited) { "[{0:HH:mm:ss}] playout exited (rc={1}), no recording" -f (Get-Date), $play.ExitCode; break }
        "[{0:HH:mm:ss}] start recorder" -f (Get-Date)
        & ffmpeg -hide_banner -loglevel warning -rw_timeout 20000000 -i "rtmp://127.0.0.1:1935/live/$name" -c copy -t ($T - 10) "$L\rec.mp4"
        "[{0:HH:mm:ss}] recorder done rc=$LASTEXITCODE" -f (Get-Date)
        if ((Test-Path "$L\rec.mp4") -and (Get-Item "$L\rec.mp4").Length -gt 1MB) { break }
        Start-Sleep 10
    }
}
$ing.WaitForExit(($T + 120) * 1000) | Out-Null
$play.WaitForExit(($D + 90) * 1000) | Out-Null
$proc.WaitForExit(60000) | Out-Null
Stop-Job $mon; Remove-Job $mon
"[{0:HH:mm:ss}] all done" -f (Get-Date)

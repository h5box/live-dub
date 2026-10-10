# Build a self-contained Windows installer; models remain first-run downloads.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "构建失败，退出码 $LASTEXITCODE" } }
npx pnpm --dir app install --frozen-lockfile
Check-Exit
npx pnpm --dir app build
Check-Exit
New-Item -ItemType Directory -Force .tmp/desktop-configs | Out-Null
Get-ChildItem -LiteralPath .tmp/desktop-configs -File | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
Get-ChildItem -LiteralPath configs -Filter *.json -File | Copy-Item -Destination .tmp/desktop-configs
New-Item -ItemType Directory -Force .tmp/demo | Out-Null
Add-Type -AssemblyName System.Speech
$voice = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$voice.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::NotSet, [System.Speech.Synthesis.VoiceAge]::NotSet, 0, [Globalization.CultureInfo]::GetCultureInfo('en-US'))
$voice.SetOutputToWaveFile((Join-Path $PWD '.tmp/demo/demo.wav'))
$voice.Speak('Welcome. This video tests English recognition and Chinese subtitles. You can pause the video and click a subtitle to watch that sentence again.')
$voice.Dispose()
ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'color=c=0x172b4d:s=960x540:r=25' -i .tmp/demo/demo.wav -c:v libx264 -g 25 -c:a aac -shortest .tmp/demo/demo.mp4
Check-Exit
uv run --no-sync pyinstaller --noconfirm --clean --name LiveDubBackend --distpath dist/desktop --workpath .tmp/pyinstaller --specpath .tmp --paths $PWD --collect-all sherpa_onnx --collect-all soundfile --collect-all yt_dlp --collect-all streamlink --hidden-import livedub.server.__main__ --hidden-import livedub.asr --hidden-import livedub.ingest --hidden-import livedub.processor --hidden-import livedub.playout --hidden-import scripts.fetch --add-data "$PWD/.tmp/desktop-configs;configs" --add-data "$PWD/app/dist;app/dist" --add-data "$PWD/dubsvc;dubsvc" --add-data "$PWD/svc;svc" --add-data "$PWD/scripts/linux;scripts/linux" --add-data "$PWD/THIRD_PARTY.md;." --add-data "$PWD/LICENSE;." --add-data "$PWD/licenses;licenses" --add-data "$PWD/.tmp/demo/demo.mp4;." scripts/desktop.py
Check-Exit
Push-Location app
try { npx pnpm tauri build --config src-tauri/tauri.windows.conf.json; Check-Exit } finally { Pop-Location }

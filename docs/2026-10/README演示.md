# README 演示

目标：README 首屏先让人看到字幕叠在画面上、配音和口型对得上，再决定要不要下载。

| 文件 | 规格 | 大小 |
| --- | --- | --- |
| `docs/media/demo.mp4` | AV1 1280×720 23.976 fps，AAC 128k，27.5 秒 | 2.9 MB |
| `docs/media/demo.avif` | 首屏动图，AV1 960×540，12.2 秒，无声；点它打开 demo.mp4 | 0.6 MB |
| `docs/media/app.webp` | 观看页截图，1440×712 | 93 KB |

## 素材

《Tears of Steel》（Blender Foundation，CC BY 3.0），原片 0:18 起 32 秒的桥上对话。两个人、7 句英文，男女声都有，句子有长有短。

```
ffmpeg -ss 18 -i https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov -t 32 -map 0:v:0 -map 0:a:0 -c:v libx264 -preset slow -crf 14 -pix_fmt yuv420p -c:a aac -b:a 192k data/tos_bridge.mp4
```

场景预设放在 `configs/local/tos.json`，不入库：

```json
{"name": "Tears of Steel（演示）", "src": "data/tos_bridge.mp4", "domain": "英文科幻短片", "delay_s": 30,
 "asr_boost": ["Thom", "Celia"], "names": ["Thom", "Celia"],
 "glossary": {"Thom": "Thom", "Celia": "Celia", "robot hand": "机械手", "robotics": "机器人"},
 "mt_target_cps": 5.0, "speaker_threshold": 0.45, "duck_db": -15, "tag": "AI 翻译配音", "sub_bottom": 14}
```

## 做法

1. 跑一场带配音的实跑：翻译用 deepseek-flash，配音用 IndexTTS-2.5。当时开着「输出到 OBS」，按实时节奏处理；点播模式跑出来的场次也能用。
2. 离线渲染：把场次目录里的 `buf/` 和 `events.jsonl` 拷到一个新目录，不带 `anchor.json`。这样 playout 会自己定锚点，按实时节奏跑；配音按 events 里记的路径去读。

   ```
   python -m livedub.playout <目录>/buf <目录>/events.jsonl master.mkv --delay 1 --bitrate 20M --sub-bottom 14
   ```

3. 从第 3 秒起截 27.5 秒，缩到 720p，作为中间文件：

   ```
   ffmpeg -ss 3.0 -i master.mkv -t 27.5 -vf scale=1280:720:flags=lanczos -c:v libx264 -preset slow -crf 12 -pix_fmt yuv420p -c:a flac ds_720.mkv
   ```

4. 编码成 AV1，右上角烧上署名。playout 输出的是 29.97 fps，`decimate=cycle=5` 去掉重复帧，回到 23.976 fps：

   ```
   ffmpeg -i ds_720.mkv -vf "decimate=cycle=5,drawtext=fontfile=DejaVuSans.ttf:text='Tears of Steel  (CC BY 3.0)  Blender Foundation | mango.blender.org':fontsize=15:fontcolor=white@0.6:x=w-tw-22:y=22" -c:v libsvtav1 -preset 4 -crf 34 -g 240 -pix_fmt yuv420p -svtav1-params tune=0 -c:a aac -b:a 128k -movflags +faststart demo.mp4
   ffmpeg -ss 1.6 -t 12.2 -i demo.mp4 -an -vf scale=960:540:flags=lanczos -c:v libsvtav1 -preset 4 -crf 36 -g 120 -pix_fmt yuv420p -f avif demo.avif
   ```

5. 截图：同一片段用点播模式（不开 OBS）再跑一场，在无头 Edge 里以 1440×900 打开观看页，出字幕时截图，裁掉下方空白（1440×712），转成 WebP，质量 88。

## 结果

- CRF 选 34。试过 34、38、42，文件分别是 2.9、2.4、2.0 MB；从 38 起字幕边缘和人脸开始发糊。
- 本地 index-9b 跑了一场：8 句都配上了音，但识别把一句听错了，译成「死去的巨型机械爪子」。
  DeepSeek 跑的那场：7 句都配上了音，译文更顺，演示用的是这一场。同一片段每次分句和译文略有不同。
- 点播模式：开始后约 14 秒首段字幕就绪，然后从头播放。仅字幕再用 index-9b 跑一场，32 秒的片段 9 秒内全部译完。
- AV1 在 Chrome、Edge、Firefox 上能播；Safari 要设备有 AV1 硬件解码（iPhone 15 Pro、M3 及以后的机型）。
  GitHub 不会在 README 里直接播放仓库里的 mp4。要内嵌播放器，得在 github.com 上编辑 README 时把 mp4 拖进去，用生成的附件链接。

## 实跑中发现并修复的问题

| 提交 | 问题 |
| --- | --- |
| f923134 | OBS 输出把 2.39:1 的片子拉伸成 16:9；现在按比例缩放，上下补黑边 |
| 4fbebda | 设置已经改成原生识别，但旧的 WSL 识别服务还在跑，处理器读不到 Windows 路径；现在两边不一致时按设置重启识别服务 |
| 51dcf77 | 短句常常自成一个说话人，借参考音时拿了「最近一个」，男声的句子配成了女声；现在借音色最像的说话人的参考 |
| a5d9913 | 新开一场时，状态还是上一场的，播放器按上一场的锚点跳到了片尾；起播时首帧比处理范围早几微秒，被当成「未处理」，结果整段重做；OBS 场次拖动时弹出误报 |

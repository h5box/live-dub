# live-dub 界面

Vue 3 + TypeScript，外面可以套 Tauri 2 成为桌面程序，也可以直接在浏览器里开。界面只是一个窗口：真正干活的是 Python 后端
`livedub.server`（127.0.0.1:8090）。桌面程序打开时后端没在跑就把它拉起来；关掉窗口，后端和正在进行的翻译照旧。

## 构建和运行

在 `app/` 里（要 Node.js；`npx pnpm` 不用全局装 pnpm）：

- 浏览器用：`npx pnpm install`，`npx pnpm build` → `dist/`。后端在 http://127.0.0.1:8090/ 托管它
  （项目目录 `uv run python -m livedub.server --open`）。浏览器拿不到本地文件的路径，本地视频要粘贴路径。
- 桌面程序：另外要 [Tauri 的构建环境](https://tauri.app/start/prerequisites/)，`npx pnpm tauri build --no-bundle` →
  `src-tauri/target/release/live-dub(.exe)`。它往上找项目目录（含 `livedub/server` 的那一层）来起后端，所以要留在项目里面，
  或者设 `LIVEDUB_HOME`。起后端时优先用项目的 `.venv`，没有就用 `uv run`；后端日志在 `logs/server.log`。
- 开发：`npx pnpm tauri dev`（界面热更新；后端要先手动起）。
- Windows 安装包：根目录运行 `scripts/build_desktop.ps1`，内置 Python 后端与测试片，模型由首次向导下载。

## 不起服务、不占显卡改界面

项目目录运行假后端 `uv run python app/dev/mock_server.py`（127.0.0.1:8099）：把 `outputs/live/` 里最近一场有记录的场次
当成正在进行回放，画面是带节目时间的测试图，配音用等长的提示音代替；预设、模型、历史场次、环境检查直接调后端的代码。

```
uv run python app/dev/mock_server.py [--scenario live|ended|fresh|starting|failed] [--run outputs/live/<场>] [--media fmp4]
cd app
VITE_API=http://127.0.0.1:8099 npx pnpm dev          # PowerShell：$env:VITE_API = "http://127.0.0.1:8099"; npx pnpm dev
```

浏览器开 http://localhost:1420 。`fresh` 是没开过场、服务都没起的样子（环境检查用这台机器的真实结果）。
浏览器没有 H.264 解码（比如 Playwright 自带的 Chromium）就加 `--media fmp4`（VP9 / Opus）。

## 页面

- **观看**：默认显示源与配音开关；场景、直播延时、翻译模型、OBS 位于高级选项。开始后显示标题、状态和停止。
  中间是播放器：hls.js 读这一场的分段，字幕按节目时间画在画面上（中英 / 中文 / 原文 / 关），配音由 WebAudio 按节目时间排程。
  直播按「第一帧 + 延时 D」播放；点播首段字幕就绪后播放，绿色区域为已处理范围，跳到未处理位置会优先生成字幕。
  空格暂停，← → 跳 5 秒，F 全屏。指标和余量图放在运行详情；右边是字幕列表与状态。
  环境还缺必需的东西时，顶上有提示，点过去看怎么装。
- **历史**：回放保留的视频、继续观看、导出中文／原文／双语 SRT，查看占用并清理缓存或删除记录。
- **设置向导**：安装原生字幕和 YouTube 组件、测试翻译接口、播放测试片；配音按需安装。
- **环境**：环境检查（缺什么、运行哪条命令补上），识别、翻译、配音、MediaMTX 的起停、说明和日志，线上翻译的 key，显卡，后端。
- 主题跟随系统，左下角可固定浅色 / 深色。

## 代码

Logo 源文件是 `public/logo.svg`，侧栏和网页 favicon 共用；没有字体或外部资源依赖。
修改后在 `app/` 运行 `npx pnpm tauri icon public/logo.svg`，同步生成桌面应用图标。
只提交 `src-tauri/icons/` 中已有的桌面图标；生成的 `android/`、`ios/` 和额外尺寸可移到 `.tmp/`。

```
src/
├── api/          types.ts 接口数据形状 · client.ts HTTP · stream.ts SSE（status 每 2 秒，这一场的字幕和耗时记录随写随推）
├── stores/       live.ts 整个窗口一条 SSE、播放位置、开始停止和服务起停、环境检查 · history.ts 历史场次
├── lib/          dubAudio.ts 配音混音（和 livedub/playout.py 同一套规则）· rows.ts 字幕行（events + timing 按 pt0 合并）
│                 · stats.ts · format.ts · prefs.ts（主题、表单、播放器设置的记忆）· serviceInfo.ts
├── components/   Player、SourceBar、SubtitleTimeline / SubtitleLine、SlackChart、Kpi、ServiceList、NavRail、Icon（自绘图标）…
├── views/        LiveView、HistoryView、ServicesView、SetupView（首次向导）
└── styles/       tokens.css 颜色（light-dark() 一处写两套）· base.css 按钮、表单、卡片
dev/mock_server.py   改界面用的假后端
```

时间：拉流在第一段写好时写 `anchor.json`（T0、p0、D），节目时间 PT 为 p 的画面默认在 T0 + D + (p − p0) 播出；
后端放在 `/api/status` 的 `health.anchor` 里。播放列表 `/api/sessions/<场>/media/index.m3u8` 每段带
`EXT-X-PROGRAM-DATE-TIME`（1970-01-01 加上该段起点的 PT），播放器据此在媒体时间和 PT 之间换算。
余量 = 写出时离默认播放时刻还有多少秒（字幕 `slack_s`，配音 `left_s`），低于 5 秒标黄。

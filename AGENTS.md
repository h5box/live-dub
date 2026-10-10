# 参与开发

## 项目导航
- live-dub（中文名：声影同传）：外语直播或视频的实时中文字幕与原音色中文配音；用户入口见 README.md。
- 技术栈：Python、Vue 3 / TypeScript、Tauri 2；识别 CPU，翻译与配音可用 GPU。
- 后端入口：`uv run python -m livedub.server`，默认 127.0.0.1:8090。
- 环境：Windows 原生字幕，配音需 WSL 2；Linux 默认 NeMo；`uv sync` 安装，doctor 检查。
- 先读存在的 AGENTS.local.md；机器专属信息只放该文件或 settings.json，不进 git。
- 前端开发和验证见 app/AGENTS.md；实验索引见 bench/README.md。

## 项目目录树
```text
live-dub/
├── livedub/         本机处理链：settings、ingest、processor、mt、playout、subs
│   └── server/     接口 app、场次 session、服务 services、进程 procs
├── svc/            NeMo CPU 识别；原生 ONNX 入口 livedub/asr.py
├── dubsvc/         Linux GPU 配音服务
│   └── omni/       vLLM-Omni 补丁和部署配置
├── app/            Vue 界面和 Tauri 外壳，详见 app/AGENTS.md
├── configs/        内置场景；local/ 为不入库的个人预设
├── scripts/        fetch.py 下载工具与模型；linux/ 安装 Linux / WSL 环境
├── bench/          实验、测试、分析；configs/ 测试源
└── docs/           用户文档；dev/ 调研与实测记录；media/ README 演示素材
```

## 项目架构图
```text
源 → ingest 切块/锚点 → processor → ASR → 断句/说话人 → MT → 配音
                         ↓ events.jsonl / timing.jsonl
server 场次/服务接口 → Vue 播放器：延时画面 + PT 字幕 + WebAudio 配音
                     → 可选 playout：烧字幕/混音/编码 → 本机 OBS
Tauri 外壳 → 启动 Python 后端；后端托管 app/dist
```

## 项目文档
- README.md：使用、安装与运行入口；LICENSE、THIRD_PARTY.md、licenses/：自有与第三方许可。
- docs/原理.md：时间锚点和处理链；docs/配置.md：设置项；docs/接口.md：后端接口。
- docs/安装.md：完整环境安装；app/README.md：前端构建、假后端、页面与代码。
- docs/2026-10/大众用户改进.md：安装、原生识别与观看体验改进状态和验证边界。
- docs/2026-10/README演示.md：首屏演示的素材、生成命令和实跑结果。
- bench/README.md：实验和分析脚本；docs/dev/README.md：实测与调研导航。

## 项目约束
- 画面不等处理：赶不上播出就去配音、退回原文、最终不叠加；下一句照常。
- 源 PT 是唯一对齐时钟：播出 = T0 + D + (PT − p0)，不使用墙钟对齐字幕和配音。
- 路径、端口、工具和 Linux 环境走 livedub/settings.py；新增设置有默认值并更新配置文档。
- 进程用 server/procs.py 的 spawn；Linux 命令用 settings.linux_argv，路径用 to_linux。
- 用户界面、错误和提示日志用中文；代码注释用英文解释原因；代码和数据路径不用中文。
- 密钥只放 .secret/，模板在 .secret.example/；不得记录到日志。
- 不入库：settings.json、.secret/、configs/local/、tools/、models/、outputs/、logs/、data/、.tmp/。
- 改行为同步对应 docs；实测记 docs/dev/；新任务文档按 docs/YYYY-MM/中文.md 存放。
- Python 用 uv，检查用 pyright，测试用 pytest；PowerShell 读取用 Get-Content -Encoding utf8。
- 提交前前端 npx pnpm build；后端修改还须 doctor 和至少一场仅字幕实跑。
- 无 GPU 测试可用 CPU 识别、线上或假兼容翻译接口、关闭配音；公开授权语音合成测试视频。
- 有 GPU 时整链用 bench/run_m2_live.ps1；录像由 bench/fetch_devday.ps1 获取。
- 每轮完整修改后提交；提交中文格式 type:简短说明，正文列出修改；不提交需告知用户。
- 采用最简单的模块化实现和成熟库；删过时代码，不保留兼容层或预防性抽象。
- 可重建工具、缓存和模型分别放 F:/Dev、F:/Cache、F:/Models；唯一数据不放 F:。

## 项目记忆
- 10-10 06:57：自有代码采用 MIT；第三方衍生文件保留原许可；GitHub 为 h5box/live-dub。
- 10-10 06:57：首次公开只发布当前快照，保留本地旧历史；后续发布沿公开分支继续。
- 10-10 07:30：Windows 默认原生 ONNX 识别；保留 NeMo 热词方案；字幕优先、配音按需安装。
- 10-10 07:36：点播提前处理并支持跳转优先；OBS 沿用实时处理；已生成字幕和时间范围保留。
- 10-10 07:36：历史支持回放、续看、SRT 导出；清理缓存保留字幕，删除整场需界面确认。
- 10-10 07:56：0.2.0 预览版已发布，独立 Windows 构建通过；干净机安装与用户试用仍待验证。
- 10-10 09:15：README 首屏演示用 Tears of Steel（CC BY 3.0）片段，AV1 视频、AVIF 动图和截图放 docs/media/。

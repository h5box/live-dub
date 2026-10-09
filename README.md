# live-dub

把英语直播或视频翻译给自己看：中文字幕，可选原音色中文配音，**按源时间戳与画面对齐**。

贴一个直播或视频链接（YouTube、Twitch、m3u8 等），或者选一个本地视频，点「开始」。直播画面延时 D 秒播放
（默认 30 秒），这段时间里先识别、翻译和配音。点播提前处理，首段字幕准备好后播放；拖到未处理位置会优先处理那里。

- 识别：NVIDIA Parakeet（英文，带词级时间戳），在 CPU 上跑；TitaNet 声纹区分说话人。
- 翻译：本地 Index-Translate-9B（llama.cpp），或者线上的 DeepSeek / 任何 OpenAI 兼容接口。
- 配音：IndexTTS-2.5，用每个说话人自己的原声当参考，按原句的时长合成。
- 播放：程序里直接看，字幕叠在画面上（中英 / 只中文 / 只原文 / 关），配音时原声自动压低；可以暂停、回看、点一句字幕跳过去。
- 可选「输出到 OBS」：把带字幕和配音的画面推到本机 OBS。

目前源语言只支持英语，译成简体中文。

## 使用须知

- 这是给自己看外语内容的工具。请只用于你有权观看的内容，遵守内容的版权和平台的服务条款；不要用它录制、转载或二次分发别人的内容。
- 「输出到 OBS」只是把画面交给你本机的 OBS。推不推流、推到哪里由你自己决定，相关的版权和法律责任由你自己承担。
- 配音会克隆原说话人的音色，只是为了让译文听起来像原话。不要用它冒充他人，或者做误导别人的内容。
- 用到的模型各有许可，有的只许非商业使用（配音链路里的 MaskGCT 语义编解码器是 CC BY-NC 4.0），见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 怎么做到和嘴对上

常见的同传工具追求低延迟：听完一句、识别、翻译、马上上屏马上念，所以字幕和配音总比嘴晚好几秒，而且每句晚得不一样。

这里反过来：**整路画面固定晚 D 秒播放**，所有环节都按源视频自己的时间戳记账。一句话在源里从 t0 说到 t1，
只要它的识别、翻译、配音在 t0 + D 之前做完，就能在画面播到 t0 的那一刻上字幕、开始念配音。
所以模型不用流式，可以按整句、带上下文、按原句时长来做。赶不上的句子降级（去掉配音，再不行只留原文），**画面从不等任何环节**。
详细设计见 [docs/原理.md](docs/原理.md)。

## 需要什么

| 想要的效果 | 需要 |
| --- | --- |
| 中文字幕（线上翻译） | 4 核以上 CPU、16 GB 内存；Windows 默认原生 ONNX，无需 WSL；Linux 默认 NeMo |
| + 本地翻译 | NVIDIA 显卡，约 5 GB 显存 |
| + 中文配音 | NVIDIA 显卡，约 6.5 GB 显存；和本地翻译一起约 12 GB，还须给系统与播放器留空间 |

磁盘：原生识别模型约 0.8 GB，NeMo 识别环境约 6 GB，翻译模型约 5.5 GB，配音环境约 20 GB。
在 Windows 11 + WSL 2（Ubuntu 22.04）+ RTX 5060 Ti 16 GB 上开发和实测；
Linux 上同一套代码可以跑，但测得少。

## 安装

普通 Windows 用户优先下载 [GitHub Releases](https://github.com/h5box/live-dub/releases) 的安装程序。
安装后打开「设置向导」，下载字幕组件、填写并测试翻译接口，再播放内置英语测试片。
字幕模式无需 Python、Node.js、WSL 或独立显卡；线上翻译按接口供应商价格计费。
模型可选择存放目录。原音色配音是可选组件，仍需 NVIDIA 显卡与 WSL 2。

下面是从源码运行的方法。

下面是最短的路径，每一步的细节、常见问题见 [docs/安装.md](docs/安装.md)。装完随时可以运行环境检查，它会告诉你还缺什么、怎么补：

```
uv run python -m livedub.doctor
```

### Windows

1. 装好基础工具（PowerShell 里）：

   ```powershell
   winget install Git.Git
   winget install Gyan.FFmpeg
   winget install DenoLand.Deno
   winget install OpenJS.NodeJS.LTS
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   装完重开一个 PowerShell 窗口，新装的命令才找得到。

   Deno 是给 yt-dlp 解 YouTube 的播放校验用的（装 Node.js 也行）；Node.js 用来构建界面。

2. 仅配音或 NeMo 识别需要 WSL 2：在管理员 PowerShell 运行 `wsl --install`，重启后建 Ubuntu 用户。
   NVIDIA 驱动只在 Windows 里装，WSL 里不用另装。

3. 下载代码，装 Python 依赖：

   ```powershell
   git clone https://github.com/h5box/live-dub.git
   cd live-dub
   uv sync
   ```

4. Windows 默认使用原生 ONNX 识别：构建界面并启动后，在「设置向导」下载模型（约 0.8 GB）。
   如需 NeMo 识别热词增强，先将 `settings.json` 的 `asr.backend` 设为 `nemo`，再安装：

   ```powershell
   wsl -e bash scripts/linux/setup_asr.sh
   ```

5. 翻译，二选一：
   - 本地：`uv run python scripts/fetch.py all`（llama.cpp 的服务端加 Index-Translate-9B，约 5.5 GB）；
   - 线上：先跳过，打开程序后在「环境」页填 DeepSeek 或其他 OpenAI 兼容接口的 API key。

6. 中文配音（可选，约 20 GB）：`wsl -e bash scripts/linux/setup_dub.sh`

7. 输出到 OBS（可选）：`uv run python scripts/fetch.py mediamtx`

### Linux

```bash
sudo apt install git ffmpeg curl            # 再装 Deno 或 Node.js（看 YouTube 用）和 Node.js（构建界面）
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/h5box/live-dub.git && cd live-dub
uv sync
bash scripts/linux/setup_asr.sh             # 识别（必需）
uv run python scripts/fetch.py all          # 本地翻译（或者用线上翻译）
bash scripts/linux/setup_dub.sh             # 中文配音（可选，要 NVIDIA 显卡）
```

国内网络：Hugging Face 慢就设 `HF_ENDPOINT=https://hf-mirror.com`，PyPI 慢就设 `UV_DEFAULT_INDEX` 指向国内镜像，
GitHub 慢就给 `scripts/fetch.py` 加 `--github-mirror`，见 [docs/安装.md](docs/安装.md)。

## 运行

先构建界面（一次就行，要 Node.js）：

```
cd app
npx pnpm install
npx pnpm build
cd ..
```

然后二选一：

- **浏览器**：`uv run python -m livedub.server --open`，会打开 http://127.0.0.1:8090 。本地视频要粘贴文件路径。
- **桌面程序**（可以选择、拖入本地视频）：另外装好 [Tauri 的构建环境](https://tauri.app/start/prerequisites/)（Rust；Windows 还要 MSVC），
  在 `app/` 里运行 `npx pnpm tauri build --no-bundle`，得到 `app/src-tauri/target/release/live-dub(.exe)`，双击它就行，后端会自己起来。

用法：

1. 在「设置向导」装好字幕组件并测试翻译接口。在「观看」页贴链接或选视频，点「开始」，默认仅字幕。
2. 场景、翻译模型、直播延时、OBS 输出位于「高级选项」。勾选中文配音会自动启动已安装的配音服务。
3. 直播等待 D 秒后播放；点播在首段字幕就绪后播放，进度条绿色区域表示已处理部分。
   可暂停、拖动、点字幕跳转；直播用「回到实时」恢复默认位置。空格暂停，← → 跳 5 秒，F 全屏。
4. 「历史」页可回放保留的视频、继续上次位置、导出中文／原文／双语 SRT，查看占用并清理缓存或删除整场记录。
   清理缓存保留字幕和统计；删除记录会移除这一场的全部文件，两者都需要确认。

## 设置

不建设置文件也能用：默认值就是安装脚本放东西的位置。要改路径、端口、默认翻译模型、YouTube cookies 等，
照 [settings.example.json](settings.example.json) 在项目目录建一个 `settings.json`，只写要改的项，说明见 [docs/配置.md](docs/配置.md)。
自己的场景（术语表、热词）放在 `configs/local/`。

## 文档

- [docs/安装.md](docs/安装.md)：安装细节和常见问题
- [docs/配置.md](docs/配置.md)：`settings.json`、场景预设、线上翻译的 key
- [docs/原理.md](docs/原理.md)：设计与时间轴（固定延时、锚点、降级）
- [docs/接口.md](docs/接口.md)：后端 API、识别和配音服务的 HTTP 接口
- [AGENTS.md](AGENTS.md)：参与开发（代码结构、怎么在没有显卡的机器上改界面）
- [docs/dev/](docs/dev/)：开发过程中的选型调研和实测记录

## 路线图

- 干净 Windows 机器安装验证、非技术用户试用和下载成功率改进。
- 在界面里编辑场景的术语表和识别热词。
- 更多源语言。

## 许可证

本项目自有代码采用 [MIT 许可证](LICENSE)。`dubsvc/engine.py` 等第三方衍生文件遵循各自原许可，
不包含在 MIT 授权范围内；完整说明及许可文本见 [THIRD_PARTY.md](THIRD_PARTY.md) 和 [licenses/](licenses/)。
模型权重不随仓库分发，其使用限制不会因本项目采用 MIT 而改变。

## 致谢

[NVIDIA NeMo](https://github.com/NVIDIA/NeMo)（Parakeet、TitaNet）、[Index-Translate](https://huggingface.co/IndexTeam/Index-Translate-9B)、
[IndexTTS](https://github.com/index-tts/index-tts)、[vLLM-Omni](https://github.com/vllm-project/vllm-omni)、
[llama.cpp](https://github.com/ggml-org/llama.cpp)、[FFmpeg](https://ffmpeg.org/)、[yt-dlp](https://github.com/yt-dlp/yt-dlp)、
[streamlink](https://streamlink.github.io/)、[MediaMTX](https://github.com/bluenviron/mediamtx)、[hls.js](https://github.com/video-dev/hls.js)、
[Tauri](https://tauri.app/)、[Vue](https://vuejs.org/)。

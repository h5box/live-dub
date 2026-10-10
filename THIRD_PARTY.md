# 第三方软件和模型

本仓库不包含任何模型权重和第三方程序的二进制文件：模型由安装脚本从 Hugging Face 下载，工具由你自己装或由 `scripts/fetch.py`
从官方发布页下载。用之前请看各自的许可，尤其是模型的。

本项目的 MIT 许可证仅适用于自有代码，不替代下列第三方文件和模型的许可。
仓库附带 [IndexTTS 许可全文](licenses/IndexTTS-LICENSE.txt) 和
[vLLM-Omni Apache-2.0 许可全文](licenses/vLLM-Omni-LICENSE.txt)。

## 模型

| 模型 | 用途 | 来源 | 许可 |
| --- | --- | --- | --- |
| Parakeet-TDT-0.6B-v2 | 英文识别 | [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) | CC BY 4.0 |
| Parakeet / TitaNet ONNX | Windows 原生识别与声纹；转换自以上 NVIDIA 模型 | [Parakeet INT8](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8)、[TitaNet ONNX](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models) | CC BY 4.0；模型转换由 sherpa-onnx 社区提供 |
| TitaNet-L | 说话人声纹 | [nvidia/speakerverification_en_titanet_large](https://huggingface.co/nvidia/speakerverification_en_titanet_large) | CC BY 4.0 |
| Index-Translate-9B（GGUF） | 本地翻译（默认） | [IndexTeam/Index-Translate-9B-GGUF](https://huggingface.co/IndexTeam/Index-Translate-9B-GGUF) | Apache-2.0 |
| Hy-MT2-7B（GGUF） | 本地翻译（备选） | [tencent/Hy-MT2-7B-GGUF](https://huggingface.co/tencent/Hy-MT2-7B-GGUF) | Apache-2.0 |
| IndexTTS-2.5 | 配音 | [IndexTeam/IndexTTS-2.5](https://huggingface.co/IndexTeam/IndexTTS-2.5) | **bilibili Model Use License**：可免费使用；月活过 1 亿或年营收过 10 亿元的公司要另行申请；不得用于高风险场景；分发衍生模型要附带声明 |
| w2v-BERT 2.0 | 配音（IndexTTS 的语义特征） | [facebook/w2v-bert-2.0](https://huggingface.co/facebook/w2v-bert-2.0) | MIT |
| MaskGCT 语义编解码器 | 配音（IndexTTS 的语义码） | [amphion/MaskGCT](https://huggingface.co/amphion/MaskGCT) | **CC BY-NC 4.0（仅限非商业）** |
| CAM++ | 配音（IndexTTS 的声纹） | [funasr/campplus](https://huggingface.co/funasr/campplus) | Apache-2.0 |
| BigVGAN v2 | 配音（声码器） | [nvidia/bigvgan_v2_22khz_80band_256x](https://huggingface.co/nvidia/bigvgan_v2_22khz_80band_256x) | MIT |
| VoxCPM2 | 配音（备选后端） | [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | Apache-2.0 |

因为 MaskGCT 是非商业许可，**带配音的用法只能是非商业的**；只用字幕不受这一条限制。

## 本仓库里改写自第三方的文件

| 文件 | 改写自 | 许可 |
| --- | --- | --- |
| `dubsvc/engine.py` | [index-tts](https://github.com/index-tts/index-tts) 的 `indextts/infer_v2_5.py`（把推理拆成单遍控时的几段） | bilibili Model Use License（这个文件不随本项目的许可证）。按该许可的要求声明：本衍生作品对原模型所做的任何修改均未经原权利人认可、保证或担保，原权利人不承担与本衍生作品相关的任何责任。 |
| `dubsvc/omni/vllm-omni-v0.30.0-target_s.patch` | [vLLM-Omni](https://github.com/vllm-project/vllm-omni) v0.30.0 的补丁（按目标时长单遍合成） | Apache-2.0 |
| `dubsvc/omni/indextts2_5_16g.yaml` | vLLM-Omni 的 `vllm_omni/deploy/indextts2_5.yaml`（按 16 GB 显卡裁小） | Apache-2.0 |

## 软件

运行时用到、由 `uv sync` / `pnpm install` 装或由你自己装的：

| 软件 | 用途 | 许可 |
| --- | --- | --- |
| [FFmpeg](https://ffmpeg.org/) | 切块、转码 | LGPL-2.1+（部分构建 GPL） |
| [PyAV](https://github.com/PyAV-Org/PyAV) | 解码音频、OBS 输出的编码 | BSD-3-Clause（自带的 FFmpeg 库另有许可） |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 解析视频网站的链接 | Unlicense |
| [streamlink](https://streamlink.github.io/) | 并行拉 HLS 直播 | BSD-2-Clause |
| [FastAPI](https://fastapi.tiangolo.com/)、[Uvicorn](https://www.uvicorn.org/) | 后端 | MIT、BSD-3-Clause |
| NumPy、Pillow、psutil | | BSD-3-Clause、MIT-CMU、BSD-3-Clause |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | 本地翻译的推理服务 | MIT |
| [NVIDIA NeMo](https://github.com/NVIDIA/NeMo)、[PyTorch](https://pytorch.org/) | 识别服务 | Apache-2.0、BSD-3-Clause |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) / ONNX Runtime | 原生 CPU 识别与声纹 | Apache-2.0 / MIT |
| SoundFile / libsndfile | 读取识别音频 | BSD-3-Clause / LGPL-2.1+ |
| [vLLM](https://github.com/vllm-project/vllm)、[vLLM-Omni](https://github.com/vllm-project/vllm-omni) | 配音服务 | Apache-2.0 |
| [index-tts](https://github.com/index-tts/index-tts) | 配音（备选后端） | bilibili Model Use License |
| [MediaMTX](https://github.com/bluenviron/mediamtx) | OBS 输出的本机 RTMP 服务 | MIT |
| [Tauri](https://tauri.app/) | 桌面程序 | MIT / Apache-2.0 |
| [Vue](https://vuejs.org/)、[Vite](https://vite.dev/) | 界面 | MIT |
| [hls.js](https://github.com/video-dev/hls.js) | 播放器 | Apache-2.0 |

## 演示素材

| 文件 | 内容 | 许可 |
| --- | --- | --- |
| `docs/media/demo.mp4`、`docs/media/demo.avif`、`docs/media/app.webp` | 《Tears of Steel》原片 0:18–0:50 中的一段，剪短、重新编码，加上本项目生成的中文字幕和中文配音 | 原片 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)，© Blender Foundation \| [mango.blender.org](https://mango.blender.org/)；这几个演示文件同样以 CC BY 3.0 提供 |

配音克隆了片中演员的音色，只用来演示；画面左上角标着「AI 翻译配音」。

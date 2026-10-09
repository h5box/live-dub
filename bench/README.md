# bench：测试、分析与实验脚本

开发时做实验、测速、分析用的脚本。每个脚本开头的注释写了用法和依赖的环境（项目的 uv 环境，或 Linux 一侧的某个 venv）。
很多脚本当初是对着测试录像（`bench/fetch_devday.ps1` 下载）和作者机器上的场次目录写的，换台机器要改参数；
结果写到 `outputs/`（不进 git），`run_live.ps1` 这类新跑的默认放 `.tmp/`。测试用的源在 `configs/`。

## 整条链路（真直播 / 录像当假直播）

| 脚本 | 做什么 |
| --- | --- |
| `run_live.ps1` | 拉流 → 处理 → 播出 → MediaMTX，带录像和内存、磁盘采样；`-Keep` 另存原始流 |
| `run_m2_live.ps1` | DevDay 假直播（24:00 起）跑整条链 |
| `run_m2_test.ps1` | 外壳测试：拉流 + 播出 + 录像，不带处理器（事件来自 `make_test_events.py`） |
| `run_gap_test.ps1` | 断流测试：中途杀掉拉流，看播出冻帧、插卡、接回 |
| `obs_watch.py` | 经 obs-websocket 把输出放进 OBS 的一个测试场景集合给人看（开监听，不推流不录制） |
| `obs_check.py` | OBS 拉流检查：截图、读媒体状态 |
| `subs_preview.py` | 在真画面上预览字幕位置（`sub_bottom` / `sub_box` / `tag_corner`） |

## 一场跑完后的分析（读场次目录里的 timing / events / arrivals）

| 脚本 | 做什么 |
| --- | --- |
| `stage_breakdown.py` | 每句在各节点花的时间（说话、到货、识别、翻译、配音） |
| `lag_timeline.py` | 每两分钟的「说完到写出」 |
| `arrivals.py` | 源的到货时间线、抖动 |
| `d_whatif.py` | 延时 D 改小会有多少字幕迟到、配音丢掉 |
| `lookahead_cost.py` | 等后文再翻要花掉多少余量 |
| `av_drift.py` | 录像对源的音画偏移和漂移 |
| `check_rec.py` | 录像里字幕出现、配音开口是否对得上 |
| `hls_watch.py` | 拉流时并行记 HLS 播放列表，分清断流是源还是我们 |
| `term_consistency.py` | 术语前后是否一致 |

## 翻译

| 脚本 | 做什么 |
| --- | --- |
| `mt_replay_chat.py` | **现用**：按顺序重放一场的句子给线上对话式翻译（DeepSeek），`--prompt` / `--thinking` / `--compact-at` 对比 |
| `mt_replay.py` | 本地 llama-server 的提示、采样变体重放 |
| `mt_index.py` | Index-Translate 2B / 9B 重放 |
| `mt_polish.py` | 二次润色实验 |
| `mt_v2.py`、`mt_hymt2.py` | M1 / M0 的 Hy-MT2 实验 |

## 配音

| 脚本 | 做什么 |
| --- | --- |
| `tts_omni.py` | **现用路径**（IndexTTS-2.5 + vLLM-Omni）测速、控时误差 |
| `tts_single_pass.py`、`run_speed.sh` | 单遍控时对比两遍法，各种加速方案 |
| `tts_overhead.py` | 每次调用固定开销拆解 |
| `tts_indextts.py` | M0 的 IndexTTS 测速 |
| `tts_voxcpm.py` | VoxCPM2 测速（10-04 试过，没用上） |
| `lead_silence.py` | 配音片头静音 |

## 识别与说话人

| 脚本 | 做什么 |
| --- | --- |
| `asr_parakeet.py` | M0 Parakeet 测速、热词 |
| `diar_nemotron.py` | M0 Nemotron 说话人分离 |
| `spk_cluster.py` | 处理器用的 TitaNet 在线聚类，离线验证门槛 |
| `pick_refs.py` | 每个说话人挑参考音 |

## 其他

- `compose_demo.py`：离线样片（烧双语字幕、混配音出 mp4）。
- `fetch_devday.ps1`：下载测试录像（OpenAI DevDay 2026 Keynote）到 `data/`，`configs/devday.json` 用它。

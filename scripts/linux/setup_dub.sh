#!/usr/bin/env bash
# The default dubbing backend: IndexTTS-2.5 served by vLLM-Omni. Needs an NVIDIA GPU with about 6.5 GB free (the
# driver must be visible here: nvidia-smi). Installs vLLM 0.30.0 + vLLM-Omni 0.30.0 in their own venv, patched for
# single-pass duration control (dubsvc/omni/*.patch), and downloads the IndexTTS-2.5 weights plus the four helper
# models it loads. About 20 GB in $LIVEDUB_HOME (default ~/live-dub). Re-runnable.
#   Linux:   bash scripts/linux/setup_dub.sh
#   Windows: in the project directory, wsl -e bash scripts/linux/setup_dub.sh
# IndexTTS-2.5 is under the bilibili Model Use License (see THIRD_PARTY.md): read it before you use the weights.
source "$(dirname "$0")/common.sh"
need_gpu
command -v git >/dev/null || die "需要 git（sudo apt install git）"
need_uv
V=$LIVEDUB_HOME/venv-omni
log "Python 环境 $V"
[ -x "$V/bin/python" ] || uv venv --python 3.11 "$V"
VIRTUAL_ENV=$V uv pip install vllm==0.30.0 --torch-backend=auto
VIRTUAL_ENV=$V uv pip install 'vllm-omni[indextts2]==0.30.0' soundfile
O=$LIVEDUB_HOME/vllm-omni
log "vLLM-Omni 源码 $O（打补丁）"
[ -d "$O/.git" ] || git clone https://github.com/vllm-project/vllm-omni.git "$O"
git -C "$O" fetch --depth 1 origin tag v0.30.0
git -C "$O" checkout -q v0.30.0
git -C "$O" checkout -q -- .            # re-runnable: drop an earlier copy of the patch
git -C "$O" apply "$REPO_DIR/dubsvc/omni/vllm-omni-v0.30.0-target_s.patch"
(cd "$O" && VIRTUAL_ENV=$V uv pip install --no-deps -e .)   # the patched checkout replaces the wheel
M=$LIVEDUB_HOME/models/IndexTTS-2.5
log "模型 -> $M"
hf_repo IndexTeam/IndexTTS-2.5 "$M"
indextts_aux "$M"
"$V/bin/python" -c "import vllm, vllm_omni, numpy, soundfile; print('vllm', vllm.__version__, 'ok')"
log "配音环境好了（第一次启动配音服务要 2–3 分钟编译）"

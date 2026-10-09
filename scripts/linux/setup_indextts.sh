#!/usr/bin/env bash
# Alternative dubbing backend "local": IndexTTS-2.5 in the dubbing process itself, from the index-tts repository (its
# own uv project, Python 3.11). Slower than the default vLLM-Omni backend; settings.json then needs
#   "dub": {"backend": "local", "python": "~/live-dub/index-tts/.venv/bin/python"}
# Shares the weights with setup_dub.sh ($LIVEDUB_HOME/models/IndexTTS-2.5). Re-runnable.
source "$(dirname "$0")/common.sh"
need_gpu
command -v git >/dev/null || die "需要 git（sudo apt install git）"
need_uv
R=$LIVEDUB_HOME/index-tts
[ -d "$R/.git" ] || git clone --depth 1 https://github.com/index-tts/index-tts.git "$R"
git -C "$R" log -1 --format='index-tts %h %cs %s'
(cd "$R" && uv sync --python 3.11)
M=$LIVEDUB_HOME/models/IndexTTS-2.5
hf_repo IndexTeam/IndexTTS-2.5 "$M"
indextts_aux "$M"
"$R/.venv/bin/python" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
log "index-tts 好了"

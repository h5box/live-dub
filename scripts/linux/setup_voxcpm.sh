#!/usr/bin/env bash
# Alternative dubbing backend "voxcpm": VoxCPM2 (openbmb, Apache-2.0) in its own venv. Its voices run faster than the
# original and need more time-stretching than IndexTTS; kept as an option. settings.json then needs
#   "dub": {"backend": "voxcpm", "python": "~/live-dub/venv-voxcpm/bin/python"}
source "$(dirname "$0")/common.sh"
need_gpu
need_uv
V=$LIVEDUB_HOME/venv-voxcpm
[ -x "$V/bin/python" ] || uv venv --python 3.11 "$V"
VIRTUAL_ENV=$V uv pip install voxcpm soundfile audiotsm --torch-backend=auto   # audiotsm: WSOLA time-stretch
hf_repo openbmb/VoxCPM2 "$LIVEDUB_HOME/models/VoxCPM2"
"$V/bin/python" -c "import torch, voxcpm; print('torch', torch.__version__, torch.cuda.get_device_name(0))"
log "VoxCPM2 好了"

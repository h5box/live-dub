#!/usr/bin/env bash
# The ASR service's environment (svc/asr_service.py; runs on the CPU): a venv with PyTorch (CPU build) and NeMo 3.0,
# and two models from Hugging Face: Parakeet-TDT-0.6B-v2 (English speech recognition) and TitaNet-L (speaker
# embeddings), both CC-BY-4.0. About 6 GB in $LIVEDUB_HOME (default ~/live-dub). Re-runnable.
#   Linux:   bash scripts/linux/setup_asr.sh
#   Windows: in the project directory, wsl -e bash scripts/linux/setup_asr.sh
source "$(dirname "$0")/common.sh"
need_uv
V=$LIVEDUB_HOME/venv-asr
log "Python 环境 $V"
[ -x "$V/bin/python" ] || uv venv --python 3.12 "$V"
# transformers pinned: left free, the resolver takes the newest huggingface-hub (2.x) and falls back to transformers
# 4.12, whose tokenizers has to be compiled from source (and fails)
VIRTUAL_ENV=$V uv pip install --torch-backend cpu "nemo_toolkit[asr]==3.0.0" "transformers>=5.17,<6" soundfile
log "模型 -> $LIVEDUB_HOME/models"
hf_file nvidia/parakeet-tdt-0.6b-v2 parakeet-tdt-0.6b-v2.nemo "$LIVEDUB_HOME/models/parakeet-tdt-0.6b-v2.nemo"
hf_file nvidia/speakerverification_en_titanet_large speakerverification_en_titanet_large.nemo \
  "$LIVEDUB_HOME/models/titanet-large.nemo"
"$V/bin/python" -c "import torch, nemo.collections.asr; print('torch', torch.__version__, 'NeMo ASR ok')"
log "识别环境好了"

# Sourced by the setup scripts: where things go, logging, uv, Hugging Face downloads.
#   LIVEDUB_HOME  venvs and models of the speech services (default ~/live-dub; settings.json linux.home must match)
#   HF_ENDPOINT   a Hugging Face mirror, e.g. https://hf-mirror.com
set -euo pipefail
LIVEDUB_HOME=${LIVEDUB_HOME:-$HOME/live-dub}
HF_ENDPOINT=${HF_ENDPOINT:-https://huggingface.co}
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
mkdir -p "$LIVEDUB_HOME/models"

log() { echo "[$(date +%T)] $*"; }
die() { echo "[$(date +%T)] 出错：$*" >&2; exit 1; }

need_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    export PATH="$HOME/.local/bin:$PATH"
  fi
  if ! command -v uv >/dev/null 2>&1; then
    log "安装 uv（https://docs.astral.sh/uv/）"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
}

need_gpu() {
  command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1 \
    || die "这里看不到 NVIDIA 显卡（nvidia-smi）。WSL 里要装 Windows 版的 NVIDIA 驱动，不要在 WSL 里另装驱动。"
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
}

# hf_file REPO FILE DEST: one file from a Hugging Face repo (resumes; DEST appears only when complete)
hf_file() {
  local repo=$1 file=$2 dest=$3
  if [ -s "$dest" ]; then echo "  已有 $dest"; return 0; fi
  mkdir -p "$(dirname "$dest")"
  echo "  下载 $repo/$file"
  local progress=-sS
  [ -t 2 ] && progress=--progress-bar          # a bar on a terminal, quiet in a log
  curl -fL $progress --retry 10 --retry-all-errors --retry-delay 5 -C - -o "$dest.part" \
    "$HF_ENDPOINT/$repo/resolve/main/$file"
  mv "$dest.part" "$dest"
}

# hf_repo REPO DEST: every file of a Hugging Face model repo
hf_repo() {
  local repo=$1 dest=$2 f
  python3 - "$HF_ENDPOINT" "$repo" > "$LIVEDUB_HOME/.files" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen(f"{sys.argv[1]}/api/models/{sys.argv[2]}", timeout=60) as r:
    for s in json.load(r)["siblings"]:
        if s["rfilename"] != ".gitattributes":
            print(s["rfilename"])
PY
  while IFS= read -r f; do hf_file "$repo" "$f" "$dest/$f"; done < "$LIVEDUB_HOME/.files"
  rm -f "$LIVEDUB_HOME/.files"
}

# the four helper models IndexTTS-2.5 loads at runtime, put where it looks for them (MODEL_DIR/hf_cache)
indextts_aux() {
  local C=$1/hf_cache f
  for f in config.json preprocessor_config.json model.safetensors; do
    hf_file facebook/w2v-bert-2.0 "$f" "$C/w2v-bert-2.0/$f"
  done
  hf_file amphion/MaskGCT semantic_codec/model.safetensors "$C/semantic_codec_model.safetensors"
  hf_file funasr/campplus campplus_cn_common.bin "$C/campplus_cn_common.bin"
  hf_file nvidia/bigvgan_v2_22khz_80band_256x config.json "$C/bigvgan/config.json"
  hf_file nvidia/bigvgan_v2_22khz_80band_256x bigvgan_generator.pt "$C/bigvgan/bigvgan_generator.pt"
}

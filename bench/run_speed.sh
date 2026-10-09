#!/usr/bin/env bash
# usage (Linux side, from the project directory): bash bench/run_speed.sh ARGS > log 2>&1
# ARGS go to bench/tts_single_pass.py; set VENV=... to use another venv (default: the index-tts repo venv)
cd ~/live-dub/index-tts
export PYTHONUNBUFFERED=1
echo "=== [$(date +%T)] start $*"
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader
"${VENV:-.venv}/bin/python" "$(dirname "$0")/tts_single_pass.py" "$@"
echo "=== [$(date +%T)] exit $?"

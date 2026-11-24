#!/usr/bin/env bash
# LoRA SFT for the defender, reusing the FromLev training environment and base weights.
#   TBT_TRAIN_PY=<python with torch/transformers/peft>  TBT_BASE=<Qwen3-0.6B HF dir>  scripts/train_lora.sh [train_lora.py args]
set -euo pipefail
cd "$(dirname "$0")/.."
FROMLEV="${FROMLEV:-$HOME/WorkSpace/GameDev/FromLev/ai/train}"
PY="${TBT_TRAIN_PY:-$FROMLEV/.venv/bin/python}"
BASE="${TBT_BASE:-$FROMLEV/base}"
exec "$PY" training/train_lora.py --data data/sft/v1/train.jsonl --val-data data/sft/v1/val.jsonl --base "$BASE" "$@"

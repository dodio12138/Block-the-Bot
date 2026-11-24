#!/usr/bin/env bash
# Convert a trained PEFT adapter to a GGUF LoRA for llama-server --lora.
#   scripts/convert_lora.sh [adapter_dir] [out.gguf]
set -euo pipefail
cd "$(dirname "$0")/.."
FROMLEV_ROOT="${FROMLEV_ROOT:-$HOME/WorkSpace/GameDev/FromLev}"
PY="${TBT_TRAIN_PY:-$FROMLEV_ROOT/ai/train/.venv/bin/python}"
BASE="${TBT_BASE:-$FROMLEV_ROOT/ai/train/base}"
CONVERT="${LLAMA_CONVERT_LORA:-$FROMLEV_ROOT/build/gd/_deps/llama-src/convert_lora_to_gguf.py}"
ADAPTER="${1:-training/out/adapter}"
OUT="${2:-game/models/bot-lora.gguf}"
mkdir -p "$(dirname "$OUT")"
"$PY" "$CONVERT" "$ADAPTER" --base "$BASE" --outfile "$OUT" --outtype f16

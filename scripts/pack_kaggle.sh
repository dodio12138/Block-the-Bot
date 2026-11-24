#!/usr/bin/env bash
# Assemble the Kaggle dataset folder (train/val data + trainer).
#   scripts/pack_kaggle.sh [data_dir] [level]     level: L2 (default) | L1 | all
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:-data/sft/v1}"
LEVEL="${2:-L2}"
OUT=training/kaggle/dataset
mkdir -p "$OUT"
for split in train val; do
  if [[ "$LEVEL" == "all" ]]; then
    cp "$SRC/$split.jsonl" "$OUT/$split.jsonl"
  else
    python3 -c "import json,sys
for l in open(sys.argv[1], encoding='utf-8'):
    if json.loads(l)['meta']['level'] == sys.argv[2]: sys.stdout.write(l)" "$SRC/$split.jsonl" "$LEVEL" > "$OUT/$split.jsonl"
  fi
done
cp training/train_lora.py "$OUT/"
wc -l "$OUT"/*.jsonl
ls -la "$OUT"

#!/usr/bin/env bash
# Start llama-server with Qwen3-0.6B for the agent service (OpenAI-compatible, port 8080).
#   LLAMA_SERVER=/path/to/llama-server  TBT_MODEL=/path/to/model.gguf  scripts/start_llm.sh
set -euo pipefail
LLAMA_SERVER="${LLAMA_SERVER:-$(command -v llama-server || echo "$HOME/tools/llama.cpp/b12651/llama-b12651/llama-server")}"
TBT_MODEL="${TBT_MODEL:-$HOME/models/qwen3-0.6b/Qwen3-0.6B-Q8_0.gguf}"
PORT="${TBT_LLM_PORT:-8080}"
[[ -x "$LLAMA_SERVER" ]] || { echo "llama-server not found ($LLAMA_SERVER)"; exit 1; }
[[ -f "$TBT_MODEL" ]] || { echo "model not found ($TBT_MODEL)"; exit 1; }
exec "$LLAMA_SERVER" -m "$TBT_MODEL" --alias qwen3-0.6b --host 127.0.0.1 --port "$PORT" \
  -c 4096 -np 1 --reasoning-budget 0 --chat-template-kwargs '{"enable_thinking":false}' "$@"

#!/usr/bin/env bash
# Build the core if needed, then launch the Godot client (it starts the agent service itself).
#   GODOT=/path/to/Godot  TBT_LLM_URL=http://127.0.0.1:8080/v1  scripts/run_game.sh [-- --dev]
set -euo pipefail
cd "$(dirname "$0")/.."
LIB=core/build/libtbt_core.dylib; [[ "$(uname)" == "Linux" ]] && LIB=core/build/libtbt_core.so
[[ -f "$LIB" ]] || scripts/build_core.sh
GODOT="${GODOT:-$HOME/Library/Application Support/Steam/steamapps/common/Godot Engine/Godot.app/Contents/MacOS/Godot}"
export TBT_PYTHON="${TBT_PYTHON:-$(command -v python3)}"
exec "$GODOT" --path game "$@"

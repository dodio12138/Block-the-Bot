#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/build_core.sh
python3 -W ignore -m unittest discover -s tests/agent

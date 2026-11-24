#!/usr/bin/env bash
# Build the C++ rules core (shared lib for the agent, headless CLI, tests) and run the tests.
set -euo pipefail
cd "$(dirname "$0")/.."
cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build core/build -j
./core/build/tbt_tests

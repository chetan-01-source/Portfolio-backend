#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m app.modules.eval.service "$@"

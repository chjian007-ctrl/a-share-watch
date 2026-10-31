#!/usr/bin/env bash
# Local test without Docker: same layout/env as the container, on port 8790 (does not touch the live 8765 server).
cd "$(dirname "$0")"
PORT=${PORT:-8790}
export DATA_DIR=${DATA_DIR:-$PWD/.localdata} SEED_DIR=$PWD/seed
export ACCESS_TOKEN=${ACCESS_TOKEN:-$(cat /workspace/a-share-watch/data/token.txt 2>/dev/null)}
mkdir -p "$DATA_DIR"
PY=${PY:-/workspace/a-share-watch/venv/bin/python}
cd app && exec "$PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT" --workers 1 --log-level warning

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/dist/skein-p0-$STAMP.tar.gz"
mkdir -p "$ROOT/dist"

tar czf "$OUT" \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='node_modules' \
  --exclude='services/console/node_modules' \
  --exclude='services/control/data' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  -C "$ROOT" \
  services/control \
  services/console/dist \
  services/console/legacy \
  deploy/p0 \
  deploy/scripts \
  docs

echo "$OUT"

#!/usr/bin/env bash
# 以现有 xiaoqiu 镜像为基底，把指标源从 qdockd 换成雁阵控制面。
# 用法：BASE_IMAGE=xiaoqiu:1.0.96-sso OUT_IMAGE=xiaoqiu:1.0.97-skein bash build.sh
set -euo pipefail

BASE_IMAGE=${BASE_IMAGE:-xiaoqiu:1.0.96-sso}
OUT_IMAGE=${OUT_IMAGE:-xiaoqiu:1.0.97-skein}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/app/ops" "$WORK/config"
for f in app/main.py app/config.py app/ops/alerts.py config/settings.yaml; do
  docker run --rm --entrypoint sh "$BASE_IMAGE" -c "cat /srv/sse-agent/$f" > "$WORK/$f"
done
cp "$HERE/skein_metrics.py" "$WORK/app/ops/skein_metrics.py"

python3 "$HERE/patch_image.py" "$WORK"
cp "$HERE/Dockerfile" "$WORK/Dockerfile"

docker build -q --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$OUT_IMAGE" "$WORK"
echo "built $OUT_IMAGE"

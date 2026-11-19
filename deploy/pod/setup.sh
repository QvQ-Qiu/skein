#!/usr/bin/env bash
# 集市云 pod 执行节点：一次性准备（可重复执行）。
# 用法：在 pod 里 bash setup.sh
# 前提：pod 已能出网（GitHub / npmmirror / skein.ssemarket.cn）。
set -euo pipefail

echo "== apt =="
sudo sed -i 's|http://archive.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g; s|http://security.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq bubblewrap openssh-client

echo "== dirs =="
mkdir -p "$HOME/jobs" "$HOME/repos" "$HOME/.local"

echo "== node 22+ =="
if [ ! -x "$HOME/.local/node/bin/node" ]; then
  VER=$(curl -fsSL https://npmmirror.com/mirrors/node/index.json | python3 -c 'import sys,json; d=json.load(sys.stdin); print(next(x["version"] for x in d if x["lts"]))')
  curl -fSL "https://npmmirror.com/mirrors/node/$VER/node-$VER-linux-x64.tar.xz" -o /tmp/node.tar.xz
  tar -xJf /tmp/node.tar.xz -C "$HOME/.local"
  mv "$HOME/.local/node-$VER-linux-x64" "$HOME/.local/node"
fi
"$HOME/.local/node/bin/node" -v
grep -q '.local/node/bin' "$HOME/.bashrc" || echo 'export PATH="$HOME/.local/node/bin:$PATH"' >> "$HOME/.bashrc"

echo "== claude code =="
export PATH="$HOME/.local/node/bin:$PATH"
npm config set registry https://registry.npmmirror.com >/dev/null
command -v claude >/dev/null || npm i -g @anthropic-ai/claude-code
claude --version

echo "== repo =="
if [ ! -d "$HOME/repos/skein/.git" ]; then
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  [ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519" -C skein-worker@pod
  echo "把下面这把公钥加成 QvQ-Qiu/skein 的 deploy key（读写）："
  cat "$HOME/.ssh/id_ed25519.pub"
  ssh-keyscan -t ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true
  git clone git@github.com:QvQ-Qiu/skein.git "$HOME/repos/skein"
fi
git -C "$HOME/repos/skein" pull --ff-only

echo "== claude 网关配置 =="
if [ ! -f "$HOME/.claude/settings.json" ]; then
  echo "缺 $HOME/.claude/settings.json（env: ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL），从控制面节点拷一份再继续"
  exit 1
fi

echo "== 完成；用 start-node-agent.sh 启动 =="

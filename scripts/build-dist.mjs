#!/usr/bin/env node
// 雁阵节点分发包构建：一次构建 → 自包含 tarball → 目标机解压即跑（零在线安装）。
// 产物：
//   dist/yanzhen-router-<ver>.tar.gz   头雁节点（采集+分类+controller+面板+账本）
//   dist/yanzhen-worker-<ver>.tar.gz   工雁监督进程（轻量，不含内核）
//   dist/yanzhen-kernel-cc-<ver>.tar.gz Claude Code 内核包（大，单独分发/复用）
// 策略：整目录复制（含 node 运行时与 node_modules），不做 esbuild 单文件——
// mysql2 有动态 require、better-sqlite3 是原生模块，整包复制最稳，性质与 fat-jar 等价。
import { cpSync, mkdirSync, rmSync, writeFileSync, readFileSync, chmodSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const ver = pkg.version;
const dist = join(root, 'dist');
const nodeBin = process.execPath;                       // 构建机 node 二进制随包
const ccDir = dirname(dirname(nodeBin)) + '/lib/node_modules/@anthropic-ai/claude-code';

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });

// 前端：Vue3+Vite 构建 → 静态产物进 public/（包内即构建产物，解压即跑不变）
execFileSync('npm', ['--prefix', join(root, 'web'), 'run', 'build'], { stdio: 'inherit' });
rmSync(join(root, 'public'), { recursive: true, force: true });
cpSync(join(root, 'web', 'dist'), join(root, 'public'), { recursive: true });

function sh(cmd, args, cwd) { execFileSync(cmd, args, { cwd, stdio: 'inherit' }); }
const manifest = { version: ver, updated_at: new Date().toISOString(), files: [] };
function tarball(name, dir) {
  const out = join(dist, `${name}-${ver}.tar.gz`);
  sh('tar', ['czf', out, '-C', dist, name]);
  const bytes = readFileSync(out).length;
  const sha = execFileSync('sha256sum', [out], { encoding: 'utf8' }).split(' ')[0];
  // latest 别名：固定下载地址，版本升级不变
  cpSync(out, join(dist, `${name}-latest.tar.gz`));
  manifest.files.push({
    name: `${name}-latest.tar.gz`, versioned: `${name}-${ver}.tar.gz`,
    url: `https://yanzhen.ssemarket.cn/dl/${name}-latest.tar.gz`,
    sha256: sha, bytes,
  });
  console.log(`✓ ${name}-${ver}.tar.gz  ${(bytes / 1048576).toFixed(1)}MB  sha256=${sha.slice(0, 16)}…`);
}
function copyNode(dest) {
  mkdirSync(join(dest, 'node', 'bin'), { recursive: true });
  cpSync(nodeBin, join(dest, 'node', 'bin', 'node'));
  chmodSync(join(dest, 'node', 'bin', 'node'), 0o755);
}
function envExample(src, dest, keepKeys = []) {
  const lines = readFileSync(src, 'utf8').split('\n').map(l => {
    if (!l.includes('=') || l.startsWith('#')) return l;
    const k = l.split('=')[0];
    return keepKeys.includes(k) ? l : `${k}=<填写>`;
  });
  writeFileSync(dest, lines.join('\n'));
}

/* ---------- 头雁 router 节点 ---------- */
{
  const d = join(dist, 'yanzhen-router');
  mkdirSync(d, { recursive: true });
  // 不带 docs/：/dl 公开下载，内部文档只留 iWiki 与本机面板
  for (const p of ['server', 'public', 'package.json', 'package-lock.json', 'node_modules']) {
    cpSync(join(root, p), join(d, p), { recursive: true });
  }
  mkdirSync(join(d, 'data'), { recursive: true });
  copyNode(d);
  envExample(join(root, '.env'), join(d, '.env.example'), ['PORT', 'LLM_BASE_URL', 'LLM_MODEL', 'LLM_FALLBACK_MODEL', 'ZHIPU_BASE_URL', 'ZHIPU_MODEL', 'FORUM_DB_HOST', 'FORUM_DB_PORT', 'FORUM_DB_NAME', 'INGEST_INTERVAL_MS']);
  writeFileSync(join(d, 'start.sh'), `#!/bin/sh
# 雁阵头雁节点 · 解压即跑（内置 node，无需任何安装）
cd "$(dirname "$0")"
[ -f .env ] || { echo "首次运行：cp .env.example .env 并填写密钥/口令"; exit 1; }
exec ./node/bin/node server/index.js
`);
  chmodSync(join(d, 'start.sh'), 0o755);
  writeFileSync(join(d, 'README.txt'), `雁阵头雁节点 v${ver}
运行：cp .env.example .env && 编辑 && ./start.sh
面板/agent通道：http://0.0.0.0:3030（PORT 可改）
账本：data/yanzhen.db（持久化请挂卷到 data/）
systemd 参考：scripts/yanzhen.service\n`);
  cpSync(join(root, 'scripts', 'yanzhen.service'), join(d, 'yanzhen.service'));
  tarball('yanzhen-router', d);
}

/* ---------- 工雁 worker 节点（轻包） ---------- */
{
  const d = join(dist, 'yanzhen-worker');
  mkdirSync(join(d, 'app'), { recursive: true });
  cpSync(join(root, 'worker', 'node-agent.mjs'), join(d, 'app', 'node-agent.mjs'));
  // worker 运行时只依赖 ws
  mkdirSync(join(d, 'app', 'node_modules'), { recursive: true });
  cpSync(join(root, 'node_modules', 'ws'), join(d, 'app', 'node_modules', 'ws'), { recursive: true });
  copyNode(d);
  writeFileSync(join(d, 'worker.env.example'), `# 雁阵 node agent 配置（机器/pod 级节点；worker=其上的 cc 会话，由面板/router 创建）
CONTROLLER_URL=wss://yanzhen.ssemarket.cn/api/agent/channel
AGENT_TOKEN=<填写>
NODE_ID=n-iwiki-host
NODE_NAME=iwiki 宿主机
PLATFORMS=iwiki
REPO_MAP={"iwiki":"/path/to/iwiki/repo"}
MAX_WORKERS=3
# Claude 内核认证（与本机 cc 同源网关）
ANTHROPIC_BASE_URL=<填写>
ANTHROPIC_AUTH_TOKEN=<填写>
# 内核包位置（yanzhen-kernel-cc 解压目录）
KERNEL_DIR=../yanzhen-kernel-cc
# 容器/pod 内自动声明沙箱；裸机 root 需显式 IS_SANDBOX=1 且只挂隔离目录
# IS_SANDBOX=1
# PUSH_BRANCH=1
`);
  writeFileSync(join(d, 'start.sh'), `#!/bin/sh
# 雁阵工雁节点 · 解压即跑（内置 node；Claude 内核包另解压到 KERNEL_DIR）
cd "$(dirname "$0")"
[ -f worker.env ] || { echo "首次运行：cp worker.env.example worker.env 并填写"; exit 1; }
set -a; . ./worker.env; set +a
export PATH="$PWD/node/bin:$PATH"
export CLAUDE_BIN="\${CLAUDE_BIN:-$KERNEL_DIR/cli-wrapper.cjs}"
exec ./node/bin/node app/node-agent.mjs
`);
  chmodSync(join(d, 'start.sh'), 0o755);
  tarball('yanzhen-worker', d);
}

/* ---------- Claude Code 内核包 ---------- */
if (existsSync(ccDir)) {
  const d = join(dist, 'yanzhen-kernel-cc');
  cpSync(ccDir, d, { recursive: true });
  tarball('yanzhen-kernel-cc', d);
} else {
  console.log('! 未找到 cc 包，跳过内核包：', ccDir);
}
writeFileSync(join(dist, 'manifest.json'), JSON.stringify(manifest, null, 2));
console.log('构建完成 →', dist);

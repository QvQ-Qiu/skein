// 账本（单一事实源）：事件 / 需求单 / worker / 派发记录 / 雁鸣 room / 日志。
// 一期 SQLite 落本机；多 router / 集群阶段可平移到共享 PG（表结构不变，
// 认领语义靠 UPDATE ... WHERE status=... 的原子性保证，与存储引擎无关）。
import Database from 'better-sqlite3';
import { join } from 'node:path';
import { config } from './config.js';

export const db = new Database(join(config.root, 'data', 'yanzhen.db'));
db.pragma('journal_mode = WAL');
db.pragma('busy_timeout = 5000');

db.exec(`
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT NOT NULL,              -- market-post | market-comment
  source_id TEXT NOT NULL,            -- 帖子 postID / 评论 pcommentID
  parent_id TEXT,                     -- 评论所属帖子
  author_alias TEXT,
  partition_name TEXT,
  title TEXT,
  content TEXT,
  source_time TEXT,
  url TEXT,
  fetched_at TEXT NOT NULL,
  verdict TEXT,                       -- router 判定 JSON
  verdict_at TEXT,
  ticket_id INTEGER,
  UNIQUE(channel, source_id)          -- 多 router 重复采集的幂等闸
);
CREATE INDEX IF NOT EXISTS idx_events_verdict ON events(verdict_at);

CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE,                   -- YZ-0001
  title TEXT NOT NULL,
  kind TEXT,                          -- bug | feature
  platform TEXT,                      -- iwiki | market | sso | applet | xiaoqiu | join | cloud | other
  severity TEXT,                      -- P0..P3
  summary TEXT,
  status TEXT NOT NULL DEFAULT 'staged',  -- staged|assigned|running|review|done|rejected
  source_event_id INTEGER,
  source_url TEXT,
  reason TEXT,
  confidence REAL,
  worker_id TEXT,
  lease_until TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);

CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,                -- 机器/pod 级节点身份，如 n-iwiki-host
  name TEXT, host TEXT, pod TEXT,
  platforms TEXT,                     -- JSON 数组：该节点可服务的平台
  repos TEXT,                         -- JSON map：platform → 仓库路径
  status TEXT NOT NULL DEFAULT 'offline',
  last_seen TEXT, registered_at TEXT,
  max_workers INTEGER NOT NULL DEFAULT 3,
  meta TEXT
);

CREATE TABLE IF NOT EXISTS workers (
  id TEXT PRIMARY KEY,                -- 稳定身份，如 w-iwiki-01
  node_id TEXT,                       -- 所在节点
  name TEXT,
  harness TEXT DEFAULT 'cc',          -- 内核：Claude Code
  session_id TEXT,                    -- ★ 一个 worker = 一个 cc 会话（--resume 续上下文）
  platform TEXT,
  repo TEXT,
  status TEXT NOT NULL DEFAULT 'creating',  -- creating | idle | busy | offline
  pending_ticket_id INTEGER,          -- 自动建 worker 时等待接的单
  last_seen TEXT,
  registered_at TEXT,
  meta TEXT
);

CREATE TABLE IF NOT EXISTS dispatches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER,
  worker_id TEXT,
  action TEXT,                        -- dispatch|ack|progress|result|cancel|lease_expired|reject
  payload TEXT,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dispatches_ticket ON dispatches(ticket_id);

CREATE TABLE IF NOT EXISTS rooms (
  id TEXT PRIMARY KEY,
  name TEXT,
  ticket_id INTEGER,
  status TEXT DEFAULT 'open',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS room_members (
  room_id TEXT, worker_id TEXT, PRIMARY KEY(room_id, worker_id)
);
CREATE TABLE IF NOT EXISTS room_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id TEXT, worker_id TEXT, kind TEXT, body TEXT, at TEXT
);

CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, level TEXT, component TEXT, message TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_at ON logs(at);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
`);

// 旧库迁移：workers 从「机器级监督进程」升级为「节点上的 cc 会话」
{
  const cols = new Set(db.prepare('PRAGMA table_info(workers)').all().map(c => c.name));
  const add = (name, ddl) => { if (!cols.has(name)) db.exec(`ALTER TABLE workers ADD COLUMN ${name} ${ddl}`); };
  add('node_id', 'TEXT');
  add('session_id', 'TEXT');
  add('platform', 'TEXT');
  add('pending_ticket_id', 'INTEGER');
  add('purpose', 'TEXT');          // 这个 worker 是干啥的（建它时的使命）
  add('created_by', 'TEXT');       // 谁建的：router id / human:sub
  if (!cols.has('platform')) db.exec("UPDATE workers SET platform = json_extract(platforms, '$[0]') WHERE platform IS NULL");
  const tcols = new Set(db.prepare('PRAGMA table_info(tickets)').all().map(c => c.name));
  if (!tcols.has('dedup_key')) {
    db.exec('ALTER TABLE tickets ADD COLUMN dedup_key TEXT');
    db.exec('CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_dedup ON tickets(dedup_key) WHERE dedup_key IS NOT NULL');
  }
  const ncols = new Set(db.prepare('PRAGMA table_info(nodes)').all().map(c => c.name));
  if (!ncols.has('peer_url')) db.exec('ALTER TABLE nodes ADD COLUMN peer_url TEXT');
}
// 建单锁：同一平台同一时刻只允许一个 router 建单（锁也在账本里，router 之间仍不通信）
db.exec(`CREATE TABLE IF NOT EXISTS create_locks (
  platform TEXT PRIMARY KEY, holder TEXT, until TEXT
)`);
// 单↔worker 多对多：一张单可挂多个 worker 协作（各带角色/使命/租约/交付）。
// 主键防的是「同一 worker 被重复塞同一张单」，不是「一单只能一人干」。
db.exec(`CREATE TABLE IF NOT EXISTS ticket_workers (
  ticket_id INTEGER NOT NULL,
  worker_id TEXT NOT NULL,
  role TEXT,                  -- 这张单里他负责啥（如：iwiki 侧修复 / sso 侧排查）
  state TEXT NOT NULL DEFAULT 'assigned',   -- assigned | running | delivered | cancelled | expired
  lease_until TEXT,
  result TEXT,
  assigned_at TEXT NOT NULL,
  PRIMARY KEY (ticket_id, worker_id)
)`);
{
  // 旧单 worker_id 迁移进关联表
  const old = db.prepare("SELECT id, worker_id, lease_until, status FROM tickets WHERE worker_id IS NOT NULL AND status IN ('assigned','running')").all();
  const ins = db.prepare('INSERT OR IGNORE INTO ticket_workers(ticket_id,worker_id,state,lease_until,assigned_at) VALUES(?,?,?,?,?)');
  for (const t of old) ins.run(t.id, t.worker_id, t.status === 'running' ? 'running' : 'assigned', t.lease_until, new Date().toISOString());
}
// router 名册：一个 router = 一个独立可观测域（渠道/部门），数量少；跨部门协作靠协作板交流
db.exec(`CREATE TABLE IF NOT EXISTS routers (
  id TEXT PRIMARY KEY,
  name TEXT,
  observes TEXT,                -- JSON 数组：观测的平台/渠道
  peer_url TEXT,
  status TEXT NOT NULL DEFAULT 'offline',
  last_seen TEXT, registered_at TEXT
)`);
db.exec(`CREATE TABLE IF NOT EXISTS router_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  router_id TEXT NOT NULL,
  ticket_id INTEGER,            -- 挂单：跨部门协作线程跟着需求单走
  body TEXT NOT NULL,
  at TEXT NOT NULL
)`);
db.exec('CREATE INDEX IF NOT EXISTS idx_rmsg_ticket ON router_messages(ticket_id)');

export const now = () => new Date().toISOString();

export function kvGet(k, dflt = null) {
  const r = db.prepare('SELECT v FROM kv WHERE k = ?').get(k);
  return r ? r.v : dflt;
}
export function kvSet(k, v) {
  db.prepare('INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v').run(k, String(v));
}

export function log(level, component, message, data = null) {
  const at = now();
  db.prepare('INSERT INTO logs(at,level,component,message,data) VALUES(?,?,?,?,?)')
    .run(at, level, component, message, data ? JSON.stringify(data) : null);
  // 日志保留 7 天，防无限增长
  db.prepare("DELETE FROM logs WHERE at < datetime('now','-7 days')").run();
  return { at, level, component, message, data };
}

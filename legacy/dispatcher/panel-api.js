// 面板 API v3：SSO 会话鉴权；读=登录用户，写/建=管理员；拓扑网/名册/协作板/多worker挂单。
import express from 'express';
import { db, now, log, kvGet, kvSet } from './db.js';
import { bus } from './bus.js';
import { config } from './config.js';
import { pollOnce } from './ingest.js';
import { pumpRouter } from './router.js';
import { dispatchTicket, cancelTicket, reviewTicket, createRoom, createWorker, destroyWorker, onlineNodeIds, onlineRouterIds } from './controller.js';
import { requireUser, requireAdmin } from './auth.js';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

export function mountPanel(app) {
  app.get('/health', (_req, res) => res.json({ ok: true, service: 'yanzhen', nodes_online: onlineNodeIds().length, routers_online: onlineRouterIds().length }));

  // 节点包固定下载站（公开只读；包内无密钥/内部文档）
  const DL_RE = /^(manifest\.json|yanzhen-(router|worker|kernel-cc)-(latest|\d+\.\d+\.\d+)\.tar\.gz)$/;
  app.get('/dl/:file', (req, res) => {
    const name = req.params.file;
    if (!DL_RE.test(name)) return res.status(404).json({ error: 'not found' });
    const path = join(config.root, 'dist', name);
    if (!existsSync(path)) return res.status(404).json({ error: '包未构建' });
    res.setHeader('Cache-Control', name === 'manifest.json' ? 'no-cache' : 'public, max-age=300');
    res.sendFile(path);
  });

  // 以下全部要 SSO 登录
  app.use('/api', requireUser);

  app.get('/api/overview', (_req, res) => {
    const c = (sql, ...p) => db.prepare(sql).get(...p).c;
    const today = new Date().toISOString().slice(0, 10);
    res.json({
      events: {
        total: c('SELECT COUNT(*) c FROM events'),
        today: c("SELECT COUNT(*) c FROM events WHERE fetched_at >= ?", today),
        unclassified: c('SELECT COUNT(*) c FROM events WHERE verdict_at IS NULL'),
        requirements: c("SELECT COUNT(*) c FROM events WHERE ticket_id IS NOT NULL"),
      },
      tickets: {
        staged: c("SELECT COUNT(*) c FROM tickets WHERE status='staged'"),
        active: c("SELECT COUNT(*) c FROM tickets WHERE status IN ('assigned','running')"),
        review: c("SELECT COUNT(*) c FROM tickets WHERE status='review'"),
        done: c("SELECT COUNT(*) c FROM tickets WHERE status='done'"),
        rejected: c("SELECT COUNT(*) c FROM tickets WHERE status='rejected'"),
      },
      nodes: { online: onlineNodeIds().length, total: c('SELECT COUNT(*) c FROM nodes') },
      routers: { online: onlineRouterIds().length, total: c('SELECT COUNT(*) c FROM routers') },
      workers: {
        idle: c("SELECT COUNT(*) c FROM workers WHERE status='idle'"),
        busy: c("SELECT COUNT(*) c FROM workers WHERE status='busy'"),
        creating: c("SELECT COUNT(*) c FROM workers WHERE status='creating'"),
        total: c('SELECT COUNT(*) c FROM workers'),
      },
      auto_dispatch: kvGet('auto_dispatch', '1') !== '0',
      watermarks: { post: kvGet('wm_market_post'), comment: kvGet('wm_market_comment') },
      llm: { model: config.llm.model, fallback: config.llm.fallbackModel },
      ingest_interval_s: config.ingestIntervalMs / 1000,
      server_time: now(),
    });
  });

  app.get('/api/events', (req, res) => {
    const limit = Math.min(Number(req.query.limit) || 100, 500);
    const filter = req.query.filter;
    let sql = 'SELECT * FROM events';
    const where = [];
    if (filter === 'req') where.push('ticket_id IS NOT NULL');
    if (filter === 'noise') where.push("verdict IS NOT NULL AND ticket_id IS NULL AND json_extract(verdict,'$.kind')='noise'");
    if (filter === 'pending') where.push('verdict_at IS NULL');
    if (where.length) sql += ' WHERE ' + where.join(' AND ');
    sql += ' ORDER BY id DESC LIMIT ?';
    res.json(db.prepare(sql).all(limit).map(r => ({ ...r, verdict: r.verdict ? JSON.parse(r.verdict) : null })));
  });

  app.get('/api/tickets', (req, res) => {
    const status = req.query.status;
    let sql = 'SELECT * FROM tickets';
    const params = [];
    if (status && status !== 'all') { sql += ' WHERE status=?'; params.push(status); }
    sql += ' ORDER BY id DESC LIMIT 300';
    res.json(db.prepare(sql).all(...params));
  });

  app.get('/api/tickets/:id', (req, res) => {
    const t = db.prepare('SELECT * FROM tickets WHERE id=?').get(req.params.id);
    if (!t) return res.status(404).json({ error: '不存在' });
    const event = t.source_event_id ? db.prepare('SELECT * FROM events WHERE id=?').get(t.source_event_id) : null;
    const dispatches = db.prepare('SELECT * FROM dispatches WHERE ticket_id=? ORDER BY id').all(t.id);
    const workers = db.prepare(`SELECT tw.*, w.name AS wname, w.node_id, w.session_id, w.purpose
      FROM ticket_workers tw LEFT JOIN workers w ON w.id=tw.worker_id WHERE tw.ticket_id=? ORDER BY tw.assigned_at`).all(t.id);
    const board = db.prepare('SELECT * FROM router_messages WHERE ticket_id=? ORDER BY id').all(t.id);
    const rooms = db.prepare('SELECT * FROM rooms WHERE ticket_id=?').all(t.id);
    res.json({ ...t, result: t.result ? JSON.parse(t.result) : null, event, dispatches, workers, board, rooms });
  });

  // 派单：可一次挂多个 worker（协作）；仅管理员
  app.post('/api/tickets/:id/dispatch', requireAdmin, express.json(), (req, res) => {
    const ids = req.body?.worker_ids || (req.body?.worker_id ? [req.body.worker_id] : []);
    if (!ids.length) return res.status(400).json({ error: '未选 worker' });
    const ok = [], fail = [];
    for (const wid of ids) {
      try { dispatchTicket(Number(req.params.id), wid, { role: req.body?.role || '', by: 'human:' + req.yzUser.sub }); ok.push(wid); }
      catch (e) { fail.push({ worker_id: wid, error: e.message }); }
    }
    res.json({ ok, fail });
  });
  app.post('/api/tickets/:id/cancel', requireAdmin, express.json(), (req, res) => {
    try { cancelTicket(Number(req.params.id), req.body?.worker_id || null, req.body?.reason); res.json({ ok: true }); }
    catch (e) { res.status(400).json({ error: e.message }); }
  });
  app.post('/api/tickets/:id/review', express.json(), (req, res) => {
    try { reviewTicket(Number(req.params.id), !!req.body?.accept, req.body?.note || ''); res.json({ ok: true }); }
    catch (e) { res.status(400).json({ error: e.message }); }
  });
  app.post('/api/tickets/:id/reject', express.json(), (req, res) => {
    try { reviewTicket(Number(req.params.id), false, req.body?.note || '人工判定非需求'); res.json({ ok: true }); }
    catch (e) { res.status(400).json({ error: e.message }); }
  });

  app.get('/api/workers', (_req, res) => {
    res.json(db.prepare('SELECT * FROM workers ORDER BY registered_at DESC').all().map(r => ({
      ...r,
      current_ticket: db.prepare("SELECT ticket_id FROM ticket_workers WHERE worker_id=? AND state IN ('assigned','running') LIMIT 1").get(r.id)?.ticket_id || null,
    })));
  });
  app.delete('/api/workers/:id', requireAdmin, (req, res) => {
    try { destroyWorker(req.params.id); res.json({ ok: true }); }
    catch (e) { res.status(400).json({ error: e.message }); }
  });

  app.get('/api/nodes', (_req, res) => {
    res.json(db.prepare('SELECT * FROM nodes ORDER BY registered_at DESC').all().map(n => ({
      ...n,
      platforms: JSON.parse(n.platforms || '[]'),
      repos: JSON.parse(n.repos || '{}'),
      online: onlineNodeIds().includes(n.id),
      workers: db.prepare('SELECT * FROM workers WHERE node_id=? ORDER BY registered_at').all(n.id),
    })));
  });
  app.post('/api/nodes/:id/workers', requireAdmin, express.json(), (req, res) => {
    try {
      const id = createWorker(req.params.id, { name: req.body?.name, platform: req.body?.platform, repo: req.body?.repo, purpose: req.body?.purpose || '', createdBy: 'human:' + req.yzUser.sub });
      res.json({ ok: true, id });
    } catch (e) { res.status(400).json({ error: e.message }); }
  });

  // router 名册 + 管理员预注册 router 身份
  app.get('/api/routers', (_req, res) => {
    res.json(db.prepare('SELECT * FROM routers ORDER BY registered_at DESC').all().map(r => ({ ...r, observes: JSON.parse(r.observes || '[]'), online: onlineRouterIds().includes(r.id) })));
  });
  app.post('/api/routers', requireAdmin, express.json(), (req, res) => {
    const id = String(req.body?.id || '').trim();
    if (!id) return res.status(400).json({ error: 'router id 必填' });
    const ts = now();
    db.prepare(`INSERT INTO routers(id,name,observes,status,registered_at) VALUES(?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET name=excluded.name, observes=excluded.observes`)
      .run(id, req.body?.name || id, JSON.stringify(req.body?.observes || []), 'offline', ts);
    log('info', 'controller', `管理员预注册 router ${id}（观测 ${(req.body?.observes || []).join(',')}）`);
    emit('router', { id, status: 'offline' });
    res.json({ ok: true, id, launch: `ROUTER_ID=${id} OBSERVES=${(req.body?.observes || []).join(',')} AGENT_TOKEN=<.env 同值> ./start.sh` });
  });
  app.get('/api/router-messages', (req, res) => {
    const tid = req.query.ticket_id;
    const sql = tid ? 'SELECT * FROM router_messages WHERE ticket_id=? ORDER BY id DESC LIMIT 200' : 'SELECT * FROM router_messages ORDER BY id DESC LIMIT 200';
    res.json((tid ? db.prepare(sql).all(tid) : db.prepare(sql).all()).reverse());
  });

  app.post('/api/auto-dispatch', requireAdmin, express.json(), (req, res) => {
    kvSet('auto_dispatch', req.body?.on ? '1' : '0');
    log('info', 'controller', `router 自动派单${req.body?.on ? '开启' : '关闭'}`);
    res.json({ ok: true, on: !!req.body?.on });
  });

  app.get('/api/rooms', (_req, res) => {
    res.json(db.prepare('SELECT * FROM rooms ORDER BY created_at DESC').all().map(r => ({
      ...r, members: db.prepare('SELECT worker_id FROM room_members WHERE room_id=?').all(r.id).map(x => x.worker_id),
    })));
  });
  app.get('/api/rooms/:id/messages', (req, res) => {
    res.json(db.prepare('SELECT * FROM room_messages WHERE room_id=? ORDER BY id LIMIT 500').all(req.params.id));
  });
  app.post('/api/rooms', requireAdmin, express.json(), (req, res) => {
    try { res.json({ ok: true, id: createRoom(req.body?.name || '联动', req.body?.ticket_id || null, req.body?.worker_ids || []) }); }
    catch (e) { res.status(400).json({ error: e.message }); }
  });

  app.get('/api/logs', (req, res) => {
    const limit = Math.min(Number(req.query.limit) || 200, 1000);
    const comp = req.query.component;
    let sql = 'SELECT * FROM logs';
    const params = [];
    if (comp) { sql += ' WHERE component=?'; params.push(comp); }
    sql += ' ORDER BY id DESC LIMIT ?';
    res.json(db.prepare(sql).all(...params, limit).reverse());
  });

  app.post('/api/ingest/run', requireAdmin, async (_req, res) => {
    const r = await pollOnce();
    void pumpRouter();
    res.json(r);
  });

  app.get('/api/design', (_req, res) => {
    try { res.type('text/markdown').send(readFileSync(join(config.root, 'docs', 'design.md'), 'utf8')); }
    catch { res.type('text/markdown').send('# 设计文档\n\n完整设计（图说版）见 iWiki：[雁阵 · Super Agent 设计](https://iwiki.ssemarket.cn/space/space-J0Ah5_NK/page/page-Dcj8VjMr)\n'); }
  });

  // 实体详情聚合：运行状态 + 下发过的命令 + 关联（拓扑图点击落地）
  app.get('/api/entity/:kind/:id', (req, res) => {
    const { kind, id } = req.params;
    const progressOf = (tid, wid) => db.prepare("SELECT * FROM dispatches WHERE ticket_id=? AND worker_id=? AND action='progress' ORDER BY id DESC LIMIT 6").all(tid, wid);
    if (kind === 'worker') {
      const w = db.prepare('SELECT * FROM workers WHERE id=?').get(id);
      if (!w) return res.status(404).json({ error: '不存在' });
      const tws = db.prepare('SELECT tw.*, t.code, t.title, t.status AS tstatus FROM ticket_workers tw JOIN tickets t ON t.id=tw.ticket_id WHERE tw.worker_id=? ORDER BY tw.assigned_at DESC').all(id);
      const active = tws.filter(x => ['assigned', 'running'].includes(x.state));
      res.json({
        kind, base: w,
        node: db.prepare('SELECT * FROM nodes WHERE id=?').get(w.node_id) || null,
        commands: db.prepare('SELECT * FROM dispatches WHERE worker_id=? ORDER BY id DESC LIMIT 60').all(id),
        logs: db.prepare("SELECT * FROM logs WHERE component='controller' AND message LIKE ? ORDER BY id DESC LIMIT 15").all('%' + id + '%'),
        rooms: db.prepare('SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.worker_id=?').all(id),
        coWorkers: db.prepare('SELECT DISTINCT worker_id FROM ticket_workers WHERE ticket_id IN (SELECT ticket_id FROM ticket_workers WHERE worker_id=?) AND worker_id != ?').all(id, id).map(r => r.worker_id),
        tws,
        live: active.map(a => ({ ...a, progress: progressOf(a.ticket_id, id) })),
      });
    } else if (kind === 'node') {
      const n = db.prepare('SELECT * FROM nodes WHERE id=?').get(id);
      if (!n) return res.status(404).json({ error: '不存在' });
      const workers = db.prepare('SELECT * FROM workers WHERE node_id=?').all(id);
      const wids = workers.map(w => w.id);
      const ph = wids.map(() => '?').join(',') || "''";
      res.json({
        kind, base: { ...n, platforms: JSON.parse(n.platforms || '[]'), repos: JSON.parse(n.repos || '{}') },
        workers,
        commands: db.prepare(`SELECT * FROM dispatches WHERE worker_id IN (${ph}) ORDER BY id DESC LIMIT 60`).all(...wids),
        logs: db.prepare("SELECT * FROM logs WHERE component='controller' AND message LIKE ? ORDER BY id DESC LIMIT 15").all('%' + id + '%'),
        rooms: db.prepare(`SELECT DISTINCT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id WHERE m.worker_id IN (${ph})`).all(...wids),
        routers: [...new Set(workers.map(w => w.created_by).filter(c => c && !c.startsWith('human')))],
        live: workers.map(w => ({ ...w, active: db.prepare("SELECT tw.ticket_id, t.code FROM ticket_workers tw JOIN tickets t ON t.id=tw.ticket_id WHERE tw.worker_id=? AND tw.state IN ('assigned','running')").all(w.id) })),
      });
    } else if (kind === 'router') {
      const r = db.prepare('SELECT * FROM routers WHERE id=?').get(id);
      if (!r) return res.status(404).json({ error: '不存在' });
      const msgs = db.prepare('SELECT * FROM router_messages WHERE router_id=? ORDER BY id DESC LIMIT 40').all(id);
      const dispatched = db.prepare("SELECT * FROM dispatches WHERE action='dispatch' AND payload LIKE ? ORDER BY id DESC LIMIT 40").all('%"by":"' + id + '"%');
      const co = db.prepare('SELECT DISTINCT router_id FROM router_messages WHERE ticket_id IN (SELECT ticket_id FROM router_messages WHERE router_id=? AND ticket_id IS NOT NULL) AND router_id != ?').all(id, id).map(x => x.router_id);
      res.json({
        kind, base: { ...r, observes: JSON.parse(r.observes || '[]'), online: onlineRouterIds().includes(id) },
        commands: dispatched,
        messages: msgs,
        createdWorkers: db.prepare('SELECT * FROM workers WHERE created_by=?').all(id),
        coRouters: co,
        logs: db.prepare("SELECT * FROM logs WHERE (component='controller' OR component='router-peer' OR component='router-board') AND message LIKE ? ORDER BY id DESC LIMIT 15").all('%' + id + '%'),
        live: { online: onlineRouterIds().includes(id), last_seen: r.last_seen },
      });
    } else res.status(400).json({ error: 'kind 须为 worker/node/router' });
  });

  // 拓扑网：router/node/worker 三类点 + 关系边（SSE 驱动前端 live）
  app.get('/api/topology', (_req, res) => {
    const routers = db.prepare('SELECT * FROM routers').all().map(r => ({ ...r, observes: JSON.parse(r.observes || '[]'), online: onlineRouterIds().includes(r.id) }));
    const nodes = db.prepare('SELECT * FROM nodes').all().map(n => ({ ...n, online: onlineNodeIds().includes(n.id) }));
    const workers = db.prepare('SELECT * FROM workers').all();
    const edges = [];
    for (const w of workers) {
      if (w.node_id) edges.push({ kind: 'host', a: w.node_id, b: w.id });
      if (w.created_by && w.created_by !== 'human' && !w.created_by.startsWith('human:')) edges.push({ kind: 'lineage', a: w.created_by, b: w.id });
    }
    const active = db.prepare("SELECT tw.ticket_id, tw.worker_id, t.code FROM ticket_workers tw JOIN tickets t ON t.id=tw.ticket_id WHERE tw.state IN ('assigned','running')").all();
    for (const a of active) {
      const d = db.prepare("SELECT payload FROM dispatches WHERE ticket_id=? AND worker_id=? AND action='dispatch' ORDER BY id DESC LIMIT 1").get(a.ticket_id, a.worker_id);
      let by = 'human';
      try { by = JSON.parse(d?.payload || '{}').by || 'human'; } catch {}
      edges.push({ kind: 'dispatch', a: by.startsWith('human:') ? 'human' : by, b: a.worker_id, label: a.code });
    }
    const rooms = db.prepare('SELECT * FROM rooms').all();
    for (const r of rooms) {
      const ms = db.prepare('SELECT worker_id FROM room_members WHERE room_id=?').all(r.id).map(x => x.worker_id);
      for (let i = 0; i < ms.length; i++) for (let j = i + 1; j < ms.length; j++) edges.push({ kind: 'room', a: ms[i], b: ms[j], label: r.name });
    }
    // router↔router：同一单协作板上共同发言
    const co = db.prepare('SELECT ticket_id, router_id FROM router_messages WHERE ticket_id IS NOT NULL').all();
    const byTicket = {};
    for (const m of co) (byTicket[m.ticket_id] ||= new Set()).add(m.router_id);
    for (const set of Object.values(byTicket)) {
      const arr = [...set];
      for (let i = 0; i < arr.length; i++) for (let j = i + 1; j < arr.length; j++) edges.push({ kind: 'board', a: arr[i], b: arr[j] });
    }
    if (edges.some(e => e.a === 'human')) routers.push({ id: 'human', name: '人工派发', kind: 'human', online: true, observes: [] });
    res.json({ routers, nodes, workers, edges });
  });

  // SSE 实时流
  app.get('/api/stream', (req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' });
    res.write('retry: 3000\n\n');
    const onChange = (evt) => res.write(`data: ${JSON.stringify(evt)}\n\n`);
    bus.on('change', onChange);
    const hb = setInterval(() => res.write(': hb\n\n'), 15_000);
    req.on('close', () => { bus.off('change', onChange); clearInterval(hb); });
  });
}

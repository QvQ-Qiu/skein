// Controller（对讲机总台）v3
//   node   = 机器/pod 常驻 agent（反向拨入+心跳=在线自动检测；带 peer_url 供 worker 直连）
//   worker = node 上的一个 Claude Code 会话（purpose=建它时的使命；--resume 续上下文）
//   router = 独立可观测域的常驻 peer（数量少；跨部门协作走协作板 router_messages）
// 单↔worker 多对多：一张单可挂多个 worker 协作；主键只防「同一 worker 重复塞同一单」。
// 建单防重在 router 侧（账本唯一键+建单锁），与派单无关。
//
// 协议 v3（JSON over WS /api/agent/channel）：
//  N→C: node.register/heartbeat, worker.created/create_failed/destroyed, ack/nack/progress/result, room.post
//  R→C: router.register/heartbeat, router.post
//  C→N: node.registered, worker.create/destroy, dispatch, cancel, room.roster, room.notify, pong
//  C→R: router.registered, board.notify, pong
import { WebSocketServer, WebSocket } from 'ws';
import { db, now, log, kvGet } from './db.js';
import { emit } from './bus.js';
import { config } from './config.js';

const nodeSockets = new Map();   // node_id → ws
const routerSockets = new Map(); // router_id → ws

const PLATFORM_CONTEXT = {
  iwiki: { repo: 'https://gitee.com/xindonhub/iwiki.git', local: '/root/SSE_Market/iWiki/code/iwiki', base_branch: 'master', notes: '部署走 iwiki.py；正式站合并须 Gitee PR' },
  market: { repo_server: '/root/SSE_Market/SSE_market_server', repo_client: '/root/SSE_Market/sse_market_new_client/newSSE', base_branch: 'master', notes: 'Go server 手工 docker run；前端 dist bind-mount 即时生效' },
  sso: { notes: 'UniSSO pod（阿里云），supervisord 管理' },
  xiaoqiu: { repo: '/root/SSE_Market/SSE-Agent/sse-agent', notes: 'delta 镜像发布，BASE_TAG 显式指定' },
};

function send(ws, msg) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)); }
function sendToNode(nodeId, msg) { send(nodeSockets.get(nodeId), msg); }
function recordDispatch(ticketId, workerId, action, payload) {
  db.prepare('INSERT INTO dispatches(ticket_id,worker_id,action,payload,at) VALUES(?,?,?,?,?)')
    .run(ticketId, workerId, action, payload ? JSON.stringify(payload) : null, now());
}
function setWorkerStatus(id, status) {
  db.prepare('UPDATE workers SET status=?, last_seen=? WHERE id=?').run(status, now(), id);
  emit('worker', { id, status });
}
const activeTw = (ticketId) => db.prepare("SELECT * FROM ticket_workers WHERE ticket_id=? AND state IN ('assigned','running')").all(ticketId);
const workerActive = (workerId) => db.prepare("SELECT 1 FROM ticket_workers WHERE worker_id=? AND state IN ('assigned','running') LIMIT 1").get(workerId);

/** 由关联表推导单状态：无人=staged；有人干活=assigned/running；全交付=review */
function recomputeTicket(ticketId) {
  const t = db.prepare('SELECT * FROM tickets WHERE id=?').get(ticketId);
  if (!t || ['done', 'rejected', 'review'].includes(t.status)) return;
  const tws = db.prepare('SELECT * FROM ticket_workers WHERE ticket_id=?').all(ticketId);
  const act = tws.filter(x => ['assigned', 'running'].includes(x.state));
  let status;
  if (!tws.length) status = 'staged';
  else if (act.length) status = act.some(x => x.state === 'running') ? 'running' : 'assigned';
  else if (tws.some(x => x.state === 'delivered')) status = 'review';
  else status = 'staged';
  if (status !== t.status) {
    db.prepare('UPDATE tickets SET status=?, updated_at=? WHERE id=?').run(status, now(), ticketId);
    emit('ticket', { id: ticketId, code: t.code, status });
  }
  if (status === 'review') {
    const results = tws.filter(x => x.state === 'delivered').map(x => ({ worker_id: x.worker_id, role: x.role, ...JSON.parse(x.result || '{}') }));
    db.prepare('UPDATE tickets SET result=? WHERE id=?').run(JSON.stringify(results.length === 1 ? results[0] : { multi: results }), ticketId);
  }
  if (status === 'staged') db.prepare('UPDATE tickets SET worker_id=NULL, lease_until=NULL WHERE id=?').run(ticketId);
}

/* ---------------------------- worker 生命周期 ---------------------------- */
export function createWorker(nodeId, { name, platform, repo, purpose = '', ticketId = null, createdBy = 'human' }) {
  const node = db.prepare('SELECT * FROM nodes WHERE id=?').get(nodeId);
  if (!node) throw new Error('节点不存在');
  if (node.status === 'offline') throw new Error('节点离线');
  const caps = JSON.parse(node.platforms || '[]');
  if (platform && caps.length && !caps.includes(platform)) throw new Error(`节点不支持平台 ${platform}`);
  const live = db.prepare("SELECT COUNT(*) c FROM workers WHERE node_id=? AND status IN ('creating','idle','busy')").get(nodeId).c;
  if (live >= node.max_workers) throw new Error(`节点 worker 数已达上限 ${node.max_workers}`);
  const repos = JSON.parse(node.repos || '{}');
  const finalRepo = repo || repos[platform] || '';
  if (!finalRepo) throw new Error(`节点无 ${platform} 仓库映射，请显式指定 repo`);
  const id = 'w-' + Math.random().toString(36).slice(2, 8);
  const ts = now();
  const finalPurpose = purpose || (ticketId ? (() => { const t = db.prepare('SELECT code,title FROM tickets WHERE id=?').get(ticketId); return t ? `${t.code} ${t.title}` : ''; })() : '');
  db.prepare(`INSERT INTO workers(id,node_id,name,harness,platform,repo,status,pending_ticket_id,purpose,created_by,registered_at,last_seen)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`).run(id, nodeId, name || `${platform}-${id.slice(2)}`, 'cc', platform || '', finalRepo, 'creating', ticketId, finalPurpose, createdBy, ts, ts);
  sendToNode(nodeId, { t: 'worker.create', worker_id: id, name: name || '', platform: platform || '', repo: finalRepo, purpose: finalPurpose });
  log('info', 'controller', `节点 ${nodeId} 创建 worker ${id}：${finalPurpose || '通用'}（${createdBy}）`);
  emit('worker', { id, status: 'creating', node_id: nodeId, purpose: finalPurpose });
  return id;
}

export function destroyWorker(workerId) {
  const w = db.prepare('SELECT * FROM workers WHERE id=?').get(workerId);
  if (!w) throw new Error('worker 不存在');
  if (workerActive(workerId)) throw new Error('worker 工作中，先取消任务');
  sendToNode(w.node_id, { t: 'worker.destroy', worker_id: workerId });
  db.prepare('DELETE FROM workers WHERE id=?').run(workerId);
  log('info', 'controller', `销毁 worker ${workerId}（会话 ${w.session_id || '未建立'}）`);
  emit('worker', { id: workerId, status: 'destroyed' });
}

/** 派单：把 worker 挂到单上（可多人协作）；主键防同一 worker 重复挂同一单 */
export function dispatchTicket(ticketId, workerId, { role = '', by = 'human' } = {}) {
  const t = db.prepare('SELECT * FROM tickets WHERE id=?').get(ticketId);
  if (!t) throw new Error('需求单不存在');
  if (['done', 'rejected'].includes(t.status)) throw new Error('单已关闭');
  const w = db.prepare('SELECT * FROM workers WHERE id=?').get(workerId);
  if (!w) throw new Error('worker 不存在');
  if (w.status !== 'idle') throw new Error(`worker 状态 ${w.status}，不可接单`);
  const node = db.prepare('SELECT * FROM nodes WHERE id=?').get(w.node_id);
  if (!node || node.status === 'offline') throw new Error('所在节点离线');
  const until = new Date(Date.now() + config.leaseSeconds * 1000).toISOString();
  const r = db.prepare(`INSERT OR IGNORE INTO ticket_workers(ticket_id,worker_id,role,state,lease_until,assigned_at)
    VALUES(?,?,?,?,?,?)`).run(ticketId, workerId, role || w.purpose || '', 'assigned', until, now());
  if (r.changes !== 1) throw new Error('该 worker 已在此单上（不重复挂）');
  db.prepare('UPDATE tickets SET worker_id=?, lease_until=?, attempts=attempts+1, updated_at=? WHERE id=?').run(workerId, until, now(), ticketId);
  setWorkerStatus(workerId, 'busy');
  recomputeTicket(ticketId);
  const payload = {
    t: 'dispatch', worker_id: workerId, role: role || w.purpose || '', by,
    ticket: { id: t.id, code: t.code, title: t.title, kind: t.kind, platform: t.platform, severity: t.severity, summary: t.summary, source_url: t.source_url },
    context: PLATFORM_CONTEXT[t.platform] || {},
    lease_seconds: config.leaseSeconds,
    branch_policy: 'fix/yz-' + t.code.toLowerCase(),
  };
  recordDispatch(t.id, workerId, 'dispatch', payload);
  sendToNode(w.node_id, payload);
  log('info', 'controller', `派发 ${t.code} → ${workerId}@${w.node_id}（角色：${role || w.purpose || '主责'}）`);
  emit('ticket', { id: t.id, code: t.code, status: db.prepare('SELECT status FROM tickets WHERE id=?').get(ticketId).status, worker_id: workerId });
  pushRoomRosterForTicket(ticketId);
  return true;
}

export function cancelTicket(ticketId, workerId = null, reason = '人工取消') {
  const t = db.prepare('SELECT * FROM tickets WHERE id=?').get(ticketId);
  if (!t) throw new Error('需求单不存在');
  const targets = workerId
    ? db.prepare("SELECT * FROM ticket_workers WHERE ticket_id=? AND worker_id=? AND state IN ('assigned','running')").all(ticketId, workerId)
    : activeTw(ticketId);
  if (!targets.length) throw new Error('无在途派发可取消');
  for (const tw of targets) {
    db.prepare("UPDATE ticket_workers SET state='cancelled' WHERE ticket_id=? AND worker_id=?").run(ticketId, tw.worker_id);
    const w = db.prepare('SELECT node_id FROM workers WHERE id=?').get(tw.worker_id);
    if (w) sendToNode(w.node_id, { t: 'cancel', ticket_id: ticketId, worker_id: tw.worker_id, reason });
    if (!workerActive(tw.worker_id)) setWorkerStatus(tw.worker_id, 'idle');
    recordDispatch(ticketId, tw.worker_id, 'cancel', { reason });
  }
  recomputeTicket(ticketId);
  log('info', 'controller', `${t.code} 取消 ${targets.length} 路派发：${reason}`);
}

export function reviewTicket(ticketId, accept, note = '') {
  const t = db.prepare('SELECT * FROM tickets WHERE id=?').get(ticketId);
  if (!t) throw new Error('需求单不存在');
  if (t.status !== 'review' && !(t.status === 'staged' && !accept)) throw new Error(`状态 ${t.status} 不可验收`);
  const status = accept ? 'done' : 'rejected';
  db.prepare('UPDATE tickets SET status=?, updated_at=?, result=COALESCE(result,?) WHERE id=?')
    .run(status, now(), JSON.stringify({ review_note: note }), ticketId);
  recordDispatch(ticketId, t.worker_id, accept ? 'accept' : 'reject', { note });
  log('info', 'controller', `${t.code} 人工验收 → ${status}${note ? '：' + note : ''}`);
  emit('ticket', { id: ticketId, code: t.code, status });
}

/* ---------------------------- router 自动派单 ---------------------------- */
const SEV_ORDER = { P0: 0, P1: 1, P2: 2, P3: 3 };
export function autoDispatchSweep() {
  if (kvGet('auto_dispatch', '1') === '0') return;
  const staged = db.prepare("SELECT * FROM tickets WHERE status='staged' ORDER BY id").all()
    .sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));
  for (const t of staged) {
    if (db.prepare('SELECT 1 FROM workers WHERE pending_ticket_id=?').get(t.id)) continue;
    const idle = db.prepare("SELECT * FROM workers WHERE status='idle' AND platform=? LIMIT 1").get(t.platform);
    if (idle) {
      try { dispatchTicket(t.id, idle.id, { by: config.routerId }); continue; } catch (e) { log('warn', 'controller', `自动派发失败：${e.message}`); continue; }
    }
    const node = db.prepare("SELECT * FROM nodes WHERE status='online'").all()
      .find(n => JSON.parse(n.platforms || '[]').includes(t.platform));
    if (!node) continue;
    try { createWorker(node.id, { platform: t.platform, ticketId: t.id, name: `${t.platform}-auto`, createdBy: config.routerId }); }
    catch (e) { /* 容量满：下轮再试 */ }
  }
}

/* ---------------------------- 雁鸣 room（worker 直连 + controller 兜底） ---------------------------- */
export function createRoom(name, ticketId, workerIds = []) {
  const id = 'room-' + Math.random().toString(36).slice(2, 10);
  db.prepare('INSERT INTO rooms(id,name,ticket_id,created_at) VALUES(?,?,?,?)').run(id, name, ticketId, now());
  const ins = db.prepare('INSERT OR IGNORE INTO room_members(room_id,worker_id) VALUES(?,?)');
  for (const w of workerIds) ins.run(id, w);
  log('info', 'room', `建立雁鸣 room ${id}（${name}）成员：${workerIds.join(', ') || '空'}`);
  emit('room', { id, name });
  pushRoomRoster(id);
  return id;
}
/** 把 room 名册（含各 worker 所在 node 的 peer_url）推给成员 node → worker 之间直连 */
function pushRoomRoster(roomId) {
  const members = db.prepare('SELECT worker_id FROM room_members WHERE room_id=?').all(roomId).map(r => r.worker_id);
  const roster = members.map(wid => {
    const w = db.prepare('SELECT node_id FROM workers WHERE id=?').get(wid);
    const n = w ? db.prepare('SELECT peer_url FROM nodes WHERE id=?').get(w.node_id) : null;
    return { worker_id: wid, node_id: w?.node_id || '', peer_url: n?.peer_url || '' };
  });
  for (const m of roster) if (m.node_id) sendToNode(m.node_id, { t: 'room.roster', room_id: roomId, members: roster });
}
function pushRoomRosterForTicket(ticketId) {
  for (const r of db.prepare('SELECT id FROM rooms WHERE ticket_id=?').all(ticketId)) pushRoomRoster(r.id);
}
function postRoomMessage(roomId, workerId, kind, body, via = 'direct') {
  const room = db.prepare('SELECT * FROM rooms WHERE id=?').get(roomId);
  if (!room) throw new Error('room 不存在');
  const at = now();
  db.prepare('INSERT INTO room_messages(room_id,worker_id,kind,body,at) VALUES(?,?,?,?,?)').run(roomId, workerId, `${kind}${via === 'relay' ? '(relay)' : ''}`, typeof body === 'string' ? body : JSON.stringify(body), at);
  // 直连为主：node 之间已互拨；controller 仅对直连失败的兜底转发
  if (via === 'relay') {
    const members = db.prepare('SELECT worker_id FROM room_members WHERE room_id=?').all(roomId).map(r => r.worker_id);
    for (const m of members) {
      if (m === workerId) continue;
      const w = db.prepare('SELECT node_id FROM workers WHERE id=?').get(m);
      if (w) sendToNode(w.node_id, { t: 'room.notify', room_id: roomId, worker_id: m, message: { worker_id: workerId, kind, body, at } });
    }
  }
  emit('room_message', { room_id: roomId, worker_id: workerId, kind, body, at });
}

/* ---------------------------- router 协作板（跨部门） ---------------------------- */
export function postRouterMessage(routerId, ticketId, body) {
  const at = now();
  db.prepare('INSERT INTO router_messages(router_id,ticket_id,body,at) VALUES(?,?,?,?)').run(routerId, ticketId || null, body, at);
  for (const [rid, ws] of routerSockets) if (rid !== routerId) send(ws, { t: 'board.notify', router_id: routerId, ticket_id: ticketId, body, at });
  log('info', 'router-board', `${routerId}${ticketId ? ' @单' + ticketId : ''}：${String(body).slice(0, 120)}`);
  emit('board', { router_id: routerId, ticket_id: ticketId, body, at });
}

/* ---------------------------- WS 接入 ---------------------------- */
function handleMessage(ws, msg) {
  const nodeId = ws.nodeId;
  switch (msg.t) {
    case 'node.register': {
      if (!config.agentToken || msg.token !== config.agentToken) { send(ws, { t: 'error', message: 'token 无效' }); ws.close(); return; }
      const id = String(msg.node_id || '').slice(0, 64);
      if (!id) { send(ws, { t: 'error', message: 'node_id 必填' }); ws.close(); return; }
      const ts = now();
      db.prepare(`INSERT INTO nodes(id,name,host,pod,platforms,repos,status,last_seen,registered_at,max_workers,meta,peer_url)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name, host=excluded.host, pod=excluded.pod,
          platforms=excluded.platforms, repos=excluded.repos, status='online', last_seen=excluded.last_seen,
          max_workers=excluded.max_workers, meta=excluded.meta, peer_url=excluded.peer_url`)
        .run(id, msg.name || id, msg.host || '', msg.pod || '', JSON.stringify(msg.platforms || []), JSON.stringify(msg.repos || {}), 'online', ts, ts, Number(msg.max_workers) || 3, JSON.stringify(msg.meta || {}), msg.peer_url || '');
      nodeSockets.set(id, ws);
      ws.nodeId = id;
      const reported = new Map((msg.workers || []).map(w => [w.worker_id, w]));
      for (const w of db.prepare('SELECT * FROM workers WHERE node_id=?').all(id)) {
        const r = reported.get(w.id);
        if (r) {
          db.prepare('UPDATE workers SET session_id=?, status=?, repo=?, platform=?, purpose=COALESCE(?,purpose), last_seen=? WHERE id=?')
            .run(r.session_id || w.session_id, r.busy ? 'busy' : 'idle', r.repo || w.repo, r.platform || w.platform, r.purpose || null, ts, w.id);
          reported.delete(w.id);
        } else if (w.status !== 'offline') setWorkerStatus(w.id, 'offline');
      }
      for (const [wid, r] of reported) {
        db.prepare(`INSERT INTO workers(id,node_id,name,harness,session_id,platform,repo,status,purpose,registered_at,last_seen)
          VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id, status=excluded.status, node_id=excluded.node_id`)
          .run(wid, id, r.name || wid, 'cc', r.session_id || '', r.platform || '', r.repo || '', r.busy ? 'busy' : 'idle', r.purpose || '', ts, ts);
      }
      send(ws, { t: 'node.registered', node_id: id, server_time: ts, heartbeat_seconds: config.heartbeatSeconds });
      log('info', 'controller', `节点 ${id} 上线（peer=${msg.peer_url || '无'}，平台 ${(msg.platforms || []).join(',') || '通用'}）`);
      emit('node', { id, status: 'online' });
      // 重连后补推 room 名册
      for (const r of db.prepare('SELECT room_id FROM room_members WHERE worker_id IN (SELECT id FROM workers WHERE node_id=?)').all(id)) pushRoomRoster(r.room_id);
      break;
    }
    case 'node.heartbeat': {
      if (!nodeId) return;
      db.prepare('UPDATE nodes SET last_seen=? WHERE id=?').run(now(), nodeId);
      db.prepare("UPDATE workers SET last_seen=? WHERE node_id=? AND status IN ('idle','busy')").run(now(), nodeId);
      const until = new Date(Date.now() + config.leaseSeconds * 1000).toISOString();
      db.prepare(`UPDATE ticket_workers SET lease_until=? WHERE state IN ('assigned','running')
        AND worker_id IN (SELECT id FROM workers WHERE node_id=?)`).run(until, nodeId);
      send(ws, { t: 'pong', server_time: now() });
      break;
    }
    case 'router.register': {
      if (!config.agentToken || msg.token !== config.agentToken) { send(ws, { t: 'error', message: 'token 无效' }); ws.close(); return; }
      const id = String(msg.router_id || '').slice(0, 64);
      if (!id) { send(ws, { t: 'error', message: 'router_id 必填' }); ws.close(); return; }
      const ts = now();
      db.prepare(`INSERT INTO routers(id,name,observes,peer_url,status,last_seen,registered_at) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name, observes=excluded.observes, peer_url=excluded.peer_url, status='online', last_seen=excluded.last_seen`)
        .run(id, msg.name || id, JSON.stringify(msg.observes || []), msg.peer_url || '', 'online', ts, ts);
      routerSockets.set(id, ws);
      ws.routerId = id;
      send(ws, { t: 'router.registered', router_id: id, server_time: ts, peers: db.prepare("SELECT id,name,observes,status FROM routers WHERE id != ?").all(id) });
      log('info', 'controller', `router ${id} 上线（观测 ${(msg.observes || []).join(',') || '?'}）`);
      emit('router', { id, status: 'online' });
      break;
    }
    case 'router.heartbeat': {
      if (!ws.routerId) return;
      db.prepare('UPDATE routers SET last_seen=? WHERE id=?').run(now(), ws.routerId);
      send(ws, { t: 'pong', server_time: now() });
      break;
    }
    case 'router.post': {
      if (!ws.routerId) return;
      postRouterMessage(ws.routerId, msg.ticket_id || null, String(msg.body || ''));
      break;
    }
    case 'worker.created': {
      if (!nodeId) return;
      const w = db.prepare('SELECT * FROM workers WHERE id=? AND node_id=?').get(msg.worker_id, nodeId);
      if (!w) return;
      db.prepare('UPDATE workers SET session_id=?, status=? WHERE id=?').run(msg.session_id || '', 'idle', msg.worker_id);
      log('info', 'controller', `worker ${msg.worker_id} 会话就绪（使命：${w.purpose || '通用'}）`);
      emit('worker', { id: msg.worker_id, status: 'idle', session_id: msg.session_id });
      if (w.pending_ticket_id) {
        db.prepare('UPDATE workers SET pending_ticket_id=NULL WHERE id=?').run(msg.worker_id);
        try { dispatchTicket(w.pending_ticket_id, msg.worker_id); } catch (e) { log('warn', 'controller', `待接单派发失败：${e.message}`); }
      }
      break;
    }
    case 'worker.create_failed': {
      if (!nodeId) return;
      db.prepare('DELETE FROM workers WHERE id=?').run(msg.worker_id);
      log('error', 'controller', `worker ${msg.worker_id} 创建失败：${msg.reason || ''}`);
      emit('worker', { id: msg.worker_id, status: 'destroyed' });
      break;
    }
    case 'worker.destroyed': {
      if (!nodeId) return;
      db.prepare('DELETE FROM workers WHERE id=? AND node_id=?').run(msg.worker_id, nodeId);
      emit('worker', { id: msg.worker_id, status: 'destroyed' });
      break;
    }
    case 'ack': {
      if (!nodeId) return;
      const r = db.prepare("UPDATE ticket_workers SET state='running' WHERE ticket_id=? AND worker_id=? AND state='assigned'").run(msg.ticket_id, msg.worker_id);
      if (r.changes) { recordDispatch(msg.ticket_id, msg.worker_id, 'ack', msg); recomputeTicket(msg.ticket_id); }
      break;
    }
    case 'nack': {
      if (!nodeId) return;
      const r = db.prepare("UPDATE ticket_workers SET state='cancelled' WHERE ticket_id=? AND worker_id=? AND state='assigned'").run(msg.ticket_id, msg.worker_id);
      if (r.changes) {
        recordDispatch(msg.ticket_id, msg.worker_id, 'nack', { reason: msg.reason });
        if (!workerActive(msg.worker_id)) setWorkerStatus(msg.worker_id, 'idle');
        recomputeTicket(msg.ticket_id);
        log('warn', 'controller', `worker ${msg.worker_id} 拒单 ${msg.ticket_id}：${msg.reason || ''}`);
      }
      break;
    }
    case 'progress': {
      if (!nodeId) return;
      recordDispatch(msg.ticket_id, msg.worker_id, 'progress', msg);
      emit('progress', { ticket_id: msg.ticket_id, worker_id: msg.worker_id, step: msg.step, message: msg.message, pct: msg.pct });
      break;
    }
    case 'result': {
      if (!nodeId) return;
      const r = db.prepare("UPDATE ticket_workers SET state='delivered', result=? WHERE ticket_id=? AND worker_id=? AND state IN ('assigned','running')")
        .run(JSON.stringify(msg.result || msg), msg.ticket_id, msg.worker_id);
      if (r.changes) {
        recordDispatch(msg.ticket_id, msg.worker_id, 'result', msg);
        if (!workerActive(msg.worker_id)) setWorkerStatus(msg.worker_id, 'idle');
        recomputeTicket(msg.ticket_id);
        log('info', 'controller', `worker ${msg.worker_id} 交付 ${msg.ticket_id}（${activeTw(msg.ticket_id).length} 路仍在途）`);
      }
      break;
    }
    case 'room.post': {
      if (!nodeId) return;
      // node 直连失败时才走 controller 兜底（via=relay）；直连成功的 node 也会抄送落账（via=direct-copy）
      try { postRoomMessage(msg.room_id, msg.worker_id, msg.kind || 'note', msg.body, msg.via === 'relay' ? 'relay' : 'direct'); }
      catch (e) { send(ws, { t: 'error', message: e.message }); }
      break;
    }
    default:
      send(ws, { t: 'error', message: `未知消息类型 ${msg.t}` });
  }
}

export function attachController(httpServer) {
  const wss = new WebSocketServer({ server: httpServer, path: '/api/agent/channel' });
  wss.on('connection', (ws) => {
    ws.on('message', (raw) => {
      let msg; try { msg = JSON.parse(raw.toString()); } catch { return; }
      try { handleMessage(ws, msg); } catch (e) {
        log('error', 'controller', `处理消息失败：${e.message}`);
        send(ws, { t: 'error', message: e.message });
      }
    });
    ws.on('close', () => {
      if (ws.nodeId && nodeSockets.get(ws.nodeId) === ws) {
        const id = ws.nodeId;
        nodeSockets.delete(id);
        db.prepare("UPDATE nodes SET status='offline' WHERE id=?").run(id);
        db.prepare("UPDATE workers SET status='offline' WHERE node_id=? AND status != 'offline'").run(id);
        log('info', 'controller', `节点 ${id} 断开（在途租约保留至到期）`);
        emit('node', { id, status: 'offline' });
      }
      if (ws.routerId && routerSockets.get(ws.routerId) === ws) {
        const id = ws.routerId;
        routerSockets.delete(id);
        db.prepare("UPDATE routers SET status='offline' WHERE id=?").run(id);
        emit('router', { id, status: 'offline' });
      }
    });
    ws.on('error', () => {});
    send(ws, { t: 'hello', service: 'yanzhen-controller', protocol: 3 });
  });

  setInterval(() => {
    // 租约巡检（按 单×worker 路）
    const expired = db.prepare("SELECT ticket_id, worker_id FROM ticket_workers WHERE state IN ('assigned','running') AND lease_until < ?").all(now());
    for (const x of expired) {
      db.prepare("UPDATE ticket_workers SET state='expired' WHERE ticket_id=? AND worker_id=?").run(x.ticket_id, x.worker_id);
      if (!workerActive(x.worker_id)) setWorkerStatus(x.worker_id, 'idle');
      recordDispatch(x.ticket_id, x.worker_id, 'lease_expired', {});
      recomputeTicket(x.ticket_id);
      log('warn', 'controller', `单${x.ticket_id}×${x.worker_id} 租约到期回收`);
    }
    // 挂接指向已不存在的 worker（会话被销毁/清理）→ 作废挂接
    for (const x of db.prepare("SELECT ticket_id, worker_id FROM ticket_workers WHERE state IN ('assigned','running') AND worker_id NOT IN (SELECT id FROM workers)").all()) {
      db.prepare("UPDATE ticket_workers SET state='cancelled' WHERE ticket_id=? AND worker_id=?").run(x.ticket_id, x.worker_id);
      recordDispatch(x.ticket_id, x.worker_id, 'worker_gone', {});
      recomputeTicket(x.ticket_id);
      log('warn', 'controller', `单${x.ticket_id} 挂接的 ${x.worker_id} 已不存在，作废`);
    }
    // 孤儿单回收：状态在途但已无挂接 worker（会话被销毁/清理）→ 回暂存
    for (const t of db.prepare("SELECT id FROM tickets WHERE status IN ('assigned','running')").all()) {
      if (!activeTw(t.id).length) {
        db.prepare("UPDATE tickets SET status='staged', worker_id=NULL, lease_until=NULL, updated_at=? WHERE id=?").run(now(), t.id);
        log('warn', 'controller', `单${t.id} 无在途挂接，回收暂存`);
        emit('ticket', { id: t.id, status: 'staged' });
      }
    }
    const cutoff = new Date(Date.now() - config.heartbeatSeconds * 4 * 1000).toISOString();
    for (const n of db.prepare("SELECT id FROM nodes WHERE status='online' AND last_seen < ?").all(cutoff)) {
      db.prepare("UPDATE nodes SET status='offline' WHERE id=?").run(n.id);
      db.prepare("UPDATE workers SET status='offline' WHERE node_id=?").run(n.id);
      emit('node', { id: n.id, status: 'offline' });
    }
    for (const r of db.prepare("SELECT id FROM routers WHERE status='online' AND last_seen < ?").all(cutoff)) {
      db.prepare("UPDATE routers SET status='offline' WHERE id=?").run(r.id);
      emit('router', { id: r.id, status: 'offline' });
    }
    autoDispatchSweep();
  }, 15_000).unref?.();

  log('info', 'controller', 'controller v3 就绪：node/worker会话/router协作板/多worker挂单/room直连名册');
}

export function onlineNodeIds() { return [...nodeSockets.keys()]; }
export function onlineRouterIds() { return [...routerSockets.keys()]; }

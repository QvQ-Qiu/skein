// router 作为 peer 拨入 controller：名册在线 + 跨部门协作板发言。
// router 数量=独立可观测域数量（少）；协作走板（router_messages），不互相直连。
import WebSocket from 'ws';
import { config } from './config.js';
import { log } from './db.js';

let ws = null, backoff = 1000, hb = null;
const queue = [];

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  else queue.push(msg);
}

export function postBoard(ticketId, body) {
  send({ t: 'router.post', ticket_id: ticketId || null, body });
}

export function startRouterPeer() {
  const url = `ws://127.0.0.1:${config.port}/api/agent/channel`;
  const connect = () => {
    ws = new WebSocket(url);
    ws.on('open', () => {
      backoff = 1000;
      send({ t: 'router.register', router_id: config.routerId, token: config.agentToken, name: config.routerId, observes: config.observes });
      clearInterval(hb);
      hb = setInterval(() => send({ t: 'router.heartbeat' }), 15_000);
      while (queue.length) send(queue.shift());
    });
    ws.on('message', (raw) => {
      let m; try { m = JSON.parse(raw.toString()); } catch { return; }
      if (m.t === 'router.registered') log('info', 'router-peer', `本 router ${config.routerId} 已入名册（观测 ${config.observes.join(',')}），同僚 ${(m.peers || []).map(p => p.id).join(',') || '无'}`);
      if (m.t === 'board.notify') log('info', 'router-peer', `协作板 ${m.router_id}${m.ticket_id ? '@单' + m.ticket_id : ''}：${String(m.body).slice(0, 120)}`);
    });
    ws.on('close', () => { clearInterval(hb); setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 30_000); });
    ws.on('error', () => {});
  };
  connect();
}

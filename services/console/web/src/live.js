// SSE 实时流 composable + toast
import { reactive } from 'vue';

export const live = reactive({ connected: false, last: null, seq: 0 });
export const toasts = reactive([]);
let es = null;

export function toast(msg) {
  const t = { id: Math.random().toString(36).slice(2), msg };
  toasts.push(t);
  setTimeout(() => { const i = toasts.indexOf(t); if (i >= 0) toasts.splice(i, 1); }, 4200);
}

export function connectLive() {
  if (es) es.close();
  es = new EventSource('/api/stream');
  es.onopen = () => { live.connected = true; };
  es.onerror = () => { live.connected = false; };
  es.onmessage = (m) => {
    let evt; try { evt = JSON.parse(m.data); } catch { return; }
    live.last = evt; live.seq++;
    const p = evt.payload || {};
    if (evt.type === 'ticket' && p.status === 'review') toast(`${p.code || '单' + p.id} 交付，待验收`);
    if (evt.type === 'node' && p.status === 'online') toast(`节点 ${p.id} 上线`);
    if (evt.type === 'router' && p.status === 'online') toast(`router ${p.id} 入列`);
    if (evt.type === 'worker' && p.status === 'idle' && p.session_id) toast(`会话 ${p.id} 就绪`);
    if (evt.type === 'board') toast(`协作板 ${p.router_id} 喊话`);
  };
}
export function disconnectLive() { if (es) { es.close(); es = null; } live.connected = false; }

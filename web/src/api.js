// API 客户端：统一鉴权失败回落地页
import { reactive } from 'vue';

export const session = reactive({ me: null, checked: false });

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...opts });
  if (res.status === 401) { session.me = null; throw new ApiError(401, '未登录'); }
  if (!res.ok) { const d = await res.json().catch(() => ({})); throw new ApiError(res.status, d.error || `HTTP ${res.status}`); }
  return res.json();
}

export async function loadMe() {
  try { session.me = await api('/auth/me'); } catch { session.me = null; }
  session.checked = true;
  return session.me;
}

export const fmtT = (s) => (s || '').replace('T', ' ').slice(5, 16);
export const KIND_TXT = { bug: '缺陷', feature: '需求', question: '疑问', noise: '噪音' };
export const STATUS_TXT = { staged: '暂存', assigned: '已派发', running: '进行中', review: '待验收', done: '完成', rejected: '已驳回' };
export const WSTATUS_TXT = { creating: '建会话中', idle: '空闲', busy: '工作中', offline: '离线' };
export const PLATFORM_TXT = { market: '主站', iwiki: 'iWiki', sso: 'SSO', applet: '小程序', xiaoqiu: '小秋', join: '招募', cloud: '集市云', other: '其他' };

export function summarizePayload(x) {
  try {
    const p = JSON.parse(x.payload);
    if (x.action === 'progress') return `${p.step || ''} ${p.message || ''}`;
    if (x.action === 'dispatch') return `${p.ticket?.title || ''}${p.by ? ' · by ' + p.by : ''}${p.role ? ' · 角色 ' + p.role : ''}`;
    if (x.action === 'result') return p.result?.summary || p.summary || '';
    if (x.action === 'worker.create' || x.action === 'cancel' || x.action === 'nack') return p.reason || p.purpose || JSON.stringify(p).slice(0, 80);
    return JSON.stringify(p);
  } catch { return x.payload || ''; }
}

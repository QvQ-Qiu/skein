<script setup>
// 网状拓扑：力导布局 SVG；点可点击进实体详情；边=宿主/派单/lineage/room/协作板
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter } from 'vue-router';
const props = defineProps({ topo: Object });
const router = useRouter();
const svg = ref(null);
const render = ref('');
const pos = new Map();
let raf = null, cool = 0, running = false;

const ANCHOR = { router: .16, node: .48, worker: .8, human: .16 };
function items() {
  const t = props.topo; if (!t) return [];
  return [
    ...t.routers.map(r => ({ id: r.id, kind: r.kind || 'router', label: r.name || r.id, online: r.online, sub: (r.observes || []).join(',') })),
    ...t.nodes.map(n => ({ id: n.id, kind: 'node', label: n.name || n.id, online: n.online })),
    ...t.workers.map(w => ({ id: w.id, kind: 'worker', label: w.id, online: w.status !== 'offline', status: w.status, sub: w.purpose || w.platform })),
  ];
}
function tick() {
  const el = svg.value; if (!el || !props.topo) { running = false; return; }
  raf = requestAnimationFrame(tick);
  const bw = el.clientWidth || 1200, bh = el.clientHeight || 460;
  const its = items();
  for (const it of its) if (!pos.has(it.id)) pos.set(it.id, { x: bw * (ANCHOR[it.kind] || .5) + (Math.random() - .5) * 120, y: bh * .5 + (Math.random() - .5) * bh * .7, vx: 0, vy: 0 });
  const P = (id) => pos.get(id);
  for (let i = 0; i < its.length; i++) for (let j = i + 1; j < its.length; j++) {
    const a = P(its[i].id), b = P(its[j].id);
    let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy || 1;
    if (d2 < 260 * 260) { const f = 1500 / d2, d = Math.sqrt(d2); dx /= d; dy /= d; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f; }
  }
  for (const e of props.topo.edges) {
    const a = P(e.a), b = P(e.b); if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
    const f = (d - 120) * .012; a.vx += dx / d * f; a.vy += dy / d * f; b.vx -= dx / d * f; b.vy -= dy / d * f;
  }
  let energy = 0;
  for (const it of its) {
    const p = P(it.id);
    p.vx += (bw * (ANCHOR[it.kind] || .5) - p.x) * .004; p.vy += (bh * .5 - p.y) * .002;
    p.vx *= .7; p.vy *= .7;
    p.x = Math.max(30, Math.min(bw - 30, p.x + p.vx)); p.y = Math.max(26, Math.min(bh - 30, p.y + p.vy));
    energy += Math.abs(p.vx) + Math.abs(p.vy);
  }
  // 冷却收敛：稳定后停渲染，节点可点；拓扑签名变化再唤醒
  cool = (energy / Math.max(1, its.length)) < .5 ? cool + 1 : 0;
  if (cool > 12) { running = false; cancelAnimationFrame(raf); }
  draw(its);
}
let lastVisual = '';
function draw(its) {
  const el = svg.value; if (!el || !props.topo) return;
  const P = (id) => pos.get(id);
  // 视觉签名（位置+状态+边）不变则不重绘，保证 DOM 稳定可点击
  const vis = lastSig + '|' + its.map(it => { const p = P(it.id); return p ? p.x.toFixed(1) + ',' + p.y.toFixed(1) : '?'; }).join(';');
  if (vis === lastVisual) return;
  lastVisual = vis;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const edges = props.topo.edges.map(e => {
    const a = P(e.a), b = P(e.b); if (!a || !b) return '';
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    return `<path class="edge ${e.kind}" d="M${a.x.toFixed(1)} ${a.y.toFixed(1)} Q${mx.toFixed(1)} ${(my - 14).toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}"/>${e.label ? `<text class="elabel" x="${mx.toFixed(1)}" y="${(my - 16).toFixed(1)}" text-anchor="middle">${esc(e.label)}</text>` : ''}`;
  }).join('');
  const glyphs = its.map(it => {
    const p = P(it.id);
    const col = it.kind === 'router' ? 'var(--violet)' : it.kind === 'node' ? 'var(--cyan)' : it.kind === 'human' ? '#fff' : it.status === 'busy' ? 'var(--amber)' : it.status === 'idle' ? 'var(--green)' : it.status === 'creating' ? 'var(--violet)' : 'var(--dim)';
    const shape = it.kind === 'router' ? `<path d="M0 -11 L9 0 L0 11 L-9 0 Z" fill="rgba(177,140,255,.16)" stroke="${col}" stroke-width="1.4"/>`
      : it.kind === 'node' ? `<path d="M-10 -6 L0 -12 L10 -6 L10 6 L0 12 L-10 6 Z" fill="rgba(79,216,235,.12)" stroke="${col}" stroke-width="1.4"/>`
      : `<circle r="${it.kind === 'human' ? 5 : 8}" fill="${col}22" stroke="${col}" stroke-width="1.4"/>${it.status === 'busy' || it.status === 'creating' ? `<circle class="ring" r="13" stroke="${col}"><animate attributeName="opacity" values=".7;.1;.7" dur="1.4s" repeatCount="indefinite"/></circle>` : ''}`;
    const path = it.kind === 'human' ? '/cluster' : `/${it.kind}/${it.id}`;
    return `<g class="glyph" style="color:${col}" transform="translate(${p.x.toFixed(1)},${p.y.toFixed(1)})" data-path="${path}"><title>${esc(it.label)}${it.sub ? ' · ' + esc(it.sub) : ''}（点击查看详情）</title>${shape}<text class="nlabel" y="24" text-anchor="middle">${esc(it.label).slice(0, 18)}</text></g>`;
  }).join('');
  render.value = edges + glyphs;
}
function onClick(ev) {
  const g = ev.target.closest('.glyph');
  if (g) router.push(g.dataset.path);
}
function wake() { if (!running) { running = true; cool = 0; raf = requestAnimationFrame(tick); } }
const sigOf = (t) => JSON.stringify([
  ...t.routers.map(r => r.id), ...t.nodes.map(n => n.id), ...t.workers.map(w => w.id + ':' + w.status),
  ...t.edges.map(e => [e.a, e.b, e.kind, e.label]),
]);
let lastSig = '';
onMounted(() => { wake(); svg.value.addEventListener('click', onClick); });
onBeforeUnmount(() => { running = false; cancelAnimationFrame(raf); svg.value?.removeEventListener('click', onClick); });
watch(() => props.topo, (t) => {
  if (!t) return;
  const s = sigOf(t);
  if (s !== lastSig) { lastSig = s; wake(); }
  else draw(items()); // 仅状态色变化：单帧渲染，不打断冷却
});
</script>
<template>
  <div id="mesh-wrap"><svg id="mesh" ref="svg" v-html="render"></svg></div>
  <div class="legend">
    <span><i style="background:rgba(79,216,235,.5)"></i>宿主</span>
    <span><i style="background:var(--amber)"></i>在途派单</span>
    <span><i style="background:rgba(177,140,255,.6)"></i>创建 lineage</span>
    <span><i style="background:rgba(87,217,163,.7)"></i>room 直连</span>
    <span><i style="background:rgba(110,168,254,.6)"></i>跨部门协作板</span>
    <span class="dim">◆ router ⬡ node ● worker会话 · 点击进详情</span>
  </div>
</template>

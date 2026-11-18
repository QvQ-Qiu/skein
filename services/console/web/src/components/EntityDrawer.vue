<script setup>
// 实体详情：运行状态(live 5s 刷新) + 下发过的命令(全量可展开) + 关联(可点跳转) + 事件日志
import { ref, watch, onBeforeUnmount, computed } from 'vue';
import { useRouter } from 'vue-router';
import { api, fmtT, WSTATUS_TXT, PLATFORM_TXT, summarizePayload } from '../api.js';
import { live } from '../live.js';
import { session } from '../api.js';

const props = defineProps({ kind: String, id: String });
const emit = defineEmits(['close']);
const router = useRouter();
const data = ref(null);
const openCmd = ref(null);
let poll = null;

async function load() {
  try { data.value = await api(`/api/entity/${props.kind}/${props.id}`); }
  catch { data.value = null; }
}
watch(() => [props.kind, props.id], () => { load(); openCmd.value = null; }, { immediate: true });
watch(() => live.seq, () => { if (data.value) load(); });
poll = setInterval(load, 5000);
onBeforeUnmount(() => clearInterval(poll));

const go = (path) => router.push(path);
async function destroy() {
  if (!window.confirm('销毁该会话？')) return;
  await api('/api/workers/' + props.id, { method: 'DELETE' });
  emit('close');
}
function payloadOf(c) { try { return JSON.stringify(JSON.parse(c.payload), null, 2); } catch { return c.payload || ''; } }
const leaseLeft = (until) => {
  if (!until) return '—';
  const s = Math.max(0, Math.round((new Date(until) - Date.now()) / 1000));
  return `${Math.floor(s / 60)}m${s % 60}s`;
};
const relations = computed(() => {
  const d = data.value; if (!d) return [];
  const out = [];
  if (props.kind === 'worker') {
    if (d.node) out.push(['node', d.node.id, '宿主节点', d.node.name || d.node.id]);
    if (d.base.created_by && !d.base.created_by.startsWith('human')) out.push(['router', d.base.created_by, '创建者 router', d.base.created_by]);
    d.rooms.forEach(r => out.push(['room', r.id, 'room', r.name]));
    d.coWorkers.forEach(w => out.push(['worker', w, '同单协作', w]));
    d.tws.slice(0, 8).forEach(t => out.push(['tickets', t.ticket_id, '接过单', `${t.code} ${t.title}`]));
  } else if (props.kind === 'node') {
    d.workers.forEach(w => out.push(['worker', w.id, 'worker 会话', `${w.id} · ${WSTATUS_TXT[w.status] || w.status}`]));
    d.routers.forEach(r => out.push(['router', r, '创建者 router', r]));
    d.rooms.forEach(r => out.push(['room', r.id, 'room', r.name]));
  } else {
    d.createdWorkers.forEach(w => out.push(['worker', w.id, '创建的会话', `${w.id} · ${w.purpose || ''}`]));
    d.coRouters.forEach(r => out.push(['router', r, '协作板同僚', r]));
    [...new Set(d.commands.map(c => c.ticket_id))].slice(0, 8).forEach(t => out.push(['tickets', t, '派发过单', '单#' + t]));
  }
  return out;
});
</script>

<template>
  <div class="drawer-mask open" @click="emit('close')"></div>
  <div class="drawer open">
    <button class="close" @click="emit('close')">✕</button>
    <template v-if="data">
      <h3>
        {{ data.base.name || data.base.id }}
        <span class="tag" :class="kind === 'worker' ? (data.base.status === 'busy' ? 'busy' : data.base.status === 'idle' ? 'idle' : 'offline') : (data.base.online || data.base.status === 'online' ? 'online' : 'offline')">
          {{ kind === 'worker' ? (WSTATUS_TXT[data.base.status] || data.base.status) : (data.base.online || data.base.status === 'online' ? '在线' : '离线') }}
        </span>
      </h3>
      <div class="kv">
        <template v-if="kind === 'worker'">
          <span class="k">使命</span><span style="color:var(--amber)">{{ data.base.purpose || '通用' }}</span>
          <span class="k">cc 会话</span><span class="mono" style="font-size:11px">{{ data.base.session_id || '建立中…' }}</span>
          <span class="k">平台/仓库</span><span class="mono" style="font-size:11px">{{ PLATFORM_TXT[data.base.platform] || data.base.platform || '通用' }} @ {{ data.base.repo }}</span>
          <span class="k">创建者</span><span class="mono">{{ data.base.created_by || '—' }}</span>
        </template>
        <template v-else-if="kind === 'node'">
          <span class="k">宿主/pod</span><span class="mono">{{ data.base.host }} {{ data.base.pod }}</span>
          <span class="k">平台</span><span>{{ data.base.platforms.map(p => PLATFORM_TXT[p] || p).join(', ') || '通用' }}</span>
          <span class="k">peer 口</span><span class="mono" style="font-size:11px">{{ data.base.peer_url || '—' }}</span>
          <span class="k">容量</span><span class="mono">{{ data.workers.length }}/{{ data.base.max_workers }}</span>
          <span class="k">心跳</span><span class="mono">{{ fmtT(data.base.last_seen) }}</span>
        </template>
        <template v-else>
          <span class="k">观测域</span><span>{{ data.base.observes.map(p => PLATFORM_TXT[p] || p).join(', ') || '—' }}</span>
          <span class="k">心跳</span><span class="mono">{{ fmtT(data.base.last_seen) }}</span>
        </template>
      </div>

      <div class="block">
        <div class="label">运行状态 · LIVE</div>
        <template v-if="kind === 'worker'">
          <div v-for="a in data.live" :key="a.ticket_id" style="margin-bottom:8px">
            <div class="feed-item"><a href="#" @click.prevent="go('/tickets/' + a.ticket_id)">{{ a.code }}</a><span>{{ a.title }}</span><span class="tag running">{{ a.state }}</span><span class="dim mono">租约余 {{ leaseLeft(a.lease_until) }}</span></div>
            <div class="feed-item" v-for="p in a.progress" :key="p.id"><span class="t">{{ fmtT(p.at) }}</span><span class="dim">{{ summarizePayload(p) }}</span></div>
          </div>
          <span v-if="!data.live.length" class="dim">空闲，无在途任务</span>
        </template>
        <template v-else-if="kind === 'node'">
          <div class="feed-item" v-for="w in data.live" :key="w.id">
            <a href="#" @click.prevent="go('/worker/' + w.id)">{{ w.id }}</a>
            <span class="tag" :class="w.status === 'busy' ? 'busy' : w.status === 'idle' ? 'idle' : 'offline'">{{ WSTATUS_TXT[w.status] || w.status }}</span>
            <span class="dim">{{ w.purpose || '' }}</span>
            <a v-for="a in w.active" :key="a.ticket_id" href="#" @click.prevent="go('/tickets/' + a.ticket_id)">{{ a.code }}</a>
          </div>
        </template>
        <template v-else>
          <span class="dim">{{ data.live.online ? '在线观测中' : '离线' }} · 最近发言 {{ data.messages.length ? fmtT(data.messages[0].at) : '—' }}</span>
        </template>
      </div>

      <div class="block">
        <div class="label">下发过的命令（{{ data.commands.length }}）</div>
        <div v-for="c in data.commands" :key="c.id" class="feed-item" style="cursor:pointer;flex-wrap:wrap" @click="openCmd = openCmd === c.id ? null : c.id">
          <span class="t">{{ fmtT(c.at) }}</span>
          <span class="tag" :class="c.action === 'dispatch' ? 'assigned' : c.action === 'result' ? 'review' : c.action === 'progress' ? 'running' : 'noise'">{{ c.action }}</span>
          <span v-if="c.worker_id" class="mono dim">{{ c.worker_id }}</span>
          <span class="grow dim" style="font-size:12px">{{ summarizePayload(c).slice(0, 90) }}</span>
          <pre v-if="openCmd === c.id && c.payload" class="code" style="width:100%">{{ payloadOf(c) }}</pre>
        </div>
        <span v-if="!data.commands.length" class="dim">暂无命令记录</span>
      </div>

      <div class="block" v-if="kind === 'router' && data.messages.length">
        <div class="label">协作板发言</div>
        <div class="feed-item" v-for="m in data.messages" :key="m.id">
          <span class="t">{{ fmtT(m.at) }}</span>
          <a v-if="m.ticket_id" href="#" @click.prevent="go('/tickets/' + m.ticket_id)">单#{{ m.ticket_id }}</a>
          <span>{{ m.body }}</span>
        </div>
      </div>

      <div class="block">
        <div class="label">关联（点击跳转）</div>
        <div class="feed-item" v-for="[k, id, rel, label] in relations" :key="k + id">
          <span class="tag platform">{{ rel }}</span>
          <a href="#" @click.prevent="go(k === 'tickets' ? '/tickets/' + id : '/' + k + '/' + id)">{{ label }}</a>
        </div>
        <span v-if="!relations.length" class="dim">暂无关联</span>
      </div>

      <div class="block">
        <div class="label">事件日志</div>
        <div class="feed-item" v-for="l in data.logs" :key="l.id"><span class="t">{{ fmtT(l.at) }}</span><span class="dim">{{ l.message }}</span></div>
      </div>

      <div class="actions" v-if="kind === 'worker' && session.me.is_admin">
        <button class="danger" :disabled="data.base.status === 'busy'" @click="destroy">销毁会话</button>
      </div>
    </template>
    <div v-else class="dim" style="padding:30px 0">加载中…</div>
  </div>
</template>

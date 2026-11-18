<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter } from 'vue-router';
import MeshGraph from '../components/MeshGraph.vue';
import { api, fmtT, PLATFORM_TXT, WSTATUS_TXT, summarizePayload, session } from '../api.js';
import { live, toast } from '../live.js';

const router = useRouter();
const o = ref(null); const topo = ref(null); const nodes = ref([]); const dispatches = ref([]);
const auto = ref(true);
let poll = null;
async function load() {
  o.value = await api('/api/overview');
  auto.value = !!o.value.auto_dispatch;
  topo.value = await api('/api/topology');
  nodes.value = await api('/api/nodes');
  const tickets = await api('/api/tickets?status=all');
  const all = [];
  for (const t of tickets.slice(0, 40)) {
    const d = await api('/api/tickets/' + t.id);
    d.dispatches.forEach(x => all.push({ ...x, code: t.code }));
  }
  all.sort((a, b) => b.id - a.id);
  dispatches.value = all.slice(0, 60);
}
onMounted(() => { load(); poll = setInterval(load, 20000); });
onBeforeUnmount(() => clearInterval(poll));
watch(() => live.seq, () => { clearTimeout(watch._t); watch._t = setTimeout(load, 800); });
async function toggleAuto(v) {
  try { await api('/api/auto-dispatch', { method: 'POST', body: JSON.stringify({ on: v }) }); toast(v ? 'router 自动派单：开' : 'router 自动派单：关'); }
  catch (e) { toast(e.message); auto.value = !v; }
}
async function pollNow() {
  try { const r = await api('/api/ingest/run', { method: 'POST' }); toast(`采集：帖 +${r.posts ?? 0} 评论 +${r.comments ?? 0}`); load(); }
  catch (e) { toast(e.message); }
}
</script>

<template>
  <section class="view active">
    <div class="stats" v-if="o">
      <div class="stat" :class="o.routers.online ? 'violet' : 'dim'"><div class="n">{{ o.routers.online }}/{{ o.routers.total }}</div><div class="l">ROUTER 在线</div></div>
      <div class="stat" :class="o.nodes.online ? 'cyan' : 'dim'"><div class="n">{{ o.nodes.online }}/{{ o.nodes.total }}</div><div class="l">节点在线</div></div>
      <div class="stat green"><div class="n">{{ o.workers.idle }}/{{ o.workers.busy }}</div><div class="l">空闲/工作会话</div></div>
      <div class="stat amber"><div class="n">{{ o.tickets.staged }}</div><div class="l">暂存需求</div></div>
      <div class="stat violet"><div class="n">{{ o.tickets.review }}</div><div class="l">待验收</div></div>
      <div class="stat cyan"><div class="n">{{ o.events.today }}</div><div class="l">今日采集</div></div>
    </div>
    <div class="filters">
      <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--dim);letter-spacing:1px">
        <input type="checkbox" v-model="auto" @change="toggleAuto(auto)"> ROUTER 自动建会话派单
      </label>
      <button @click="pollNow">立即采集一轮</button>
      <span class="dim" style="font-size:11px;margin-left:auto" v-if="o">入口 集市主站 · {{ o.ingest_interval_s }}s 轮询 · 水位 {{ o.watermarks.post }}/{{ o.watermarks.comment }} · LLM {{ o.llm.model }}</span>
    </div>
    <MeshGraph :topo="topo" />
    <div class="hr"></div>
    <h2><b>节点</b> · 心跳自动检测在线</h2>
    <div class="node-block" v-for="n in nodes" :key="n.id">
      <div class="node-head">
        <span class="name">{{ n.name || n.id }}</span>
        <span class="tag" :class="n.online ? 'online' : 'offline'">{{ n.online ? '在线' : '离线' }}</span>
        <button @click="router.push('/node/' + n.id)">详情/命令</button>
        <button v-if="session.me.is_admin" @click="router.push('/admin')">+ 建 worker</button>
      </div>
      <div class="node-meta">
        <span>{{ n.id }}</span><span>{{ n.host }}{{ n.pod ? ' / ' + n.pod : '' }}</span>
        <span>平台 {{ n.platforms.map(p => PLATFORM_TXT[p] || p).join(',') || '通用' }}</span>
        <span>会话 {{ n.workers.filter(w => w.status !== 'offline').length }}/{{ n.max_workers }}</span>
        <span>心跳 {{ fmtT(n.last_seen) }}</span>
        <span v-if="n.peer_url">peer {{ n.peer_url }}</span>
      </div>
      <div class="worker-line" v-for="w in n.workers" :key="w.id" @click="router.push('/worker/' + w.id)">
        <span class="mono">{{ w.id }}</span>
        <span class="tag" :class="w.status === 'busy' ? 'busy' : w.status === 'idle' ? 'idle' : w.status === 'creating' ? 'creating' : 'offline'">{{ WSTATUS_TXT[w.status] || w.status }}</span>
        <span class="dim" style="font-size:12px">{{ w.purpose || w.platform || '通用' }}</span>
        <span v-if="w.current_ticket" class="mono" style="color:var(--amber)">单#{{ w.current_ticket }}</span>
      </div>
      <div v-if="!n.workers.length" class="dim" style="font-size:12px;padding:4px 22px">无 worker 会话</div>
    </div>
    <div class="empty" v-if="!nodes.length">
      <div class="big">尚无节点接入</div>
      <p>目标机器/pod 部署 node agent 包，反向拨入即自动上线：</p>
      <pre class="code">curl -O https://yanzhen.ssemarket.cn/dl/yanzhen-worker-latest.tar.gz
curl -O https://yanzhen.ssemarket.cn/dl/yanzhen-kernel-cc-latest.tar.gz
tar xzf yanzhen-worker-latest.tar.gz && tar xzf yanzhen-kernel-cc-latest.tar.gz
cd yanzhen-worker && cp worker.env.example worker.env   # 填 TOKEN/NODE_ID/PLATFORMS/REPO_MAP
./start.sh   # 自动上线；worker 会话由管理员/router 在其上创建</pre>
    </div>
    <h2><b>派发记录</b></h2>
    <div class="rows">
      <div class="row" style="cursor:default" v-for="x in dispatches" :key="x.id">
        <span class="t dim mono nowrap">{{ fmtT(x.at) }}</span>
        <span class="mono nowrap">{{ x.code }}</span>
        <span class="mono dim nowrap">{{ x.worker_id || '' }}</span>
        <span class="tag" :class="x.action === 'result' ? 'review' : x.action === 'dispatch' ? 'assigned' : ['lease_expired', 'nack'].includes(x.action) ? 'bug' : 'noise'">{{ x.action }}</span>
        <span class="grow dim" style="font-size:12px">{{ summarizePayload(x).slice(0, 110) }}</span>
      </div>
      <div v-if="!dispatches.length" class="dim" style="padding:8px 6px">暂无派发记录</div>
    </div>
  </section>
</template>

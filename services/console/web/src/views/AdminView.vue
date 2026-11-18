<script setup>
import { ref, onMounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { api, fmtT, PLATFORM_TXT, session } from '../api.js';
import { live, toast } from '../live.js';
const router = useRouter();
const nodes = ref([]); const routers = ref([]);
const aw = ref({ node: '', platform: 'market', name: '', repo: '', purpose: '' });
const ar = ref({ id: '', name: '', observes: '' });
const launch = ref('');
async function load() {
  nodes.value = await api('/api/nodes');
  routers.value = await api('/api/routers');
  if (!aw.value.node && nodes.value.length) aw.value.node = nodes.value[0].id;
}
onMounted(load);
watch(() => live.seq, load);
async function createWorker() {
  try {
    const r = await api(`/api/nodes/${aw.value.node}/workers`, { method: 'POST', body: JSON.stringify(aw.value) });
    toast('worker 创建中：' + r.id); aw.value.purpose = ''; aw.value.name = '';
  } catch (e) { toast(e.message); }
}
async function createRouter() {
  try {
    const r = await api('/api/routers', { method: 'POST', body: JSON.stringify({ id: ar.value.id, name: ar.value.name, observes: ar.value.observes.split(',').map(s => s.trim()).filter(Boolean) }) });
    toast('router 已预注册：' + r.id);
    launch.value = '# 观测域机器上启动 router 节点：\n' + r.launch;
    load();
  } catch (e) { toast(e.message); }
}
</script>
<template>
  <section class="view active" v-if="session.me.is_admin">
    <h2><b>建 worker</b> · 在节点上初始化一个 Claude 会话</h2>
    <div class="form-grid">
      <label>节点<select v-model="aw.node"><option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.name || n.id }}{{ n.online ? '' : '（离线）' }}</option></select></label>
      <label>平台<select v-model="aw.platform"><option v-for="(label, p) in PLATFORM_TXT" :key="p" :value="p">{{ label }}</option></select></label>
      <label>名称<input v-model="aw.name" placeholder="可选"></label>
      <label>仓库路径<input v-model="aw.repo" placeholder="留空用节点映射"></label>
      <label style="grid-column:1/-1">使命（这个 worker 是干啥的）<input v-model="aw.purpose" placeholder="如：修复 YZ-0003 集市长评论 bug / iwiki 平台缺陷值守"></label>
    </div>
    <button class="primary" @click="createWorker">创建会话</button>
    <div class="hr"></div>
    <h2><b>预注册 router</b> · 一个独立可观测域一个 router</h2>
    <div class="form-grid">
      <label>router id<input v-model="ar.id" placeholder="router-iwiki-1"></label>
      <label>名称<input v-model="ar.name" placeholder="可选"></label>
      <label>观测域（逗号分隔）<input v-model="ar.observes" placeholder="iwiki,sso"></label>
    </div>
    <button class="primary" @click="createRouter">注册 router</button>
    <pre class="code" v-if="launch">{{ launch }}</pre>
    <div class="hr"></div>
    <h2><b>router 名册</b></h2>
    <div class="rows">
      <div class="row" v-for="r in routers" :key="r.id" @click="router.push('/router/' + r.id)">
        <span class="mono" style="color:var(--violet)">{{ r.id }}</span>
        <span class="tag" :class="r.online ? 'online' : 'offline'">{{ r.online ? '在线' : '离线' }}</span>
        <span class="grow dim">{{ (r.observes || []).map(p => PLATFORM_TXT[p] || p).join(', ') || '—' }}</span>
        <span class="dim mono">{{ fmtT(r.last_seen) }}</span>
      </div>
    </div>
  </section>
  <section class="view active" v-else><div class="empty"><div class="big">仅管理员</div>建 worker / router 需要管理员身份。</div></section>
</template>

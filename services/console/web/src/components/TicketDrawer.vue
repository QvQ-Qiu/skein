<script setup>
// 需求单详情：判定依据/原帖/多路worker/协作板/交付/流转 + 管理员多选派发 + 验收
import { ref, watch, onBeforeUnmount, computed } from 'vue';
import { useRouter } from 'vue-router';
import { api, fmtT, KIND_TXT, STATUS_TXT, PLATFORM_TXT, WSTATUS_TXT, summarizePayload, session } from '../api.js';
import { live, toast } from '../live.js';

const props = defineProps({ id: Number });
const emit = defineEmits(['close']);
const router = useRouter();
const t = ref(null);
const workers = ref([]);
const sel = ref([]);
const role = ref('');
const note = ref('');
let poll = null;

async function load() {
  try {
    t.value = await api('/api/tickets/' + props.id);
    workers.value = await api('/api/workers');
  } catch { t.value = null; }
}
watch(() => props.id, load, { immediate: true });
watch(() => live.seq, () => { if (t.value) load(); });
poll = setInterval(load, 6000);
onBeforeUnmount(() => clearInterval(poll));

const idle = computed(() => workers.value.filter(w => w.status === 'idle'));
const verdict = computed(() => t.value?.event?.verdict ? JSON.parse(t.value.event.verdict) : null);
async function dispatch() {
  if (!sel.value.length) return toast('先选 worker');
  const r = await api(`/api/tickets/${props.id}/dispatch`, { method: 'POST', body: JSON.stringify({ worker_ids: sel.value, role: role.value }) });
  toast(`派发 ${r.ok.length} 路${r.fail.length ? '，失败 ' + r.fail.length : ''}`);
  sel.value = []; load();
}
async function cancel() { await api(`/api/tickets/${props.id}/cancel`, { method: 'POST', body: '{}' }); toast('已回收'); load(); }
async function reject() { await api(`/api/tickets/${props.id}/reject`, { method: 'POST', body: '{}' }); toast('已驳回'); emit('close'); }
async function review(accept) { await api(`/api/tickets/${props.id}/review`, { method: 'POST', body: JSON.stringify({ accept, note: note.value }) }); toast(accept ? '验收通过' : '已驳回'); emit('close'); }
</script>

<template>
  <div class="drawer-mask open" @click="emit('close')"></div>
  <div class="drawer open">
    <button class="close" @click="emit('close')">✕</button>
    <template v-if="t">
      <h3>{{ t.code }} · {{ t.title }}</h3>
      <div>
        <span class="tag" :class="t.kind">{{ KIND_TXT[t.kind] || t.kind }}</span>
        <span class="tag platform">{{ PLATFORM_TXT[t.platform] || t.platform }}</span>
        <span class="tag" :class="t.severity">{{ t.severity }}</span>
        <span class="tag" :class="t.status">{{ STATUS_TXT[t.status] || t.status }}</span>
      </div>
      <div class="kv">
        <span class="k">摘要</span><span>{{ t.summary }}</span>
        <span class="k">置信</span><span class="mono">{{ t.confidence ?? '-' }}</span>
        <span class="k">来源</span><span><a v-if="t.source_url" :href="t.source_url" target="_blank">{{ t.source_url }}↗</a><template v-else>—</template></span>
        <span class="k">创建</span><span class="mono">{{ fmtT(t.created_at) }}</span>
      </div>
      <div class="block"><div class="label">头雁判定依据</div><div class="body">{{ t.reason || '—' }}</div></div>
      <div class="block" v-if="t.event">
        <div class="label">原始{{ t.event.channel === 'market-post' ? '帖子' : '评论' }} · {{ t.event.author_alias }} · {{ fmtT(t.event.source_time) }}</div>
        <div class="body">{{ t.event.title ? t.event.title + '\n' : '' }}{{ t.event.content }}</div>
      </div>
      <div class="block" v-if="verdict?.parent_content">
        <div class="label">所属帖子上下文</div>
        <div class="body">{{ verdict.parent_title }}{{ '\n' }}{{ verdict.parent_content }}</div>
      </div>
      <div class="block">
        <div class="label">协作 worker（{{ t.workers.length }} 路）</div>
        <div class="feed-item" v-for="w in t.workers" :key="w.worker_id">
          <a href="#" @click.prevent="router.push('/worker/' + w.worker_id)">{{ w.worker_id }}</a>
          <span class="tag" :class="w.state === 'delivered' ? 'done' : w.state === 'running' ? 'running' : w.state === 'assigned' ? 'assigned' : 'noise'">{{ w.state }}</span>
          <span class="dim">{{ w.role || w.purpose || '' }}</span>
          <span v-if="w.session_id" class="dim mono">cc {{ w.session_id.slice(0, 8) }}</span>
        </div>
        <span v-if="!t.workers.length" class="dim">暂存中，未挂 worker</span>
      </div>
      <div class="block" v-if="t.board?.length">
        <div class="label">跨部门协作板</div>
        <div class="feed-item" v-for="m in t.board" :key="m.id"><span class="t">{{ fmtT(m.at) }}</span><span class="tag platform">{{ m.router_id }}</span><span>{{ m.body }}</span></div>
      </div>
      <div class="block" v-if="t.result"><div class="label">交付</div><pre class="code">{{ JSON.stringify(t.result, null, 2) }}</pre></div>
      <div class="block">
        <div class="label">流转记录</div>
        <div class="timeline">
          <div class="step" v-for="x in t.dispatches" :key="x.id"><span class="t">{{ fmtT(x.at) }}</span><span><b>{{ x.action }}</b><template v-if="x.worker_id"> · {{ x.worker_id }}</template> · {{ summarizePayload(x).slice(0, 140) }}</span></div>
          <span v-if="!t.dispatches.length" class="dim">暂无</span>
        </div>
      </div>
      <div class="actions" style="flex-direction:column;align-items:stretch">
        <template v-if="['staged', 'assigned', 'running'].includes(t.status) && session.me.is_admin">
          <div class="label dim" style="font-size:11px;letter-spacing:2px">挂 worker（可多选协作）</div>
          <label v-for="w in idle" :key="w.id" style="display:flex;gap:8px;align-items:baseline;font-size:13px;padding:3px 0">
            <input type="checkbox" :value="w.id" v-model="sel">
            <span class="mono">{{ w.id }}</span><span class="dim">{{ w.purpose || w.platform || '' }}</span>
          </label>
          <span v-if="!idle.length" class="dim">无空闲会话 —— 去集群/管理页建 worker</span>
          <input v-model="role" placeholder="角色/使命（可选，如 sso 侧排查）" style="width:100%;margin:6px 0">
          <div style="display:flex;gap:10px">
            <button class="primary" :disabled="!idle.length" @click="dispatch">派发选中</button>
            <button v-if="['assigned', 'running'].includes(t.status)" class="danger" @click="cancel">取消在途</button>
            <button class="danger" @click="reject">驳回</button>
          </div>
        </template>
        <span v-else-if="['staged', 'assigned', 'running'].includes(t.status)" class="dim" style="font-size:12px">派发/取消仅管理员；验收所有登录用户可操作</span>
        <template v-if="t.status === 'review'">
          <input v-model="note" placeholder="验收备注（可选）" style="width:100%">
          <div style="display:flex;gap:10px"><button class="primary" @click="review(true)">验收通过</button><button class="danger" @click="review(false)">驳回</button></div>
        </template>
      </div>
    </template>
    <div v-else class="dim" style="padding:30px 0">加载中…</div>
  </div>
</template>

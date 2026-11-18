<script setup>
import { ref, onMounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { api, fmtT, session } from '../api.js';
import { live, toast } from '../live.js';
const props = defineProps({ id: String });
const router = useRouter();
const rooms = ref([]); const board = ref([]); const msgs = ref([]);
async function load() {
  rooms.value = await api('/api/rooms');
  board.value = (await api('/api/router-messages')).slice().reverse();
  if (props.id) msgs.value = await api('/api/rooms/' + props.id + '/messages');
}
onMounted(load);
watch(() => props.id, load);
watch(() => live.seq, load);
async function newRoom() {
  if (!session.me.is_admin) return toast('仅管理员可建 room');
  const name = prompt('room 名称（如：iWiki登录异常×SSO联动）');
  if (!name) return;
  const ws = await api('/api/workers');
  const r = await api('/api/rooms', { method: 'POST', body: JSON.stringify({ name, worker_ids: ws.filter(w => w.status !== 'offline').map(w => w.id) }) });
  toast('room 已建：' + r.id); router.push('/room/' + r.id);
}
</script>
<template>
  <section class="view active">
    <div class="filters"><button @click="newRoom">+ 建联动 room</button><span class="dim" style="font-size:11px">worker 之间经 node peer 口直连；controller 仅兜底</span></div>
    <div class="hr"></div>
    <h2><b>Rooms</b></h2>
    <div class="rows">
      <div class="row" v-for="r in rooms" :key="r.id" @click="router.push('/room/' + r.id)">
        <span class="mono" style="color:var(--green)">{{ r.id }}</span>
        <span class="grow title">{{ r.name }}</span>
        <span class="dim" style="font-size:12px">{{ r.members.join(', ') || '空' }}</span>
        <span class="dim mono">{{ fmtT(r.created_at) }}</span>
      </div>
      <div v-if="!rooms.length" class="dim" style="padding:8px 6px">暂无 room</div>
    </div>
    <div class="feed" style="margin-top:8px" v-if="props.id">
      <div class="feed-item" v-for="m in msgs" :key="m.id"><span class="t">{{ fmtT(m.at) }}</span><span class="tag idle">{{ m.worker_id }}</span><span>{{ m.body }}</span></div>
      <span v-if="!msgs.length" class="dim">暂无消息</span>
    </div>
    <div class="hr"></div>
    <h2><b>跨部门协作板</b> · router ↔ router</h2>
    <div class="feed">
      <div class="feed-item" v-for="m in board" :key="m.id">
        <span class="t">{{ fmtT(m.at) }}</span><span class="tag platform">{{ m.router_id }}</span>
        <a v-if="m.ticket_id" href="#" @click.prevent="router.push('/tickets/' + m.ticket_id)">单#{{ m.ticket_id }}</a>
        <span>{{ m.body }}</span>
      </div>
      <span v-if="!board.length" class="dim">暂无跨部门喊话</span>
    </div>
  </section>
</template>

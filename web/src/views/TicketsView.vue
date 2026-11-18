<script setup>
import { ref, onMounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { api, fmtT, KIND_TXT, STATUS_TXT, PLATFORM_TXT } from '../api.js';
import { live } from '../live.js';
const router = useRouter();
const rows = ref([]); const filter = ref('all');
const FILTERS = [['all', '全部'], ['staged', '暂存'], ['assigned', '已派发'], ['running', '进行中'], ['review', '待验收'], ['done', '完成'], ['rejected', '已驳回']];
async function load() { rows.value = await api('/api/tickets?status=' + filter.value); }
onMounted(load);
watch(() => live.seq, load);
</script>
<template>
  <section class="view active">
    <div class="filters">
      <button v-for="[s, label] in FILTERS" :key="s" :class="{ on: filter === s }" @click="filter = s; load()">{{ label }}</button>
    </div>
    <div class="rows">
      <div class="row" v-for="t in rows" :key="t.id" @click="router.push('/tickets/' + t.id)">
        <span class="mono nowrap" style="color:var(--cyan)">{{ t.code }}</span>
        <span class="grow title">{{ t.title }}</span>
        <span class="tag" :class="t.kind">{{ KIND_TXT[t.kind] || t.kind }}</span>
        <span class="tag platform">{{ PLATFORM_TXT[t.platform] || t.platform }}</span>
        <span class="tag" :class="t.severity">{{ t.severity }}</span>
        <span class="tag" :class="t.status">{{ STATUS_TXT[t.status] || t.status }}</span>
        <a v-if="t.source_url" :href="t.source_url" target="_blank" @click.stop>原帖↗</a>
        <span class="dim mono nowrap">{{ fmtT(t.created_at) }}</span>
      </div>
    </div>
    <div class="empty" v-if="!rows.length"><div class="big">暂无需求单</div>router 监听集市主站，识别 bug/feature 自动建单暂存。</div>
  </section>
</template>

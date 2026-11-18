<script setup>
import { ref, onMounted, watch } from 'vue';
import { api, fmtT, KIND_TXT, PLATFORM_TXT } from '../api.js';
import { live } from '../live.js';
const rows = ref([]); const filter = ref('all');
const FILTERS = [['all', '全部'], ['req', '已建单'], ['noise', '噪音'], ['pending', '待分类']];
async function load() { rows.value = await api('/api/events?limit=150&filter=' + filter.value); }
onMounted(load);
watch(() => live.seq, load);
</script>
<template>
  <section class="view active">
    <div class="filters">
      <button v-for="[f, label] in FILTERS" :key="f" :class="{ on: filter === f }" @click="filter = f; load()">{{ label }}</button>
      <span class="dim" style="font-size:11px;margin-left:auto">{{ rows.length }} 条</span>
    </div>
    <div class="rows">
      <div class="row" style="cursor:default" v-for="e in rows" :key="e.id">
        <span class="mono dim">{{ e.id }}</span>
        <span class="nowrap dim" style="font-size:12px">{{ e.channel === 'market-post' ? '帖' : '评' }}#{{ e.source_id }}</span>
        <span class="grow" style="font-size:13px">{{ ((e.title ? e.title + ' — ' : '') + (e.content || '')).slice(0, 90) }}<template v-if="((e.title ? e.title + ' — ' : '') + (e.content || '')).length > 90">…</template> <a v-if="e.url" :href="e.url" target="_blank">↗</a></span>
        <span v-if="e.verdict" class="tag" :class="e.verdict.kind">{{ KIND_TXT[e.verdict.kind] || e.verdict.kind }}</span>
        <span v-else class="dim">待分类</span>
        <span v-if="e.verdict && e.verdict.kind !== 'noise'" class="tag platform">{{ PLATFORM_TXT[e.verdict.platform] || e.verdict.platform }}</span>
        <span class="mono dim">{{ e.verdict ? (e.verdict.confidence ?? '') : '' }}</span>
        <span class="dim nowrap" style="font-size:11px">{{ fmtT(e.source_time) }}</span>
      </div>
    </div>
  </section>
</template>

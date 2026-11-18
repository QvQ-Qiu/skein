<script setup>
import { ref, onMounted, watch } from 'vue';
import { api, fmtT } from '../api.js';
import { live } from '../live.js';
const rows = ref([]); const comp = ref('');
const COMPS = [['', '全部'], ['ingest', 'ingest'], ['router', 'router'], ['controller', 'controller'], ['router-board', '协作板'], ['llm', 'llm']];
async function load() { rows.value = await api('/api/logs?limit=300&component=' + comp.value); }
onMounted(load);
watch(() => live.seq, load);
</script>
<template>
  <section class="view active">
    <div class="filters">
      <button v-for="[c, label] in COMPS" :key="c" :class="{ on: comp === c }" @click="comp = c; load()">{{ label }}</button>
    </div>
    <div class="feed" style="max-height:640px;font-family:var(--mono);font-size:12px">
      <div class="feed-item" v-for="l in rows" :key="l.id">
        <span class="t">{{ fmtT(l.at) }}</span>
        <span :style="{ color: l.level === 'error' ? 'var(--red)' : l.level === 'warn' ? 'var(--amber)' : 'var(--dim)', fontSize: '11px' }">{{ l.component }}</span>
        <span>{{ l.message }}</span>
      </div>
      <span v-if="!rows.length" class="dim">暂无日志</span>
    </div>
  </section>
</template>

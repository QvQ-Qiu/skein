<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { fetchLogs, type LogRecord } from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";

const rows = ref<LogRecord[]>([]);
const level = ref("");
const component = ref("");
const loading = ref(false);
const error = ref<string | null>(null);
const lastAt = ref<number | null>(null);
let timer: number | undefined;
let alive = true;

function stampSeconds(ts: number): string {
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}:${pad(date.getSeconds())}`;
}

function stampMillis(ts: number): string {
  const date = new Date(ts);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}:${pad(date.getSeconds())}`;
}

async function load() {
  if (loading.value) return;
  loading.value = true;
  error.value = null;
  try {
    const body = await fetchLogs({
      limit: 500,
      level: level.value || undefined,
      component: component.value.trim() || undefined,
    });
    if (!alive) return;
    rows.value = body.logs;
    lastAt.value = Date.now();
  } catch (err) {
    if (alive) error.value = err instanceof Error ? err.message : "日志加载失败";
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void load();
  timer = window.setInterval(() => {
    void load();
  }, 5000);
});

onBeforeUnmount(() => {
  alive = false;
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <section class="logs">
    <header class="logs__head">
      <div>
        <h2>日志</h2>
        <p class="logs__sub">
          {{ rows.length }} 条
          <span v-if="lastAt">· 最近刷新 {{ stampMillis(lastAt) }}</span>
        </p>
      </div>
      <button type="button" class="ghost" :disabled="loading" @click="load">
        {{ loading ? "刷新中" : "刷新" }}
      </button>
    </header>

    <div class="filters">
      <label>
        <span>级别</span>
        <select v-model="level" @change="load">
          <option value="">全部</option>
          <option value="debug">debug</option>
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="error">error</option>
        </select>
      </label>
      <label class="filters__component">
        <span>组件</span>
        <input
          v-model="component"
          type="text"
          placeholder="如 skein.poll"
          spellcheck="false"
          @keyup.enter="load"
        />
      </label>
      <span class="filters__hint">进程内最近 2000 条，页面每 5 秒刷新</span>
    </div>

    <error-bar v-if="error" :message="error" @retry="load" />

    <div v-if="!rows.length && !loading" class="logs__empty">
      <empty-state title="暂无日志" description="控制面产生日志后会出现在这里" />
    </div>

    <div v-else class="feed">
      <div v-for="(row, index) in rows" :key="`${row.at}-${index}`" class="feed__row">
        <time class="feed__time mono">{{ stampSeconds(row.at) }}</time>
        <span class="feed__level" :class="`feed__level--${row.level}`">{{ row.level }}</span>
        <span class="feed__component mono">{{ row.component }}</span>
        <span class="feed__message">{{ row.message }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.logs {
  min-width: 0;
}

.logs__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.logs__head h2 {
  margin: 0;
  color: var(--text);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.12em;
}

.logs__sub {
  margin: 4px 0 0;
  color: var(--muted);
  font-family: var(--mono);
  font-size: 11px;
}

.filters {
  display: flex;
  align-items: flex-end;
  gap: 14px;
  flex-wrap: wrap;
  padding: 8px 0 10px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 8px;
}

.filters label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.12em;
}

.filters select,
.filters input {
  height: 26px;
  min-width: 110px;
  padding: 0 8px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
  border-radius: 0;
  color: var(--text);
  font-family: var(--mono);
  font-size: 12px;
}

.filters select:focus,
.filters input:focus {
  outline: none;
  border-color: var(--cyan);
}

.filters__component input {
  width: 220px;
}

.filters__hint {
  margin-left: auto;
  padding-bottom: 6px;
  color: var(--muted);
  font-size: 11px;
}

.ghost {
  padding: 4px 12px 6px;
  background: transparent;
  border: none;
  color: var(--muted);
  letter-spacing: 0.12em;
  cursor: pointer;
}

.ghost:hover:not(:disabled),
.ghost:focus-visible {
  color: var(--cyan);
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.7);
}

.ghost:disabled {
  opacity: 0.45;
  cursor: default;
}

.logs__empty {
  padding-top: 24px;
}

.feed {
  max-height: calc(100vh - 220px);
  overflow: auto;
}

.feed__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 7px 4px;
  border-bottom: 1px solid rgba(125, 137, 184, 0.1);
  font-size: 12px;
}

.feed__row:hover {
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.06), transparent 70%);
}

.feed__time {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: 11px;
}

.feed__level {
  flex: 0 0 62px;
  font-family: var(--mono);
  font-size: 11px;
}

.feed__level--info {
  color: var(--cyan);
}

.feed__level--warning {
  color: var(--amber);
}

.feed__level--error {
  color: var(--bad);
}

.feed__level--debug {
  color: var(--muted);
}

.feed__component {
  flex: 0 0 170px;
  overflow: hidden;
  color: var(--dim);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.feed__message {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--text);
}

.mono {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
}
</style>

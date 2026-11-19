<script setup lang="ts">
// 感知层：k3s Pod 清单与日志（经控制面 /ops 代理，SSO 内部身份）。
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { fetchPodLogs, fetchPods, type K8sPod } from "../api";
import ErrorBar from "../components/ErrorBar.vue";

const pods = ref<K8sPod[]>([]);
const reason = ref("");
const error = ref<string | null>(null);
const loading = ref(true);
const filter = ref("");
const selected = ref<K8sPod | null>(null);
const logs = ref("");
const logsError = ref<string | null>(null);
const logsLoading = ref(false);
let timer: number | undefined;

const visible = computed(() => {
  const text = filter.value.trim().toLowerCase();
  const list = pods.value.filter(
    (pod) =>
      !text ||
      pod.name.toLowerCase().includes(text) ||
      pod.ns.toLowerCase().includes(text) ||
      pod.node.toLowerCase().includes(text),
  );
  return list.slice(0, 200);
});

const counts = computed(() => {
  const map: Record<string, number> = {};
  for (const pod of pods.value) map[pod.phase] = (map[pod.phase] ?? 0) + 1;
  return map;
});

function age(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

async function load() {
  loading.value = true;
  try {
    const data = await fetchPods();
    pods.value = data.pods ?? [];
    reason.value = data.ok ? "" : data.reason || "k8s 不可用";
    error.value = null;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

async function openLogs(pod: K8sPod) {
  selected.value = pod;
  logs.value = "";
  logsError.value = null;
  logsLoading.value = true;
  try {
    const data = await fetchPodLogs(pod.ns, pod.name, 300);
    logs.value = data.log || "";
    if (data.error) logsError.value = data.error;
  } catch (err) {
    logsError.value = err instanceof Error ? err.message : "日志加载失败";
  } finally {
    logsLoading.value = false;
  }
}

onMounted(() => {
  void load();
  timer = window.setInterval(() => void load(), 20_000);
});

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <section class="page">
    <header class="page__head">
      <h2 class="page__title">Pod</h2>
      <div class="meta">
        <span v-for="(count, phase) in counts" :key="phase" class="muted mono">
          {{ phase }} {{ count }}
        </span>
        <input v-model="filter" class="filter" placeholder="按名称 / 命名空间 / 节点过滤" />
        <button type="button" class="ghost" :disabled="loading" @click="load">
          {{ loading ? "刷新中" : "刷新" }}
        </button>
      </div>
    </header>
    <ErrorBar v-if="error" :message="error" @retry="load()" />
    <p v-if="reason" class="muted hint">{{ reason }}</p>

    <div class="layout">
      <div class="list">
        <div class="row row--head">
          <span>命名空间 / 名称</span>
          <span>状态</span>
          <span>就绪</span>
          <span>重启</span>
          <span>节点</span>
          <span>年龄</span>
        </div>
        <button
          v-for="pod in visible"
          :key="`${pod.ns}/${pod.name}`"
          type="button"
          class="row row--pod"
          :class="{ active: selected?.name === pod.name && selected?.ns === pod.ns }"
          @click="openLogs(pod)"
        >
          <span class="mono name">
            <span class="muted">{{ pod.ns }}/</span>{{ pod.name }}
          </span>
          <span class="phase" :class="`phase--${pod.phase.toLowerCase()}`">{{ pod.phase }}</span>
          <span class="mono">{{ pod.ready }}</span>
          <span class="mono" :class="{ warn: pod.restarts > 0 }">{{ pod.restarts }}</span>
          <span class="mono muted">{{ pod.node }}</span>
          <span class="mono muted">{{ age(pod.age_s) }}</span>
        </button>
        <p v-if="!visible.length" class="muted empty">没有匹配的 Pod</p>
      </div>

      <aside class="logs">
        <header class="logs__head">
          <span class="mono">{{ selected ? `${selected.ns}/${selected.name}` : "选择左侧 Pod 查看日志" }}</span>
          <button
            v-if="selected"
            type="button"
            class="ghost"
            :disabled="logsLoading"
            @click="openLogs(selected)"
          >
            {{ logsLoading ? "读取中" : "重读" }}
          </button>
        </header>
        <p v-if="logsError" class="muted hint">{{ logsError }}</p>
        <pre v-if="logs" class="logs__body">{{ logs }}</pre>
        <p v-else-if="selected && !logsLoading && !logsError" class="muted hint">没有日志</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.page {
  min-width: 0;
}

.page__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.page__title {
  margin: 0;
  color: var(--text);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.12em;
}

.meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  flex-wrap: wrap;
}

.filter {
  width: 220px;
  padding: 3px 8px;
  background: transparent;
  border: 1px solid var(--line);
  color: var(--text);
  font-size: 12px;
}

.filter:focus {
  outline: none;
  border-color: var(--cyan);
}

.ghost {
  padding: 4px 12px 6px;
  background: transparent;
  border: none;
  color: var(--muted);
  letter-spacing: 0.12em;
  cursor: pointer;
}

.ghost:hover:not(:disabled) {
  color: var(--cyan);
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 12px;
}

@media (max-width: 1100px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

.list {
  min-width: 0;
  border: 1px solid var(--line);
  background: var(--bg-elevated);
  overflow: auto;
}

.row {
  display: grid;
  grid-template-columns: minmax(0, 2.2fr) 0.8fr 0.6fr 0.5fr 1fr 0.5fr;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  border-bottom: 1px solid rgba(125, 137, 184, 0.12);
  background: transparent;
  color: var(--text);
  font-size: 12px;
  text-align: left;
}

.row--head {
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.08em;
  border-bottom-color: var(--line);
}

.row--pod {
  cursor: pointer;
}

.row--pod:hover,
.row--pod.active {
  background: rgba(79, 216, 235, 0.07);
}

.name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.phase {
  font-size: 11px;
}

.phase--running {
  color: var(--ok);
}

.phase--pending {
  color: var(--amber);
}

.phase--failed {
  color: var(--bad);
}

.phase--succeeded {
  color: var(--muted);
}

.warn {
  color: var(--amber);
}

.logs {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 320px;
  border: 1px solid var(--line);
  background: var(--bg-elevated);
}

.logs__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  font-size: 12px;
}

.logs__body {
  flex: 1 1 auto;
  margin: 0;
  padding: 10px;
  overflow: auto;
  max-height: 60vh;
  color: var(--text);
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.hint {
  margin: 10px;
  font-size: 12px;
}

.empty {
  margin: 16px 10px;
  font-size: 12px;
}

.mono {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
}
</style>

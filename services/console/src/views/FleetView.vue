<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { fetchSummary, fetchTopology, type Summary, type Topology } from "../api";
import ErrorBar from "../components/ErrorBar.vue";
import TopologyBoard from "../components/TopologyBoard.vue";

const POLL_MS = 10000;

const summary = ref<Summary | null>(null);
const topology = ref<Topology | null>(null);
const error = ref<string | null>(null);
const loading = ref(false);

let alive = true;
let seq = 0;
let timer = 0;

const shadowCount = computed(() => sumValues(summary.value?.shadow.by_action));

const byStatus = computed<Record<string, number>>(
  () => summary.value?.tickets.by_status ?? {},
);
const reviewCount = computed(() => byStatus.value.review ?? 0);
const activeCount = computed(() =>
  ["staged", "assigned", "running"].reduce((total, key) => total + (byStatus.value[key] ?? 0), 0),
);
const finishedCount = computed(() =>
  ["done", "rejected"].reduce((total, key) => total + (byStatus.value[key] ?? 0), 0),
);


function sumValues(record: Record<string, number> | undefined): number {
  if (!record) return 0;
  return Object.values(record).reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
}

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "网络错误";
}

async function load() {
  const token = ++seq;
  const initial = summary.value == null && topology.value == null;
  if (initial) loading.value = true;
  try {
    const [summaryResult, topologyResult] = await Promise.allSettled([
      fetchSummary(),
      fetchTopology(),
    ]);
    if (!alive || token !== seq) return;
    let nextError: string | null = null;
    if (summaryResult.status === "fulfilled") summary.value = summaryResult.value;
    else nextError = errorMessage(summaryResult.reason);
    if (topologyResult.status === "fulfilled") topology.value = topologyResult.value;
    else nextError = nextError ?? errorMessage(topologyResult.reason);
    error.value = nextError;
  } finally {
    if (alive && token === seq) loading.value = false;
  }
}

onMounted(() => {
  void load();
  timer = window.setInterval(() => {
    void load();
  }, POLL_MS);
});

onUnmounted(() => {
  alive = false;
  window.clearInterval(timer);
});
</script>

<template>
  <section class="page">
    <h2 class="page__title">总览</h2>
    <ErrorBar v-if="error" :message="error" @retry="load()" />
    <p v-if="loading && !summary && !topology" class="muted load">加载中</p>

    <div v-if="summary" class="metrics">
      <div class="metric metric--attention">
        <span class="metric__label">待你验收</span>
        <span class="metric__value mono">{{ reviewCount }}</span>
        <span class="metric__hint">需要人做决定</span>
      </div>
      <div class="metric">
        <span class="metric__label">进行中</span>
        <span class="metric__value mono">{{ activeCount }}</span>
        <span class="metric__hint">暂存 / 已派 / 执行中</span>
      </div>
      <div class="metric">
        <span class="metric__label">已结束</span>
        <span class="metric__value mono">{{ finishedCount }}</span>
        <span class="metric__hint">完成或驳回</span>
      </div>
      <div class="metrics__aside">
        <span>事件 <b class="mono">{{ summary.events.total }}</b></span>
        <span>会话 <b class="mono">{{ summary.sessions }}</b></span>
        <span>影子 <b class="mono">{{ shadowCount }}</b></span>
      </div>
    </div>

    <TopologyBoard
      v-if="topology"
      :nodes="topology.nodes"
      :tickets="topology.tickets"
      :dispatches="topology.dispatches"
      :sessions="topology.sessions"
      :subscriptions="topology.subscriptions"
    />
  </section>
</template>

<style scoped>
.page {
  min-width: 0;
  max-width: 100%;
  overflow-x: clip;
}

.load {
  margin: 0 0 10px;
}

.metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
  margin: 0 0 12px;
}

.metric {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 10px;
  min-width: 0;
  max-width: 100%;
  padding: 6px 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
}

.metric__label {
  color: var(--muted);
}

.metric__value {
  font-size: 16px;
  font-weight: 600;
}

.metric__part {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.metric {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 132px;
  padding: 10px 14px;
}

.metric__value {
  font-size: 26px;
  line-height: 1.1;
}

.metric__hint {
  color: var(--muted);
  font-size: 11px;
}

.metric--attention .metric__value {
  color: var(--amber);
  text-shadow: 0 0 14px rgba(210, 153, 34, 0.45);
}

.metrics__aside {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
  color: var(--muted);
  font-size: 12px;
}

.metrics__aside b {
  margin-left: 4px;
  color: var(--text);
  font-weight: 600;
}

.dot {
  display: inline-block;
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
</style>

<script setup lang="ts">
// 感知层：主机水位 + 服务状态 + 容器占用（数据走小秋 ops 面，控制面做权限门）。
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  fetchHostSeries,
  fetchHostWorkloads,
  type MetricSeriesResp,
  type WorkloadsResp,
} from "../api";
import ErrorBar from "../components/ErrorBar.vue";

const seriesData = ref<MetricSeriesResp | null>(null);
const workloads = ref<WorkloadsResp | null>(null);
const error = ref<string | null>(null);
const loading = ref(true);
let timer: number | undefined;

const host = computed(() => workloads.value?.host ?? null);
const services = computed(() => workloads.value?.services?.items ?? []);
const containers = computed(() =>
  [...(host.value?.containers ?? [])].sort((a, b) => b.cpu_pct - a.cpu_pct).slice(0, 6),
);

const tiles = computed(() => [
  {
    key: "cpu",
    label: "CPU 使用",
    value: host.value?.cpu?.used_pct,
    suffix: "%",
    bad: (host.value?.cpu?.used_pct ?? 0) >= 85,
    series: pointsOf("host_cpu_used_pct"),
  },
  {
    key: "mem",
    label: "可用内存",
    value: host.value?.mem?.available_pct,
    suffix: "%",
    bad: (host.value?.mem?.available_pct ?? 100) <= 12,
    series: pointsOf("host_mem_available_pct"),
  },
  {
    key: "disk",
    label: "磁盘使用",
    value: host.value?.disk?.used_pct,
    suffix: "%",
    bad: (host.value?.disk?.used_pct ?? 0) >= 88,
    series: pointsOf("host_disk_used_pct"),
  },
  {
    key: "load",
    label: "负载 load1",
    value: host.value?.load?.load1,
    suffix: "",
    bad: (host.value?.load?.load1 ?? 0) >= 4,
    series: pointsOf("host_load1"),
  },
]);

function pointsOf(name: string): Array<[number, number]> {
  return seriesData.value?.series.find((item) => item.name === name)?.points ?? [];
}

function spark(points: Array<[number, number]>): string {
  if (points.length < 2) return "";
  const values = points.map((point) => point[1]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 120;
  const height = 28;
  return points
    .map((point, index) => {
      const x = (index / (points.length - 1)) * width;
      const y = height - ((point[1] - min) / span) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function fmt(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return value >= 100 ? value.toFixed(0) : value.toFixed(1);
}

function ago(ts: string | undefined | null): string {
  if (!ts) return "";
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  return `${Math.floor(diff / 3600)} 小时前`;
}

async function load() {
  loading.value = true;
  const [seriesResult, workloadsResult] = await Promise.allSettled([
    fetchHostSeries(360),
    fetchHostWorkloads(),
  ]);
  error.value = null;
  if (seriesResult.status === "fulfilled") seriesData.value = seriesResult.value;
  else error.value = seriesResult.reason instanceof Error ? seriesResult.reason.message : "时序加载失败";
  if (workloadsResult.status === "fulfilled") workloads.value = workloadsResult.value;
  else error.value = error.value ?? (workloadsResult.reason instanceof Error ? workloadsResult.reason.message : "主机状态加载失败");
  loading.value = false;
}

onMounted(() => {
  void load();
  timer = window.setInterval(() => void load(), 30_000);
});

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <section class="page">
    <header class="page__head">
      <h2 class="page__title">主机</h2>
      <button type="button" class="ghost" :disabled="loading" @click="load">
        {{ loading ? "刷新中" : "刷新" }}
      </button>
    </header>
    <ErrorBar v-if="error" :message="error" @retry="load()" />
    <p v-if="loading && !workloads" class="muted">加载中</p>

    <div class="tiles">
      <article v-for="tile in tiles" :key="tile.key" class="tile" :class="{ bad: tile.bad }">
        <span class="tile__label">{{ tile.label }}</span>
        <b class="tile__value mono">{{ fmt(tile.value) }}{{ tile.suffix }}</b>
        <svg class="tile__spark" viewBox="0 0 120 28" preserveAspectRatio="none" aria-hidden="true">
          <path :d="spark(tile.series)" />
        </svg>
      </article>
    </div>

    <div class="lanes">
      <article class="panel">
        <header class="panel__head">
          <h3>服务状态</h3>
          <span class="muted mono">{{ services.length }}</span>
          <span v-if="workloads?.services?.age_s != null" class="muted stamp">
            {{ Math.round(workloads.services.age_s) }}s 前上报
          </span>
        </header>
        <div v-if="services.length" class="rows">
          <div v-for="service in services" :key="service.name" class="row">
            <span class="dot" :class="{ bad: service.stale || service.status !== 'ok' }" aria-hidden="true"></span>
            <span class="name">{{ service.name }}</span>
            <span class="muted detail">{{ service.detail }}</span>
            <span class="muted stamp">{{ ago(service.reported_at) }}</span>
          </div>
        </div>
        <p v-else class="muted empty">没有上报服务状态</p>
      </article>

      <article class="panel">
        <header class="panel__head">
          <h3>容器占用 Top</h3>
          <span class="muted mono">{{ containers.length }}</span>
        </header>
        <div v-if="containers.length" class="rows">
          <div v-for="container in containers" :key="container.container" class="row">
            <span class="name">{{ container.container }}</span>
            <span class="muted mono cpu">{{ fmt(container.cpu_pct) }}% CPU</span>
            <span class="muted mono">{{ fmt(container.mem_pct) }}% MEM</span>
            <span class="muted stamp">{{ Math.round(container.mem_mb) }} MB</span>
          </div>
        </div>
        <p v-else class="muted empty">没有容器数据</p>
      </article>
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
}

.page__title {
  margin: 0;
  color: var(--text);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.12em;
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

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.tile {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 14px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
  border-top: 2px solid var(--cyan);
}

.tile.bad {
  border-top-color: var(--bad);
}

.tile__label {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.12em;
}

.tile__value {
  color: var(--text);
  font-size: 26px;
  font-weight: 700;
  line-height: 1.1;
}

.tile.bad .tile__value {
  color: var(--bad);
}

.tile__spark {
  width: 100%;
  height: 28px;
  margin-top: 4px;
}

.tile__spark path {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.4;
  opacity: 0.65;
}

.tile {
  color: var(--cyan);
}

.tile.bad {
  color: var(--bad);
}

.tile__value,
.tile__label {
  color: inherit;
}

.tile__label {
  color: var(--muted);
}

.lanes {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: 12px;
}

@media (max-width: 900px) {
  .lanes {
    grid-template-columns: minmax(0, 1fr);
  }
}

.panel {
  min-width: 0;
  padding: 12px 14px 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
}

.panel__head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.panel__head h3 {
  margin: 0;
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.14em;
}

.stamp {
  margin-left: auto;
  font-size: 10px;
}

.rows {
  display: flex;
  flex-direction: column;
  margin-top: 6px;
}

.row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(125, 137, 184, 0.12);
  font-size: 12px;
}

.name {
  color: var(--text);
  font-family: var(--mono);
}

.detail {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cpu {
  margin-left: auto;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 6px var(--ok);
}

.dot.bad {
  background: var(--bad);
  box-shadow: 0 0 6px var(--bad);
}

.empty {
  margin: 12px 0 4px;
  font-size: 12px;
}

.mono {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
}
</style>

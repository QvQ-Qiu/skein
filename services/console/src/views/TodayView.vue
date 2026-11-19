<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  fetchSignals,
  fetchTickets,
  fetchTopology,
  request,
  type Signal,
  type Ticket,
  type Topology,
} from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";

const review = ref<Ticket[]>([]);
const signals = ref<Signal[]>([]);
const topology = ref<Topology | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const acting = ref<string | null>(null);
let alive = true;
let timer: number | undefined;

const activeDispatches = computed(
  () =>
    topology.value?.dispatches.filter((item) =>
      ["dispatched", "created", "pending"].includes(item.state),
    ).length ?? 0,
);
const sessionCount = computed(() => topology.value?.sessions.length ?? 0);
const stalled = computed(() =>
  signals.value.filter((item) =>
    ["dispatch_timeout", "node_offline_with_work"].includes(item.kind),
  ),
);
const failures = computed(() =>
  signals.value.filter((item) =>
    ["run_failed", "effect_failed"].includes(item.kind),
  ),
);
const topSignals = computed(() => stalled.value.concat(failures.value).slice(0, 4));

function signalText(signal: Signal): string {
  switch (signal.kind) {
    case "dispatch_timeout":
      return `派单超时：工单 ${signal.ticket_id} 在节点 ${signal.node_id} 已等待 ${Math.round((signal.age_s ?? 0) / 60)} 分钟`;
    case "run_failed":
      return `作业失败：${signal.job} ${signal.day}`;
    case "effect_failed":
      return `写回失败：${signal.effect_kind} ${signal.object}`;
    case "node_offline_with_work":
      return `节点离线且有在途工单：${signal.node_id} / 工单 ${signal.ticket_id}`;
    default:
      return signal.kind;
  }
}

async function load() {
  loading.value = true;
  error.value = null;
  const [tickets, signalList, topo] = await Promise.allSettled([
    fetchTickets({ status: "review", limit: 20 }),
    fetchSignals(),
    fetchTopology(),
  ]);
  if (!alive) return;
  if (tickets.status === "fulfilled") review.value = tickets.value.tickets;
  if (signalList.status === "fulfilled") signals.value = signalList.value.signals;
  if (topo.status === "fulfilled") topology.value = topo.value;
  const failed = [tickets, signalList, topo].filter((item) => item.status === "rejected");
  error.value = failed.length
    ? failed
        .map((item) =>
          item.status === "rejected" && item.reason instanceof Error
            ? item.reason.message
            : "加载失败",
        )
        .join("；")
    : null;
  loading.value = false;
}

async function decide(ticket: Ticket, decision: "approve" | "reject") {
  if (acting.value) return;
  if (decision === "reject" && !window.confirm(`确认驳回 ${ticket.code}？状态将变为已驳回。`)) {
    return;
  }
  acting.value = ticket.id;
  try {
    await request(`/internal/tickets/${encodeURIComponent(ticket.id)}/review`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "操作失败";
  } finally {
    acting.value = null;
  }
}

onMounted(() => {
  void load();
  timer = window.setInterval(() => {
    void load();
  }, 15000);
});

onBeforeUnmount(() => {
  alive = false;
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <section class="today">
    <header class="today__head">
      <div>
        <h2>工作台</h2>
        <p class="muted">先处理需要你的事，再看系统状态。</p>
      </div>
      <button type="button" class="ghost" :disabled="loading" @click="load">
        {{ loading ? "刷新中" : "刷新" }}
      </button>
    </header>

    <error-bar v-if="error" :message="error" @retry="load" />

    <div class="cards">
      <article class="card card--review">
        <header class="card__head">
          <span>待你验收</span>
          <b class="card__number">{{ review.length }}</b>
        </header>
        <div v-if="review.length" class="rows">
          <div v-for="ticket in review.slice(0, 5)" :key="ticket.id" class="row">
            <span class="mono code">{{ ticket.code }}</span>
            <span class="title">{{ ticket.title }}</span>
            <span class="actions">
              <button
                type="button"
                class="mini"
                :disabled="acting !== null"
                @click="decide(ticket, 'approve')"
              >
                通过
              </button>
              <button
                type="button"
                class="mini danger"
                :disabled="acting !== null"
                @click="decide(ticket, 'reject')"
              >
                驳回
              </button>
            </span>
          </div>
        </div>
        <empty-state
          v-else
          title="没有待验收的工单"
          description="工雁交付后会出现在这里。"
        />
        <router-link to="/tickets" class="more">查看全部需求 →</router-link>
      </article>

      <article class="card card--signals">
        <header class="card__head">
          <span>卡住的事</span>
          <b class="card__number">{{ topSignals.length }}</b>
        </header>
        <div v-if="topSignals.length" class="rows">
          <div v-for="signal in topSignals" :key="`${signal.kind}-${signal.id}`" class="row row--signal">
            <span class="dot" aria-hidden="true"></span>
            <span class="title">{{ signalText(signal) }}</span>
          </div>
        </div>
        <empty-state
          v-else
          title="没有卡住的派工"
          description="派工超时、作业失败、写回失败或节点离线会出现在这里。"
        />
        <router-link to="/signals" class="more">查看全部信号 →</router-link>
      </article>

      <article class="card card--running">
        <header class="card__head">
          <span>正在跑</span>
          <b class="card__number">{{ activeDispatches }}</b>
        </header>
        <dl class="running">
          <dt>在途派工</dt>
          <dd class="mono">{{ activeDispatches }}</dd>
          <dt>已记录会话</dt>
          <dd class="mono">{{ sessionCount }}</dd>
        </dl>
        <p class="muted hint">点进运行面看拓扑、节点和会话。</p>
        <router-link to="/fleet" class="more">进入运行 →</router-link>
      </article>
    </div>

    <p v-if="loading && !review.length && !topSignals.length" class="muted loading">
      正在读取工作台…
    </p>
  </section>
</template>

<style scoped>
.today {
  min-width: 0;
}

.today__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.today__head h2 {
  margin: 0;
  color: var(--text);
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.14em;
}

.today__head p {
  margin: 4px 0 0;
  font-size: 12px;
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
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.65);
}

.cards {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 0.9fr);
  gap: 12px;
}

@media (max-width: 1100px) {
  .cards {
    grid-template-columns: minmax(0, 1fr);
  }
}

.card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 12px 14px 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
  border-left: 2px solid var(--line);
}

.card--review {
  border-left-color: var(--amber);
}

.card--signals {
  border-left-color: var(--bad);
}

.card--running {
  border-left-color: var(--cyan);
}

.card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.14em;
}

.card__number {
  color: var(--text);
  font-family: var(--mono);
  font-size: 26px;
  line-height: 1;
  font-weight: 700;
}

.card--review .card__number {
  color: var(--amber);
}

.card--signals .card__number {
  color: var(--bad);
}

.card--running .card__number {
  color: var(--cyan);
}

.rows {
  display: flex;
  flex-direction: column;
  margin-top: 8px;
}

.row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  padding: 7px 0;
  border-bottom: 1px solid rgba(125, 137, 184, 0.12);
  font-size: 12px;
}

.row .code {
  color: var(--cyan);
  font-size: 11px;
}

.row .title {
  flex: 1 1 160px;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row--signal {
  align-items: flex-start;
  color: var(--text);
}

.row--signal .title {
  white-space: normal;
  overflow-wrap: anywhere;
}

.actions {
  display: flex;
  gap: 4px;
  margin-left: auto;
}

.mini {
  padding: 2px 7px 3px;
  background: transparent;
  border: 1px solid var(--line);
  color: var(--text);
  font-size: 11px;
  cursor: pointer;
}

.mini:hover:not(:disabled) {
  border-color: var(--cyan);
  color: var(--cyan);
}

.mini.danger:hover:not(:disabled) {
  border-color: var(--bad);
  color: var(--bad);
}

.mini:disabled {
  opacity: 0.4;
  cursor: default;
}

.dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  margin-top: 6px;
  border-radius: 50%;
  background: var(--bad);
  box-shadow: 0 0 8px var(--bad);
}

.running {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px 12px;
  margin: 12px 0 8px;
  font-size: 12px;
}

.running dt {
  color: var(--muted);
}

.running dd {
  margin: 0;
  font-size: 15px;
}

.hint {
  margin: 0 0 10px;
  font-size: 12px;
}

.more {
  margin-top: auto;
  padding-top: 8px;
  color: var(--cyan);
  font-size: 12px;
  text-decoration: none;
}

.more:hover {
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.65);
}

.mono {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
}

.loading {
  margin-top: 12px;
  font-size: 12px;
}
</style>

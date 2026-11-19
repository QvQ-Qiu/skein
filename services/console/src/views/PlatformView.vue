<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { NButton, NTabPane, NTabs, useMessage } from "naive-ui";
import {
  ApiError,
  fetchAdmission,
  fetchAudit,
  fetchPrompts,
  fetchRuns,
  request,
  saveAdmission,
  type AdmissionPolicies,
  type AuditRecord,
  type PromptPointer,
  type RunRecord,
} from "../api";
import AdmissionTable from "../components/AdmissionTable.vue";
import EmptyState from "../components/EmptyState.vue";
import PromptTable from "../components/PromptTable.vue";
import StatusTag from "../components/StatusTag.vue";

type DataTab = "prompts" | "runs" | "audit";
type PromptAction = "promote" | "rollback" | "unfreeze";

const ACTION_LABEL: Record<PromptAction, string> = {
  promote: "升格",
  rollback: "回退",
  unfreeze: "解冻",
};

const DETAIL_CELL = 160;
const DETAIL_TITLE = 4000;
const REFRESH_MS = 15_000;

const message = useMessage();
const tab = ref<string | number>("prompts");
const prompts = ref<PromptPointer[]>([]);
const runs = ref<RunRecord[]>([]);
const audit = ref<AuditRecord[]>([]);
const admission = ref<AdmissionPolicies | null>(null);
const admissionSaving = ref(false);
const admissionFailed = ref(false);
const loading = ref(false);
const busyKey = ref<string | null>(null);
const lastRefreshAt = ref<number | null>(null);
const settled = reactive<Record<DataTab, boolean>>({
  prompts: false,
  runs: false,
  audit: false,
});
const failed = reactive<Record<DataTab, boolean>>({
  prompts: false,
  runs: false,
  audit: false,
});

let fetchGen = 0;
let admissionGen = 0;
let loadingToken = 0;
let timer = 0;
let alive = true;
let lastError = "";

const refreshLabel = computed(() =>
  lastRefreshAt.value == null ? "尚未刷新" : formatDate(new Date(lastRefreshAt.value)),
);

const refreshIso = computed(() =>
  lastRefreshAt.value == null ? undefined : new Date(lastRefreshAt.value).toISOString(),
);

const auditRows = computed(() =>
  audit.value.map((row) => {
    const full = serializeDetail(row.detail);
    return {
      id: row.id,
      when: formatUnix(row.created_at),
      actor: row.actor,
      action: row.action,
      detailShort: truncate(full, DETAIL_CELL),
      detailTitle: truncate(full, DETAIL_TITLE),
    };
  }),
);

function formatDate(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  const seconds = pad(date.getSeconds());
  return `${date.getFullYear()}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function formatUnix(seconds: number): string {
  if (!Number.isFinite(seconds)) return String(seconds);
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return String(seconds);
  return formatDate(date);
}

function zhStatus(status: string): string {
  switch (status.trim().toLowerCase()) {
    case "running":
      return "进行中";
    case "failed":
    case "error":
      return "失败";
    case "done":
    case "succeeded":
      return "完成";
    case "success":
    case "ok":
      return "成功";
    case "pending":
      return "待处理";
    case "queued":
      return "排队";
    case "cancelled":
    case "canceled":
      return "已取消";
    case "skipped":
      return "已跳过";
    default:
      return status.trim() || "—";
  }
}

function truncate(text: string, max: number): string {
  const chars = Array.from(text);
  if (chars.length <= max) return text;
  return `${chars.slice(0, max).join("")}...`;
}

function serializeDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "symbol" || typeof value === "function") return "";
  const seen = new WeakSet<object>();
  try {
    const text = JSON.stringify(value, (_key, nested: unknown) => {
      if (typeof nested === "bigint") return nested.toString();
      if (typeof nested === "symbol" || typeof nested === "function") return undefined;
      if (nested !== null && typeof nested === "object") {
        if (seen.has(nested)) return "[Circular]";
        seen.add(nested);
      }
      return nested;
    });
    return text ?? "";
  } catch {
    return "";
  }
}

function tabName(): string {
  return String(tab.value);
}

function isDataTab(value: string): value is DataTab {
  return value === "prompts" || value === "runs" || value === "audit";
}

function errorText(err: unknown): string {
  if (err instanceof ApiError && err.message.trim()) return err.message;
  if (err instanceof Error && err.message.trim()) return err.message;
  return "网络错误";
}

function reportError(text: string, source: "manual" | "poll"): void {
  if (!alive) return;
  if (source === "poll" && text === lastError) return;
  lastError = text;
  message.error(text);
}

async function loadAdmission(source: "manual" | "poll"): Promise<void> {
  if (source === "poll" || admissionSaving.value) return;
  const mine = ++admissionGen;
  const manualToken = ++loadingToken;
  loading.value = true;
  try {
    const body = await fetchAdmission();
    if (!alive || mine !== admissionGen) return;
    admission.value = body;
    admissionFailed.value = false;
    lastRefreshAt.value = Date.now();
    lastError = "";
  } catch (err) {
    if (!alive || mine !== admissionGen) return;
    admissionFailed.value = true;
    reportError(errorText(err), "manual");
  } finally {
    if (manualToken === loadingToken) loading.value = false;
  }
}

async function onSaveAdmission(body: AdmissionPolicies): Promise<void> {
  if (admissionSaving.value) return;
  admissionSaving.value = true;
  try {
    const saved = await saveAdmission(body);
    if (!alive) return;
    admission.value = saved;
    admissionFailed.value = false;
    lastError = "";
    message.success("准入策略已保存");
  } catch (err) {
    if (!alive) return;
    const text = errorText(err);
    lastError = text;
    message.error(`保存失败：${text}`);
  } finally {
    admissionSaving.value = false;
  }
}

async function loadCurrent(source: "manual" | "poll"): Promise<void> {
  const key = tabName();
  if (key === "admission") {
    await loadAdmission(source);
    return;
  }
  if (!isDataTab(key)) return;
  if (key === "prompts" && busyKey.value) return;
  const mine = ++fetchGen;
  const manualToken = source === "manual" ? ++loadingToken : 0;
  if (source === "manual") loading.value = true;
  try {
    if (key === "prompts") {
      const body = await fetchPrompts();
      if (!alive || mine !== fetchGen) return;
      prompts.value = body.prompts;
    } else if (key === "runs") {
      const body = await fetchRuns(50);
      if (!alive || mine !== fetchGen) return;
      runs.value = body.runs;
    } else {
      const body = await fetchAudit(100);
      if (!alive || mine !== fetchGen) return;
      audit.value = body.audit;
    }
    settled[key] = true;
    failed[key] = false;
    lastRefreshAt.value = Date.now();
    lastError = "";
  } catch (err) {
    if (!alive || mine !== fetchGen) return;
    failed[key] = true;
    reportError(errorText(err), source);
  } finally {
    if (source === "manual" && manualToken === loadingToken) loading.value = false;
  }
}

async function run(action: PromptAction, part: string): Promise<void> {
  if (busyKey.value) return;
  const key = `${action}:${part}`;
  busyKey.value = key;
  const mine = ++fetchGen;
  try {
    await request<unknown>(`/internal/prompts/${action}`, {
      method: "POST",
      body: JSON.stringify({ part }),
    });
  } catch (err) {
    if (alive) {
      const text = errorText(err);
      lastError = text;
      message.error(`${ACTION_LABEL[action]}失败：${text}`);
    }
    if (busyKey.value === key) busyKey.value = null;
    return;
  }
  try {
    const body = await fetchPrompts();
    if (!alive || mine !== fetchGen) return;
    prompts.value = body.prompts;
    settled.prompts = true;
    failed.prompts = false;
    if (tabName() === "prompts") {
      lastRefreshAt.value = Date.now();
      lastError = "";
    }
  } catch (err) {
    if (alive) reportError(errorText(err), "manual");
  } finally {
    if (busyKey.value === key) busyKey.value = null;
  }
}

function onPromote(part: string): void {
  void run("promote", part);
}

function onRollback(part: string): void {
  void run("rollback", part);
}

function onUnfreeze(part: string): void {
  void run("unfreeze", part);
}

function restartTimer(): void {
  window.clearInterval(timer);
  timer = window.setInterval(() => {
    void loadCurrent("poll");
  }, REFRESH_MS);
}

function refresh(): void {
  void loadCurrent("manual");
  restartTimer();
}

watch(tab, () => {
  void loadCurrent("manual");
  restartTimer();
});

onMounted(() => {
  void loadCurrent("manual");
  restartTimer();
});

onUnmounted(() => {
  alive = false;
  window.clearInterval(timer);
  fetchGen += 1;
  loadingToken += 1;
});
</script>

<template>
  <section class="page platform">
    <header class="platform__bar">
      <h2 class="page__title">平台</h2>
      <div class="platform__refresh">
        <span class="muted">最近刷新</span>
        <time class="mono" :datetime="refreshIso">{{ refreshLabel }}</time>
        <n-button
          class="text-btn"
          size="small"
          quaternary
          :loading="loading"
          :disabled="
            (tabName() === 'prompts' && busyKey !== null) ||
            (tabName() === 'admission' && admissionSaving)
          "
          @click="refresh"
        >
          刷新
        </n-button>
      </div>
    </header>
    <n-tabs v-model:value="tab" class="platform__tabs" type="line" size="small" :animated="false">
      <n-tab-pane name="prompts" tab="提示词">
        <prompt-table
          v-if="prompts.length > 0"
          :rows="prompts"
          :busy-key="busyKey"
          @promote="onPromote"
          @rollback="onRollback"
          @unfreeze="onUnfreeze"
        />
        <empty-state
          v-else-if="settled.prompts"
          title="暂无提示词指针"
          description="Watchdog 或人工提出修订后，这里会出现稳定版与金丝雀。"
        />
        <p v-else-if="failed.prompts" class="muted state-line">加载失败</p>
        <p v-else class="muted state-line">加载中</p>
      </n-tab-pane>
      <n-tab-pane name="runs" tab="运行">
        <table v-if="runs.length > 0" class="grid">
          <colgroup>
            <col class="col-job" />
            <col class="col-day" />
            <col class="col-status" />
            <col class="col-run-detail" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">任务</th>
              <th scope="col">日期</th>
              <th scope="col">状态</th>
              <th scope="col">详情</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in runs" :key="row.id">
              <td>
                <span class="clip mono" :title="row.job">{{ row.job }}</span>
              </td>
              <td>
                <span class="clip mono" :title="row.day">{{ row.day }}</span>
              </td>
              <td>
                <status-tag
                  :class="{ 'is-code': zhStatus(row.status) === row.status }"
                  :status="row.status"
                  :label="zhStatus(row.status)"
                  :title="row.status"
                />
              </td>
              <td>
                <span class="clip" :title="row.detail || undefined">{{ row.detail ?? "" }}</span>
              </td>
            </tr>
          </tbody>
        </table>
        <empty-state
          v-else-if="settled.runs"
          title="暂无运行记录"
          description="定时作业开跑后会在这里留下状态与说明。"
        />
        <p v-else-if="failed.runs" class="muted state-line">加载失败</p>
        <p v-else class="muted state-line">加载中</p>
      </n-tab-pane>
      <n-tab-pane name="audit" tab="审计">
        <table v-if="auditRows.length > 0" class="grid">
          <colgroup>
            <col class="col-time" />
            <col class="col-actor" />
            <col class="col-action" />
            <col class="col-detail" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">时间</th>
              <th scope="col">操作者</th>
              <th scope="col">动作</th>
              <th scope="col">详情</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in auditRows" :key="row.id">
              <td>
                <span class="clip mono" :title="row.when">{{ row.when }}</span>
              </td>
              <td>
                <span class="clip mono" :title="row.actor">{{ row.actor }}</span>
              </td>
              <td>
                <span class="clip mono" :title="row.action">{{ row.action }}</span>
              </td>
              <td>
                <span class="clip mono" :title="row.detailTitle || undefined">{{ row.detailShort }}</span>
              </td>
            </tr>
          </tbody>
        </table>
        <empty-state
          v-else-if="settled.audit"
          title="暂无审计记录"
          description="派单、验收、改提示词和准入后，这里会留下操作人。"
        />
        <p v-else-if="failed.audit" class="muted state-line">加载失败</p>
        <p v-else class="muted state-line">加载中</p>
      </n-tab-pane>
      <n-tab-pane name="admission" tab="准入">
        <p class="admission-note">程序层准入先于模型，改完下一次采集生效。</p>
        <admission-table
          v-if="admission"
          :policy="admission"
          :saving="admissionSaving"
          @save="onSaveAdmission"
        />
        <p v-else-if="admissionFailed" class="muted state-line">加载失败</p>
        <p v-else class="muted state-line">加载中</p>
      </n-tab-pane>
    </n-tabs>
  </section>
</template>

<style scoped>
.platform {
  min-width: 0;
  max-width: 100%;
  overflow-x: clip;
}

.platform__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  min-width: 0;
  margin-bottom: 4px;
  padding-bottom: 8px;
  background-image: linear-gradient(90deg, rgba(79, 216, 235, 0.4), transparent 72%);
  background-repeat: no-repeat;
  background-position: left bottom;
  background-size: 100% 1px;
}

.platform__bar .page__title {
  margin: 0;
  color: var(--muted);
  font-weight: 500;
  letter-spacing: 0.22em;
}

.platform__refresh {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.platform__refresh time {
  color: var(--text);
  font-size: 12px;
}

.platform :deep(.text-btn.n-button) {
  --n-border: none !important;
  --n-border-hover: none !important;
  --n-border-pressed: none !important;
  --n-border-focus: none !important;
  --n-border-disabled: none !important;
  --n-color: transparent !important;
  --n-color-hover: transparent !important;
  --n-color-pressed: transparent !important;
  --n-color-focus: transparent !important;
  --n-color-disabled: transparent !important;
  --n-text-color: #dfe6ff !important;
  --n-text-color-hover: #4fd8eb !important;
  --n-text-color-pressed: #2fb8cc !important;
  --n-text-color-focus: #4fd8eb !important;
  --n-ripple-color: transparent !important;
  letter-spacing: 0.12em;
}

.platform :deep(.text-btn.n-button:not(.n-button--disabled):hover) {
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.7);
  box-shadow: inset 0 -1px 0 var(--cyan);
}

.platform__tabs {
  min-width: 0;
  max-width: 100%;
}

.platform :deep(.n-tabs) {
  --n-bar-color: #4fd8eb !important;
  --n-tab-text-color: #7d89b8 !important;
  --n-tab-text-color-hover: #4fd8eb !important;
  --n-tab-text-color-active: #4fd8eb !important;
  --n-tab-border-color: transparent !important;
}

.platform :deep(.n-tabs-nav) {
  box-shadow: inset 0 -1px 0 rgba(79, 216, 235, 0.16);
}

.platform :deep(.n-tabs-tab) {
  letter-spacing: 0.14em;
}

.platform :deep(.n-tabs-tab.n-tabs-tab--active) {
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.75);
}

.platform :deep(.n-tabs-bar) {
  height: 1px !important;
  background: linear-gradient(90deg, transparent, var(--cyan), transparent) !important;
  box-shadow: 0 0 8px rgba(79, 216, 235, 0.8);
}

.platform :deep(.n-tabs-nav),
.platform :deep(.n-tabs-pane-wrapper),
.platform :deep(.n-tab-pane) {
  min-width: 0;
  max-width: 100%;
}

.platform :deep(.n-tab-pane) {
  max-height: calc(100vh - 188px);
  padding: 8px 0 0;
  overflow: auto;
}

.grid {
  width: 100%;
  max-width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  border: none;
  font-size: 12px;
  box-shadow: inset 0 1px 0 rgba(79, 216, 235, 0.28);
}

.col-job {
  width: 20%;
}

.col-day {
  width: 16%;
}

.col-status {
  width: 18%;
}

.col-run-detail {
  width: 46%;
}

.col-time {
  width: 22%;
}

.col-actor {
  width: 16%;
}

.col-action {
  width: 20%;
}

.col-detail {
  width: 42%;
}

.grid th,
.grid td {
  padding: 6px 8px;
  border: none;
  text-align: left;
  vertical-align: middle;
  overflow: hidden;
  box-shadow: inset 0 -1px 0 rgba(79, 216, 235, 0.14);
}

.grid th {
  position: sticky;
  top: 0;
  z-index: 1;
  color: var(--muted);
  font-weight: 500;
  letter-spacing: 0.12em;
  background: rgba(4, 6, 14, 0.94);
}

.grid tbody tr:hover td {
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.07), transparent 78%);
}

.grid :deep(.status-tag.is-code) {
  font-family: var(--mono);
  font-size: 11px;
}

.clip {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.state-line {
  margin: 22px 0;
  letter-spacing: 0.16em;
  text-align: center;
}

.admission-note {
  margin: 4px 0 10px;
  color: var(--muted);
  letter-spacing: 0.04em;
}
</style>

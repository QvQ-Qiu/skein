<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton, useMessage } from "naive-ui";
import type { AdmissionPolicies, SourcePolicy } from "../api";

const SOURCE_NAME = /^[a-z0-9:_-]{1,64}$/;

const props = defineProps<{
  policy: AdmissionPolicies;
  saving: boolean;
}>();

const emit = defineEmits<{
  save: [policy: AdmissionPolicies];
}>();

const message = useMessage();

interface DraftPolicy {
  accept: boolean;
  require_mention: boolean;
  record_unmentioned: boolean;
  min_text_chars: number | null;
}

interface Draft {
  default: DraftPolicy;
  sources: Record<string, DraftPolicy>;
}

function copyPolicy(policy: SourcePolicy): DraftPolicy {
  return {
    accept: policy.accept,
    require_mention: policy.require_mention,
    record_unmentioned: policy.record_unmentioned,
    min_text_chars: policy.min_text_chars,
  };
}

function snapshot(policy: AdmissionPolicies): Draft {
  const sources: Record<string, DraftPolicy> = {};
  for (const [name, item] of Object.entries(policy.sources)) {
    sources[name] = copyPolicy(item);
  }
  return { default: copyPolicy(policy.default), sources };
}

const draft = ref<Draft>(snapshot(props.policy));
const order = ref<string[]>(Object.keys(props.policy.sources));
const sourceDraft = ref("");

watch(
  () => props.policy,
  (policy) => {
    draft.value = snapshot(policy);
    order.value = Object.keys(policy.sources);
    sourceDraft.value = "";
  },
);

const rows = computed(() => {
  const items: { key: string; source: string; policy: DraftPolicy; removable: boolean }[] = [
    { key: "fallback", source: "default", policy: draft.value.default, removable: false },
  ];
  for (const name of order.value) {
    const policy = draft.value.sources[name];
    if (!policy) continue;
    items.push({ key: `src:${name}`, source: name, policy, removable: true });
  }
  return items;
});

function validChars(value: number | null): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 200;
}

function addSource(): void {
  const name = sourceDraft.value.trim();
  if (!SOURCE_NAME.test(name)) {
    message.error("来源名只允许小写字母、数字、冒号、下划线和连字符，1–64 位");
    return;
  }
  if (name === "default" || Object.prototype.hasOwnProperty.call(draft.value.sources, name)) {
    message.error(name === "default" ? "default 是默认行" : "来源已存在");
    return;
  }
  draft.value.sources[name] = {
    accept: true,
    require_mention: true,
    record_unmentioned: true,
    min_text_chars: 2,
  };
  order.value = [...order.value, name];
  sourceDraft.value = "";
}

function removeSource(name: string): void {
  delete draft.value.sources[name];
  order.value = order.value.filter((item) => item !== name);
}

function onReset(): void {
  draft.value = snapshot(props.policy);
  order.value = Object.keys(props.policy.sources);
  sourceDraft.value = "";
}

function toPolicy(item: DraftPolicy): SourcePolicy | null {
  if (!validChars(item.min_text_chars)) return null;
  return {
    accept: item.accept,
    require_mention: item.require_mention,
    record_unmentioned: item.record_unmentioned,
    min_text_chars: item.min_text_chars,
  };
}

function onSave(): void {
  const fallback = toPolicy(draft.value.default);
  if (!fallback) {
    message.error("最短字数必须是 0–200 的整数");
    return;
  }
  const sources: Record<string, SourcePolicy> = {};
  for (const name of order.value) {
    const item = draft.value.sources[name];
    const policy = item ? toPolicy(item) : null;
    if (!policy) {
      message.error("最短字数必须是 0–200 的整数");
      return;
    }
    sources[name] = policy;
  }
  emit("save", { default: fallback, sources });
}

function onChars(policy: DraftPolicy, event: Event): void {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) return;
  const raw = target.value.trim();
  if (raw === "") {
    policy.min_text_chars = null;
    return;
  }
  const value = Number(raw);
  policy.min_text_chars = Number.isFinite(value) ? value : null;
}
</script>

<template>
  <div class="admission">
    <table class="grid">
      <colgroup>
        <col class="col-source" />
        <col class="col-flag" />
        <col class="col-flag" />
        <col class="col-flag" />
        <col class="col-chars" />
        <col class="col-ops" />
      </colgroup>
      <thead>
        <tr>
          <th scope="col">来源</th>
          <th scope="col" title="accept，关闭后该来源全部忽略">接收</th>
          <th scope="col" title="require_mention，未点名不进入处理">需要点名</th>
          <th scope="col" title="record_unmentioned，允许名单里未点名的是否记账">未点名记账</th>
          <th scope="col" title="min_text_chars，0–200">最短字数</th>
          <th scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key">
          <td>
            <span class="clip mono" :title="row.source">{{ row.source }}</span>
          </td>
          <td>
            <input
              v-model="row.policy.accept"
              class="tick"
              type="checkbox"
              :disabled="saving"
              :aria-label="`${row.source} accept`"
            />
          </td>
          <td>
            <input
              v-model="row.policy.require_mention"
              class="tick"
              type="checkbox"
              :disabled="saving"
              :aria-label="`${row.source} require_mention`"
            />
          </td>
          <td>
            <input
              v-model="row.policy.record_unmentioned"
              class="tick"
              type="checkbox"
              :disabled="saving"
              :aria-label="`${row.source} record_unmentioned`"
            />
          </td>
          <td>
            <input
              class="chars mono"
              type="number"
              min="0"
              max="200"
              step="1"
              :value="row.policy.min_text_chars ?? ''"
              :disabled="saving"
              :aria-label="`${row.source} min_text_chars`"
              @input="onChars(row.policy, $event)"
            />
          </td>
          <td>
            <n-button
              v-if="row.removable"
              class="text-btn"
              size="tiny"
              quaternary
              :disabled="saving"
              @click="removeSource(row.source)"
            >
              移除
            </n-button>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="admission__bar">
      <input
        v-model="sourceDraft"
        class="name mono"
        type="text"
        maxlength="64"
        placeholder="来源名，如 forum_comment"
        :disabled="saving"
        @keydown.enter.prevent="addSource"
      />
      <n-button class="text-btn" size="small" quaternary :disabled="saving" @click="addSource">
        添加
      </n-button>
      <span class="admission__gap" />
      <n-button class="text-btn" size="small" quaternary :disabled="saving" @click="onReset">
        重置
      </n-button>
      <n-button
        class="text-btn"
        size="small"
        quaternary
        :loading="saving"
        :disabled="saving"
        @click="onSave"
      >
        保存
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.admission {
  min-width: 0;
  max-width: 100%;
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

.col-source {
  width: 22%;
}

.col-flag {
  width: 16%;
}

.col-chars {
  width: 16%;
}

.col-ops {
  width: 14%;
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
  letter-spacing: 0.08em;
  background: rgba(4, 6, 14, 0.94);
}

.grid tbody tr:hover td {
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.07), transparent 78%);
}

.clip {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tick {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--cyan);
}

.chars,
.name {
  max-width: 100%;
  padding: 2px 6px;
  background: transparent;
  border: none;
  border-bottom: 1px solid rgba(79, 216, 235, 0.35);
  color: var(--text);
  outline: none;
}

.chars {
  width: 72px;
}

.chars:focus,
.name:focus {
  border-bottom-color: var(--cyan);
}

.admission__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-top: 12px;
}

.name {
  flex: 1 1 180px;
  min-width: 0;
}

.admission__gap {
  flex: 1 1 auto;
}

.admission :deep(.text-btn.n-button) {
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
  --n-text-color-disabled: #7d89b8 !important;
  --n-ripple-color: transparent !important;
  letter-spacing: 0.08em;
}

.admission :deep(.text-btn.n-button:not(.n-button--disabled):hover) {
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.65);
  box-shadow: inset 0 -1px 0 var(--cyan);
}
</style>

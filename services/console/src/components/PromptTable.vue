<script setup lang="ts">
import { NButton, NPopconfirm } from "naive-ui";
import type { PromptPointer } from "../api";

type PromptAction = "promote" | "rollback" | "unfreeze";

const props = defineProps<{
  rows: PromptPointer[];
  busyKey: string | null;
}>();

const emit = defineEmits<{
  promote: [part: string];
  rollback: [part: string];
  unfreeze: [part: string];
}>();

function isBusy(action: PromptAction, part: string): boolean {
  return props.busyKey === `${action}:${part}`;
}

function formatUnix(seconds: number): string {
  if (!Number.isFinite(seconds)) return String(seconds);
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return String(seconds);
  const pad = (value: number) => String(value).padStart(2, "0");
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  const secondsOfDay = pad(date.getSeconds());
  return `${date.getFullYear()}-${month}-${day} ${hours}:${minutes}:${secondsOfDay}`;
}

function frozenLabel(seconds: number | null): string {
  return seconds == null ? "无" : formatUnix(seconds);
}
</script>

<template>
  <table class="grid">
    <colgroup>
      <col class="col-part" />
      <col class="col-rev" />
      <col class="col-rev" />
      <col class="col-frozen" />
      <col class="col-actions" />
    </colgroup>
    <thead>
      <tr>
        <th scope="col">part</th>
        <th scope="col">稳定修订</th>
        <th scope="col">金丝雀修订</th>
        <th scope="col">冻结到</th>
        <th scope="col">操作</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="row in rows" :key="row.part">
        <td>
          <span class="clip mono" :title="row.part">{{ row.part }}</span>
        </td>
        <td>
          <span class="clip mono">{{ row.stable_revision }}</span>
        </td>
        <td>
          <span class="clip mono">{{ row.canary_revision != null ? row.canary_revision : "无" }}</span>
        </td>
        <td>
          <span class="clip mono">{{ frozenLabel(row.frozen_until) }}</span>
        </td>
        <td class="actions-cell">
          <div class="actions">
            <n-popconfirm
              v-if="row.canary_revision != null"
              to="body"
              :show-icon="false"
              :animated="false"
              positive-text="确认"
              negative-text="取消"
              :content-style="{ maxWidth: '280px' }"
              @positive-click="emit('promote', row.part)"
            >
              <template #trigger>
                <n-button
                  size="tiny"
                  :loading="isBusy('promote', row.part)"
                  :disabled="busyKey !== null"
                >
                  升格
                </n-button>
              </template>
              确认把 {{ row.part }} 的金丝雀修订升格为稳定修订？
            </n-popconfirm>
            <n-popconfirm
              v-if="row.canary_revision != null"
              to="body"
              :show-icon="false"
              :animated="false"
              positive-text="确认"
              negative-text="取消"
              :content-style="{ maxWidth: '280px' }"
              @positive-click="emit('rollback', row.part)"
            >
              <template #trigger>
                <n-button
                  size="tiny"
                  :loading="isBusy('rollback', row.part)"
                  :disabled="busyKey !== null"
                >
                  回退
                </n-button>
              </template>
              确认回退 {{ row.part }} 的金丝雀修订？
            </n-popconfirm>
            <n-popconfirm
              v-if="row.frozen_until != null"
              to="body"
              :show-icon="false"
              :animated="false"
              positive-text="确认"
              negative-text="取消"
              :content-style="{ maxWidth: '280px' }"
              @positive-click="emit('unfreeze', row.part)"
            >
              <template #trigger>
                <n-button
                  size="tiny"
                  :loading="isBusy('unfreeze', row.part)"
                  :disabled="busyKey !== null"
                >
                  解冻
                </n-button>
              </template>
              确认解冻 {{ row.part }}？
            </n-popconfirm>
          </div>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.grid {
  width: 100%;
  max-width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  border: none;
  font-size: 12px;
  box-shadow: inset 0 1px 0 rgba(79, 216, 235, 0.28);
}

.col-part {
  width: 24%;
}

.col-rev {
  width: 13%;
}

.col-frozen {
  width: 22%;
}

.col-actions {
  width: 28%;
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

.clip {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actions-cell {
  overflow: hidden;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
  min-width: 0;
  max-width: 100%;
}

.actions :deep(.n-button) {
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

.actions :deep(.n-button:not(.n-button--disabled):hover) {
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.65);
  box-shadow: inset 0 -1px 0 var(--cyan);
}
</style>

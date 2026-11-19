<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  darkTheme,
  dateZhCN,
  zhCN,
  NButton,
  NConfigProvider,
  NDialogProvider,
  NInput,
  NMessageProvider,
  type GlobalThemeOverrides,
} from "naive-ui";
import ErrorBar from "./components/ErrorBar.vue";
import SkyCanvas from "./components/SkyCanvas.vue";
import { useConsoleStore } from "./store";

const store = useConsoleStore();
const route = useRoute();
const router = useRouter();
const tokenDraft = ref(store.token);
const submitted = ref(false);

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: "#4fd8eb",
    primaryColorHover: "#7ae6f4",
    primaryColorPressed: "#2fb8cc",
    primaryColorSuppl: "#4fd8eb",
    infoColor: "#6ea8fe",
    infoColorHover: "#8ebbff",
    infoColorPressed: "#4d8eeb",
    infoColorSuppl: "#6ea8fe",
    successColor: "#57d9a3",
    successColorHover: "#7ee4b8",
    successColorPressed: "#36c48a",
    successColorSuppl: "#57d9a3",
    warningColor: "#f5b942",
    warningColorHover: "#ffcb6b",
    warningColorPressed: "#e0a428",
    warningColorSuppl: "#f5b942",
    errorColor: "#f26d7d",
    errorColorHover: "#ff8b98",
    errorColorPressed: "#e05264",
    errorColorSuppl: "#f26d7d",
    bodyColor: "#04060e",
    cardColor: "#0a1024",
    modalColor: "#0a1024",
    popoverColor: "#10182e",
    tableColor: "#0a1024",
    tableHeaderColor: "#10182e",
    tableColorHover: "rgba(79, 216, 235, 0.06)",
    tableColorStriped: "#0c1428",
    inputColor: "transparent",
    hoverColor: "rgba(79, 216, 235, 0.08)",
    pressedColor: "rgba(79, 216, 235, 0.14)",
    borderColor: "#303a54",
    dividerColor: "#303a54",
    borderRadius: "0",
    borderRadiusSmall: "0",
    textColorBase: "#dfe6ff",
    textColor1: "#dfe6ff",
    textColor2: "#c5d0ee",
    textColor3: "#7d89b8",
    placeholderColor: "#7d89b8",
    iconColor: "#7d89b8",
    iconColorHover: "#4fd8eb",
    scrollbarColor: "rgba(79, 216, 235, 0.35)",
    scrollbarColorHover: "rgba(79, 216, 235, 0.55)",
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    fontFamilyMono: '"Cascadia Mono", "Sarasa Mono SC", ui-monospace, monospace',
    fontSize: "13px",
    fontSizeSmall: "12px",
  },
  Menu: {
    color: "transparent",
    itemColorHover: "transparent",
    itemColorActive: "transparent",
    itemColorActiveHover: "transparent",
    itemColorActiveCollapsed: "transparent",
    itemTextColor: "#7d89b8",
    itemTextColorHover: "#4fd8eb",
    itemTextColorActive: "#4fd8eb",
    itemTextColorActiveHover: "#4fd8eb",
    itemTextColorHorizontal: "#7d89b8",
    itemTextColorHoverHorizontal: "#4fd8eb",
    itemTextColorActiveHorizontal: "#4fd8eb",
    itemTextColorActiveHoverHorizontal: "#4fd8eb",
    itemIconColor: "#7d89b8",
    itemIconColorHover: "#4fd8eb",
    itemIconColorActive: "#4fd8eb",
    itemIconColorActiveHover: "#4fd8eb",
    arrowColor: "#7d89b8",
    arrowColorHover: "#4fd8eb",
    arrowColorActive: "#4fd8eb",
    arrowColorActiveHover: "#4fd8eb",
    borderRadius: "0px",
  },
  Input: {
    color: "transparent",
    colorHover: "transparent",
    colorFocus: "transparent",
    textColor: "#dfe6ff",
    placeholderColor: "#7d89b8",
    caretColor: "#4fd8eb",
    iconColor: "#7d89b8",
    iconColorHover: "#4fd8eb",
    border: "1px solid transparent",
    borderHover: "1px solid transparent",
    borderFocus: "1px solid transparent",
    boxShadowFocus: "none",
    borderRadius: "0",
  },
  Button: {
    color: "transparent",
    colorHover: "rgba(79, 216, 235, 0.08)",
    colorPressed: "rgba(79, 216, 235, 0.14)",
    colorFocus: "transparent",
    colorQuaternary: "transparent",
    colorQuaternaryHover: "transparent",
    colorQuaternaryPressed: "transparent",
    textColor: "#dfe6ff",
    textColorHover: "#4fd8eb",
    textColorPressed: "#2fb8cc",
    textColorFocus: "#dfe6ff",
    border: "1px solid #303a54",
    borderHover: "1px solid #4fd8eb",
    borderPressed: "1px solid #2fb8cc",
    borderFocus: "1px solid #4fd8eb",
    rippleColor: "transparent",
    colorPrimary: "#4fd8eb",
    colorHoverPrimary: "#7ae6f4",
    colorPressedPrimary: "#2fb8cc",
    colorFocusPrimary: "#4fd8eb",
    textColorPrimary: "#041018",
    textColorHoverPrimary: "#041018",
    textColorPressedPrimary: "#041018",
    textColorFocusPrimary: "#041018",
    borderPrimary: "1px solid #4fd8eb",
    borderHoverPrimary: "1px solid #7ae6f4",
    borderPressedPrimary: "1px solid #2fb8cc",
    borderFocusPrimary: "1px solid #4fd8eb",
    rippleColorPrimary: "transparent",
  },
};

const navItems = [
  { label: "工作台", key: "/today" },
  { label: "需求", key: "/tickets" },
  { label: "运行", key: "/fleet" },
  { label: "平台", key: "/platform" },
] as const;

const RUNS_NAV = [
  { label: "总览", key: "/fleet" },
  { label: "节点", key: "/nodes" },
  { label: "Worker", key: "/workers" },
  { label: "事件", key: "/events" },
  { label: "信号", key: "/signals" },
] as const;

const PLATFORM_NAV = [
  { label: "准入与提示词", key: "/platform" },
  { label: "日志", key: "/logs" },
] as const;

const booting = computed(
  () =>
    !store.connected &&
    store.token.length > 0 &&
    !store.unauthorized &&
    !store.error,
);

const landing = computed(() => !store.connected && !booting.value);

// 401 会把 store.error 清掉，拒绝原因要在落地上单独说清楚。
const gateMessage = computed(() => {
  if (store.connected) return "";
  if (store.error) return store.error;
  if (submitted.value && store.token.length === 0) return "请填写控制面令牌";
  if (store.refreshing) return "";
  if (store.unauthorized && store.token.length > 0) return "令牌无效，调度台拒绝接入";
  return "";
});

const refreshLabel = computed(() =>
  store.lastRefreshAt == null ? "尚未刷新" : formatStamp(store.lastRefreshAt),
);

const refreshIso = computed(() =>
  store.lastRefreshAt == null ? undefined : new Date(store.lastRefreshAt).toISOString(),
);

function formatStamp(ts: number): string {
  const date = new Date(ts);
  const pad = (value: number) => String(value).padStart(2, "0");
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  const seconds = pad(date.getSeconds());
  return `${date.getFullYear()}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function applyToken() {
  submitted.value = true;
  const value = tokenDraft.value.trim();
  tokenDraft.value = value;
  store.saveToken(value);
  void store.refresh();
}

const section = computed<"runs" | "platform" | null>(() => {
  if (["/fleet", "/nodes", "/workers", "/events", "/signals"].includes(route.path)) {
    return "runs";
  }
  if (["/platform", "/logs"].includes(route.path)) return "platform";
  return null;
});

const subItems = computed(() => {
  if (section.value === "runs") return RUNS_NAV;
  if (section.value === "platform") return PLATFORM_NAV;
  return [];
});

function navActive(key: string) {
  if (key === "/fleet") return section.value === "runs";
  if (key === "/platform") return section.value === "platform";
  return route.path === key || route.path.startsWith(`${key}/`);
}

function openNav(key: string) {
  void router.push(key);
}

onMounted(() => {
  void store.refresh();
});
</script>

<template>
  <n-config-provider
    class="app-root"
    :theme="darkTheme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-message-provider>
      <n-dialog-provider>
        <div class="hud-corner hud-tl" aria-hidden="true"></div>
        <div class="hud-corner hud-tr" aria-hidden="true"></div>
        <div class="hud-corner hud-bl" aria-hidden="true"></div>
        <div class="hud-corner hud-br" aria-hidden="true"></div>

        <div v-if="booting" class="boot" role="status" aria-live="polite">
          <span class="boot__dot" aria-hidden="true"></span>
          <span>正在连接控制面…</span>
        </div>

        <div v-else-if="landing" class="landing">
          <sky-canvas :landing="true" />
          <div class="landing__content">
            <h1>
              <span>雁</span><span>阵</span>
            </h1>
            <p class="landing__pitch">把真实用户反馈，变成可验收的修复</p>
            <p class="landing__sub">需求即命令 · 会话即工雁 · 账本即阵型</p>
            <form class="landing__form" @submit.prevent="applyToken">
              <n-input
                v-model:value="tokenDraft"
                class="token-field landing__token"
                type="password"
                placeholder="控制面令牌"
                autocomplete="off"
                :input-props="{ autocomplete: 'off', spellcheck: false, 'aria-label': '控制面令牌' }"
                @keyup.enter="applyToken"
              />
              <button class="landing__enter" type="submit" :aria-busy="store.refreshing">
                接 入 调 度 台 →
              </button>
              <p v-if="gateMessage" class="landing__error" role="alert">{{ gateMessage }}</p>
            </form>
            <router-link to="/about" class="landing__about">这是什么？</router-link>
          </div>
        </div>

        <div v-else class="shell">
          <header class="topbar">
            <div class="brand">
              <h1>雁阵</h1>
              <span class="brand__sub">SKEIN // AGENT GRID</span>
            </div>
            <nav class="nav" aria-label="主导航">
              <button
                v-for="item in navItems"
                :key="item.key"
                type="button"
                class="nav__item"
                :class="{ active: navActive(item.key) }"
                :aria-current="navActive(item.key) ? 'page' : undefined"
                @click="openNav(item.key)"
              >
                {{ item.label }}
              </button>
            </nav>
            <div class="topbar__meta">
              <div class="conn" :class="{ live: store.connected }">
                <span class="conn__dot" aria-hidden="true"></span>
                <span>{{ store.connected ? "LIVE" : "未连接" }}</span>
              </div>
              <div class="topbar__clock">
                <span>最近刷新</span>
                <time :key="store.lastRefreshAt ?? 0" class="mono" :datetime="refreshIso">{{ refreshLabel }}</time>
              </div>
              <form class="topbar__token" @submit.prevent="applyToken">
                <n-input
                  v-model:value="tokenDraft"
                  class="token-field"
                  size="small"
                  type="password"
                  placeholder="令牌"
                  autocomplete="off"
                  :input-props="{ autocomplete: 'off', spellcheck: false, 'aria-label': '控制面令牌' }"
                  @keyup.enter="applyToken"
                />
              </form>
              <n-button
                class="topbar__refresh"
                quaternary
                size="small"
                :loading="store.refreshing"
                @click="store.refresh()"
              >
                刷新
              </n-button>
            </div>
          </header>
          <nav v-if="subItems.length" class="subnav" aria-label="二级导航">
            <button
              v-for="item in subItems"
              :key="item.key"
              type="button"
              class="subnav__item"
              :class="{ active: route.path === item.key }"
              :aria-current="route.path === item.key ? 'page' : undefined"
              @click="openNav(item.key)"
            >
              {{ item.label }}
            </button>
          </nav>
          <main class="main">
            <error-bar v-if="store.error" :message="store.error" @retry="store.refresh()" />
            <router-view />
          </main>
        </div>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

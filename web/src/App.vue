<script setup>
import { computed, watch, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import SkyCanvas from './components/SkyCanvas.vue';
import EntityDrawer from './components/EntityDrawer.vue';
import TicketDrawer from './components/TicketDrawer.vue';
import { session, loadMe, api } from './api.js';
import { live, toasts, connectLive, disconnectLive, toast } from './live.js';

const route = useRoute(); const router = useRouter();
const landing = computed(() => session.checked && !session.me);
const entity = computed(() => ['worker', 'node', 'router'].includes(route.name ?? '') || ['worker', 'node', 'router'].some(k => route.path.startsWith('/' + k + '/')) ? { kind: route.path.split('/')[1], id: route.params.id } : null);
const ticketId = computed(() => route.path.startsWith('/tickets/') ? Number(route.params.id) : null);

onMounted(loadMe);
watch(() => session.me, (me) => { if (me) connectLive(); else disconnectLive(); });

const NAV = [
  ['cluster', '集群'], ['tickets', '需求池'], ['events', '采集流'], ['rooms', '雁鸣'], ['logs', '日志'], ['admin', '管理'], ['design', '设计'],
];
const navActive = (v) => route.path === '/' + v || route.path.startsWith('/' + v + '/') || (v === 'cluster' && ['worker', 'node', 'router'].some(k => route.path.startsWith('/' + k + '/'))) || (v === 'rooms' && route.path.startsWith('/room/'));
async function logout() { await api('/auth/logout', { method: 'POST' }); session.me = null; router.push('/cluster'); }
function goLogin() { window.location.href = '/auth/sso/login'; }
</script>

<template>
  <SkyCanvas :landing="landing" />
  <div class="hud-corner hud-tl"></div><div class="hud-corner hud-tr"></div>
  <div class="hud-corner hud-bl"></div><div class="hud-corner hud-br"></div>

  <div v-if="!session.checked" style="position:relative;z-index:2;min-height:100vh"></div>

  <div v-else-if="landing" id="landing">
    <div class="scan"></div>
    <div class="kicker">SSE MARKET · SUPER AGENT GRID</div>
    <h1><span>雁</span><span>阵</span></h1>
    <div class="sub">需求即命令 · 会话即工雁 · 账本即阵型</div>
    <button class="primary enter" id="enter-btn" @click="goLogin">接 入 调 度 台 →</button>
    <div class="hint">UniSSO 统一身份登录 · 集群实况登录后可见</div>
  </div>

  <div v-else id="app" style="display:block">
    <header>
      <div class="logo">雁阵 <span class="sub">YANZHEN // AGENT GRID</span></div>
      <nav>
        <button v-for="[v, label] in NAV" :key="v" v-show="v !== 'admin' || session.me.is_admin"
          :class="{ active: navActive(v) }" @click="router.push('/' + v)">{{ label }}</button>
      </nav>
      <div class="conn" :class="{ live: live.connected }"><span class="dot"></span><span>{{ live.connected ? 'LIVE' : 'RELINK' }}</span></div>
      <div class="userchip">
        <span>{{ session.me.name || session.me.email }}</span>
        <span class="admin" v-if="session.me.is_admin">ADMIN</span>
        <button @click="logout">退出</button>
      </div>
    </header>
    <main>
      <router-view v-slot="{ Component }">
        <component :is="Component" :key="route.matched[0]?.path" />
      </router-view>
    </main>
  </div>

  <EntityDrawer v-if="session.me && entity" :kind="entity.kind" :id="entity.id" @close="router.push('/cluster')" />
  <TicketDrawer v-if="session.me && ticketId" :id="ticketId" @close="router.push('/tickets')" />

  <div id="toasts"><div class="toast" v-for="t in toasts" :key="t.id">{{ t.msg }}</div></div>
</template>

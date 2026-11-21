import { createRouter, createWebHashHistory } from "vue-router";
import TodayView from "./views/TodayView.vue";
import AboutView from "./views/AboutView.vue";
import TicketsView from "./views/TicketsView.vue";
import SignalsView from "./views/SignalsView.vue";
import FleetView from "./views/FleetView.vue";
import WorkersView from "./views/WorkersView.vue";
import NodesView from "./views/NodesView.vue";
import HostView from "./views/HostView.vue";
import PodsView from "./views/PodsView.vue";
import EventsView from "./views/EventsView.vue";
import CallsView from "./views/CallsView.vue";
import PlatformView from "./views/PlatformView.vue";
import LogsView from "./views/LogsView.vue";

export const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    { path: "/", redirect: "/today" },
    { path: "/today", component: TodayView },
    { path: "/about", component: AboutView },
    { path: "/tickets", component: TicketsView },
    { path: "/tickets/:id", component: TicketsView },
    { path: "/signals", component: SignalsView },
    { path: "/fleet", component: FleetView },
    { path: "/workers", component: WorkersView },
    { path: "/workers/:id", component: WorkersView },
    { path: "/nodes", component: NodesView },
    { path: "/nodes/:id", component: NodesView },
    { path: "/host", component: HostView },
    { path: "/pods", component: PodsView },
    { path: "/events", component: EventsView },
    { path: "/calls", component: CallsView },
    { path: "/logs", component: LogsView },
    { path: "/platform", component: PlatformView },
  ],
});

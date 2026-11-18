// hash 路由：一视图一 URL，实体带 id —— 刷新/分享还原现场
import { createRouter, createWebHashHistory } from 'vue-router';

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/cluster' },
    { path: '/cluster', component: () => import('./views/ClusterView.vue') },
    { path: '/tickets', component: () => import('./views/TicketsView.vue') },
    { path: '/tickets/:id', component: () => import('./views/TicketsView.vue'), props: true },
    { path: '/events', component: () => import('./views/EventsView.vue') },
    { path: '/rooms', component: () => import('./views/RoomsView.vue') },
    { path: '/room/:id', component: () => import('./views/RoomsView.vue'), props: true },
    { path: '/logs', component: () => import('./views/LogsView.vue') },
    { path: '/admin', component: () => import('./views/AdminView.vue') },
    { path: '/design', component: () => import('./views/DesignView.vue') },
    { path: '/worker/:id', component: () => import('./views/ClusterView.vue'), props: true },
    { path: '/node/:id', component: () => import('./views/ClusterView.vue'), props: true },
    { path: '/router/:id', component: () => import('./views/ClusterView.vue'), props: true },
    { path: '/:pathMatch(.*)*', redirect: '/cluster' },
  ],
});

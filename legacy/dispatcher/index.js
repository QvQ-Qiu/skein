// 雁阵 · Super Agent 调度台 — 入口
// 一期形态：单进程 = 入口采集 + 头雁 router + controller + 面板，全部状态落账本（SQLite）。
import express from 'express';
import http from 'node:http';
import { join } from 'node:path';
import { config } from './config.js';
import { log } from './db.js';
import { mountPanel } from './panel-api.js';
import { attachController } from './controller.js';
import { startIngest } from './ingest.js';
import { startRouter } from './router.js';
import { startRouterPeer } from './router-peer.js';
import { mountAuth } from './auth.js';

const app = express();
app.disable('x-powered-by');
mountAuth(app);
app.use(express.static(join(config.root, 'public')));
mountPanel(app);

const server = http.createServer(app);
attachController(server);

server.listen(config.port, '0.0.0.0', () => {
  log('info', 'main', `雁阵调度台启动 :${config.port}（面板 http://127.0.0.1:${config.port}/）`);
  startIngest();
  startRouter();
  startRouterPeer();
});

process.on('unhandledRejection', (e) => log('error', 'main', `unhandledRejection: ${e?.message || e}`));
process.on('uncaughtException', (e) => {
  log('error', 'main', `uncaughtException: ${e?.message || e}`);
  console.error(e);
});

// 配置：读取项目根 .env（不依赖第三方 dotenv）
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const env = {};
try {
  for (const line of readFileSync(join(root, '.env'), 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i > 0) env[t.slice(0, i)] = t.slice(i + 1);
  }
} catch { /* .env 缺失时用进程环境变量兜底 */ }

export const config = {
  root,
  port: Number(env.PORT || process.env.PORT || 3030),
  llm: {
    baseUrl: env.LLM_BASE_URL,
    apiKey: env.LLM_API_KEY,
    model: env.LLM_MODEL || 'glm-5.3',
    fallbackModel: env.LLM_FALLBACK_MODEL || 'qwen-3.8-flash',
    zhipu: { baseUrl: env.ZHIPU_BASE_URL, apiKey: env.ZHIPU_API_KEY, model: env.ZHIPU_MODEL || 'glm-4.5-flash' },
    timeoutMs: 90_000,
  },
  forumDb: {
    host: env.FORUM_DB_HOST || '127.0.0.1',
    port: Number(env.FORUM_DB_PORT || 3506),
    user: env.FORUM_DB_USER,
    password: env.FORUM_DB_PASSWORD,
    database: env.FORUM_DB_NAME || 'ssemarket',
  },
  ingestIntervalMs: Number(env.INGEST_INTERVAL_MS || 60_000),
  panelPassword: env.PANEL_PASSWORD || '',
  agentToken: env.AGENT_TOKEN || '',
  unisso: {
    issuer: env.UNISSO_ISSUER || 'https://sso.ssemarket.cn',
    clientId: env.UNISSO_CLIENT_ID || '',
    clientSecret: env.UNISSO_CLIENT_SECRET || '',
    redirectUri: env.UNISSO_REDIRECT_URI || '',
    scopes: env.UNISSO_SCOPES || 'openid profile email',
  },
  adminSubs: (env.YZ_ADMIN_SUBS || '').split(',').map(s => s.trim()).filter(Boolean),
  adminEmails: (env.YZ_ADMIN_EMAILS || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean),
  routerId: env.ROUTER_ID || 'router-market-1',
  observes: (env.OBSERVES || 'market').split(',').map(s => s.trim()).filter(Boolean),
  leaseSeconds: 600,          // worker 任务租约：10 分钟无心跳续约即回收
  heartbeatSeconds: 15,
};

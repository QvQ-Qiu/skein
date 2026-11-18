// LLM 链：平台薄层 glm-5.3（主）→ qwen-3.8-flash（备）→ 智谱直连（兜底）。
// 与小秋 chat 同源同链；glm-5.3 强制思考，thinking 字段一律不传。
import { config } from './config.js';
import { log } from './db.js';

async function callOnce(baseUrl, apiKey, model, messages, { maxTokens = 1200, extra = {} } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), config.llm.timeoutMs);
  try {
    const res = await fetch(baseUrl.replace(/\/$/, '') + '/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({ model, messages, max_tokens: maxTokens, temperature: 0.2, ...extra }),
      signal: ctl.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
    const d = await res.json();
    const content = d.choices?.[0]?.message?.content;
    if (!content) throw new Error('empty content');
    return content;
  } finally {
    clearTimeout(timer);
  }
}

export async function chat(messages, opts = {}) {
  const legs = [
    { name: 'thin-primary', url: config.llm.baseUrl, key: config.llm.apiKey, model: config.llm.model, extra: {} },
    { name: 'thin-fallback', url: config.llm.baseUrl, key: config.llm.apiKey, model: config.llm.fallbackModel, extra: { chat_template_kwargs: { enable_thinking: false } } },
    { name: 'zhipu', url: config.llm.zhipu.baseUrl, key: config.llm.zhipu.apiKey, model: config.llm.zhipu.model, extra: { thinking: { type: 'disabled' } } },
  ];
  let lastErr;
  for (const leg of legs) {
    if (!leg.key || !leg.url) continue;
    try {
      const out = await callOnce(leg.url, leg.key, leg.model, messages, { ...opts, extra: leg.extra });
      return { content: out, leg: leg.name, model: leg.model };
    } catch (e) {
      lastErr = e;
      log('warn', 'llm', `${leg.name} 失败：${e.message}`);
    }
  }
  throw lastErr || new Error('no LLM endpoint configured');
}

/** 从模型输出里抠出第一个 JSON 对象（容忍 ```json 围栏与前后废话） */
export function extractJson(text) {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const cand = fenced ? fenced[1] : text;
  const start = cand.indexOf('{');
  if (start < 0) throw new Error('no JSON object in output');
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < cand.length; i++) {
    const c = cand[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === '\\') esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') inStr = true;
    else if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return JSON.parse(cand.slice(start, i + 1)); }
  }
  throw new Error('unbalanced JSON in output');
}

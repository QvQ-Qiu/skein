// 头雁 Router：对采集事件做扩展理解 + 意图分类，产出需求单（无 worker 时暂存 staged）。
// 多 router 防重：认领 = UPDATE events SET verdict_at=? WHERE id=? AND verdict_at IS NULL，
// 谁改成功谁分类；需求单按 (platform,title) 对未关闭单去重，同源事件幂等。
import { db, now, log } from './db.js';
import { chat, extractJson } from './llm.js';
import { emit } from './bus.js';
import { postBoard } from './router-peer.js';
import mysql from 'mysql2/promise';
import { config } from './config.js';

const PLATFORMS = {
  market: '集市主站 ssemarket.cn（论坛/账号/币/小说/简历/评分/下载等）',
  iwiki: 'iWiki 文档站 iwiki.ssemarket.cn（空间/文档/协作/表单/课程资源等）',
  sso: 'UniSSO 统一登录 sso.ssemarket.cn（登录/注册/授权/账号绑定）',
  applet: '小程序集市 applet.ssemarket.cn',
  xiaoqiu: 'AI 助手小秋（对话/生图/搜索/楼中楼回复）',
  join: '招募站 join.ssemarket.cn（岗位/报名）',
  cloud: '集市云部署平台 cloud.ssemarket.cn（组/容器/部署）',
  other: '其他或无法判断',
};

const SYSTEM_PROMPT = `你是「雁阵」super agent 系统的需求识别 router，负责从软工集市论坛的帖子/评论中识别平台需求。
平台清单：
${Object.entries(PLATFORMS).map(([k, v]) => `- ${k}: ${v}`).join('\n')}

判定规则：
1. 绝大多数帖子是学生日常聊天、课程讨论、生活分享 → kind=noise，is_req=false。
2. 只有明确针对上述平台的问题反馈(bug)或功能建议(feature)才算需求：is_req=true。
3. 对平台用法的疑问/求助 → kind=question，is_req=false（不是需求单）。
4. 抱怨但指向不明（如"学校泳池坏了"）→ noise。提到平台名或明显描述平台行为才算。
5. 【评论铁律】判定对象永远是评论自身的文本，不是所属帖子。评论只是附和、点赞、表情、"同问"、简短闲聊时，即使所属帖子含 bug/需求，也必须判 noise——帖子的需求由帖子事件自己建单。只有评论自身独立提出新的问题反馈或建议时才判 bug/feature。
6. kind 只能是 bug|feature|question|noise 之一；platform 只能是清单中的 key。
7. severity：P0=站点不可用/数据丢失；P1=核心功能损坏；P2=一般缺陷/体验问题；P3=建议优化。noise/question 一律 P3。
8. title 用一句简短中文概括需求（≤30字）；summary 一两句说明现象与期望；reason 一句话给出判定依据。
9. confidence 为 0~1 小数，拿不准就压低。
10. 【同义去重】若判为 bug/feature，对照用户消息末尾给出的「已有未关闭需求单」：实质是同一问题/同一建议（即使措辞不同）时，dup_of 填该单编号（如 YZ-0003），否则填 null。

只输出一个 JSON 对象，不要任何其他文字：
{"is_req":bool,"kind":"...","platform":"...","title":"...","summary":"...","severity":"P?","confidence":0.x,"reason":"...","dup_of":null}`;

/** 评论硬闸：纯附和/超短文本不送 LLM，直接 noise（防模型拿父帖内容替评论建单） */
const AGREEMENT_RE = /^[\s点赞👍🐮牛啊哈哈呵嗯哦同问+1顶沙发板凳马克码住收藏插眼蹲一个蹲\s\d.。，,！!？?~～…・*#@\p{Emoji_Presentation}]+$/u;
function commentShortCircuit(ev) {
  if (ev.channel !== 'market-comment') return null;
  const text = (ev.content || '').trim();
  if (text.length < 15 || AGREEMENT_RE.test(text)) {
    return { is_req: false, kind: 'noise', platform: 'other', title: '', summary: '', severity: 'P3', confidence: 0.99, reason: '评论为超短文本/纯附和，按评论铁律直接判噪音（硬闸）', dup_of: null, guard: 'short-circuit' };
  }
  return null;
}

function buildUserPrompt(ev, parent) {
  const parts = [];
  if (ev.channel === 'market-comment') {
    parts.push(`【评论】（判定对象=评论自身文本；所属帖子仅作上下文）`);
    if (parent) parts.push(`所属帖子标题：${parent.title}\n所属帖子正文（节选）：${(parent.content || '').slice(0, 600)}`);
    parts.push(`评论内容：${ev.content}`);
  } else {
    parts.push(`【帖子】板块：${ev.partition_name || '未知'}`);
    parts.push(`标题：${ev.title || '(无)'}`);
    parts.push(`正文：${(ev.content || '').slice(0, 1500)}`);
  }
  const open = db.prepare("SELECT code, title, kind, platform FROM tickets WHERE status NOT IN ('done','rejected') ORDER BY id DESC LIMIT 30").all();
  if (open.length) {
    parts.push(`\n已有未关闭需求单（同义去重用，dup_of 填编号或 null）：\n` +
      open.map(t => `- ${t.code} [${t.kind}/${t.platform}] ${t.title}`).join('\n'));
  }
  return parts.join('\n');
}

async function getParentPost(ev) {
  if (!ev.parent_id) return null;
  const local = db.prepare("SELECT title, content FROM events WHERE channel='market-post' AND source_id=?").get(ev.parent_id);
  if (local) return local;
  try {
    const conn = await mysql.createConnection({
      host: config.forumDb.host, port: config.forumDb.port, user: config.forumDb.user,
      password: config.forumDb.password, database: config.forumDb.database, connectTimeout: 8000,
    });
    const [rows] = await conn.query('SELECT title, ptext AS content FROM v_posts_public WHERE postID=?', [ev.parent_id]);
    await conn.end();
    return rows[0] || null;
  } catch { return null; }
}

const claimStmt = db.prepare('UPDATE events SET verdict_at=? WHERE id=? AND verdict_at IS NULL');
const saveVerdict = db.prepare('UPDATE events SET verdict=?, verdict_at=?, ticket_id=? WHERE id=?');

/* ---- 建单防重（router 之间不通信，靠共享账本）----
 * 三道保险：① 指纹唯一键（同平台+规范化标题，并发写也只有一个落账）
 *           ② 建单锁（同平台同一时刻只一个 router 在建，锁在账本里）
 *           ③ 锁内翻新鲜账本（LLM 对照最新未关闭单判同义）
 */
const normTitle = (s) => String(s || '').toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '');
const fingerprint = (v) => `${v.platform}|${normTitle(v.title)}`;
const LOCK_TTL_MS = 30_000;
function acquireCreateLock(platform, holder) {
  const until = new Date(Date.now() + LOCK_TTL_MS).toISOString();
  db.prepare('INSERT OR IGNORE INTO create_locks(platform,holder,until) VALUES(?,?,?)').run(platform, holder, until);
  return db.prepare('UPDATE create_locks SET holder=?, until=? WHERE platform=? AND (holder=? OR until < ?)')
    .run(holder, until, platform, holder, now()).changes === 1;
}
const releaseCreateLock = (platform, holder) =>
  db.prepare('DELETE FROM create_locks WHERE platform=? AND holder=?').run(platform, holder);

async function freshDupCheck(v) {
  const open = db.prepare("SELECT code, title, kind FROM tickets WHERE platform=? AND status NOT IN ('done','rejected') ORDER BY id DESC LIMIT 30").all(v.platform);
  if (!open.length) return null;
  try {
    const { content } = await chat([
      { role: 'system', content: '你是需求判重器。只输出一个 JSON：{"dup_of": null 或已有单编号}。实质同一问题/同一建议（措辞不同也算）才填编号，否则 null。' },
      { role: 'user', content: `新需求：[${v.kind}] ${v.title} — ${v.summary || ''}\n已有单：\n` + open.map(t => `- ${t.code} [${t.kind}] ${t.title}`).join('\n') },
    ], { maxTokens: 100 });
    return extractJson(content).dup_of || null;
  } catch { return null; }
}

export async function createTicket(ev, v) {
  const holder = `r${process.pid}-${Math.random().toString(36).slice(2, 6)}`;
  for (let i = 0; i < 40 && !acquireCreateLock(v.platform, holder); i++) await new Promise(r => setTimeout(r, 500));
  try {
    const key = fingerprint(v);
    const findDup = () =>
      db.prepare("SELECT id, code FROM tickets WHERE dedup_key=? AND status NOT IN ('done','rejected')").get(key) ||
      db.prepare("SELECT id, code FROM tickets WHERE platform=? AND title=? AND status NOT IN ('done','rejected') LIMIT 1").get(v.platform, v.title);
    const merge = (dup) => {
      db.prepare('UPDATE events SET ticket_id=? WHERE id=?').run(dup.id, ev.id);
      log('info', 'router', `事件 ${ev.id} 并入已有需求单 ${dup.code}（同题去重）`);
      return dup.id;
    };
    let dup = findDup();
    if (!dup && v.dup_of) dup = db.prepare("SELECT id, code FROM tickets WHERE code=? AND status NOT IN ('done','rejected')").get(String(v.dup_of).trim());
    if (!dup) {
      const code = await freshDupCheck(v); // 锁内翻新鲜账本
      if (code) dup = db.prepare("SELECT id, code FROM tickets WHERE code=? AND status NOT IN ('done','rejected')").get(code);
    }
    if (dup) return merge(dup);
    const ts = now();
    let id;
    try {
      const info = db.prepare(`INSERT INTO tickets
        (code, title, kind, platform, severity, summary, status, source_event_id, source_url, reason, confidence, dedup_key, created_at, updated_at)
        VALUES ('', ?, ?, ?, ?, ?, 'staged', ?, ?, ?, ?, ?, ?, ?)`)
        .run(v.title, v.kind, v.platform, v.severity || 'P3', v.summary || '', ev.id, ev.url, v.reason || '', v.confidence ?? 0.5, key, ts, ts);
      id = info.lastInsertRowid;
    } catch (e) {
      if (String(e.message).includes('UNIQUE')) { const d2 = findDup(); if (d2) return merge(d2); }
      throw e;
    }
    const code = 'YZ-' + String(id).padStart(4, '0');
    db.prepare('UPDATE tickets SET code=? WHERE id=?').run(code, id);
    db.prepare('UPDATE events SET ticket_id=? WHERE id=?').run(id, ev.id);
    log('info', 'router', `新需求单 ${code} [${v.kind}/${v.platform}/${v.severity}] ${v.title}`, { event_id: ev.id });
    emit('ticket', { id, code, title: v.title, status: 'staged', kind: v.kind, platform: v.platform, severity: v.severity });
    return id;
  } finally {
    releaseCreateLock(v.platform, holder);
  }
}

async function classifyOne(ev) {
  const parent = await getParentPost(ev);
  let v = commentShortCircuit(ev);
  let leg = 'guard', model = 'short-circuit';
  if (!v) {
    const r = await chat(
      [{ role: 'system', content: SYSTEM_PROMPT }, { role: 'user', content: buildUserPrompt(ev, parent) }],
      { maxTokens: 1500 });
    v = extractJson(r.content);
    leg = r.leg; model = r.model;
  }
  if (!['bug', 'feature', 'question', 'noise'].includes(v.kind)) v.kind = 'noise';
  if (!PLATFORMS[v.platform]) v.platform = 'other';
  v.is_req = v.is_req === true && (v.kind === 'bug' || v.kind === 'feature');
  // 评论建单时附上父帖快照，面板可展示完整上下文
  if (ev.channel === 'market-comment' && parent) {
    v.parent_title = parent.title || '';
    v.parent_content = (parent.content || '').slice(0, 800);
  }
  let ticketId = null;
  if (v.is_req) {
    ticketId = await createTicket(ev, v);
    // 跨观测域：本 router 看不到的平台 → 协作板喊话，请观测该域的 router 协作
    if (ticketId && !config.observes.includes(v.platform)) {
      const t = db.prepare('SELECT code FROM tickets WHERE id=?').get(ticketId);
      postBoard(ticketId, `需求 ${t?.code || ticketId}「${v.title}」落在 ${v.platform}，超出本 router 观测域（${config.observes.join(',')}）。请观测该域的 router 认领协作。`);
    }
  }
  saveVerdict.run(JSON.stringify({ ...v, leg, model }), now(), ticketId, ev.id);
  emit('verdict', { event_id: ev.id, verdict: v, ticket_id: ticketId });
  return v;
}

let running = false;
const failCounts = new Map();
export async function pumpRouter() {
  if (running) return;
  running = true;
  try {
    for (;;) {
      const ev = db.prepare('SELECT * FROM events WHERE verdict_at IS NULL ORDER BY id LIMIT 1').get();
      if (!ev) break;
      // 原子认领（多 router 时只有一个能抢到）
      if (claimStmt.run(now(), ev.id).changes !== 1) continue;
      try {
        const v = await classifyOne(ev);
        failCounts.delete(ev.id);
        log('info', 'router', `事件 ${ev.id}（${ev.channel}#${ev.source_id}）→ ${v.kind}/${v.platform} conf=${v.confidence}`, null);
      } catch (e) {
        const n = (failCounts.get(ev.id) || 0) + 1;
        failCounts.set(ev.id, n);
        if (n >= 3) {
          // 三次失败放弃：标记错误判定，不再重试（面板可见）
          saveVerdict.run(JSON.stringify({ is_req: false, kind: 'noise', platform: 'other', title: '', summary: '', severity: 'P3', confidence: 0, reason: `分类失败×3：${e.message}`.slice(0, 200), error: true }), now(), null, ev.id);
          failCounts.delete(ev.id);
          log('error', 'router', `事件 ${ev.id} 分类连续失败，已放弃：${e.message}`);
        } else {
          db.prepare('UPDATE events SET verdict_at=NULL WHERE id=?').run(ev.id);
          log('warn', 'router', `事件 ${ev.id} 分类失败（第${n}次）：${e.message}`);
          await new Promise(r => setTimeout(r, 15_000)); // LLM 故障时退避
        }
      }
    }
  } finally {
    running = false;
  }
}

let timer = null;
export function startRouter() {
  // 崩溃恢复：认领后未落判定的事件（进程中途被杀）重置回待分类
  const orphan = db.prepare('UPDATE events SET verdict_at=NULL WHERE verdict_at IS NOT NULL AND verdict IS NULL').run();
  if (orphan.changes) log('warn', 'router', `崩溃恢复：${orphan.changes} 条悬置事件重置待分类`);
  void pumpRouter();
  timer = setInterval(() => void pumpRouter(), 10_000);
  timer.unref?.();
  log('info', 'router', '头雁 router 已启动（分类 → 需求单暂存）');
}

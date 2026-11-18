// 需求入口 · 集市主站线：轮询论坛公开视图（新帖 + 新评论）→ events 账本。
// 只读账号，不碰主站写路径；水位线存 kv，重启续采。
// 多 router 场景：UNIQUE(channel, source_id) 保证重复采集只落一条。
import mysql from 'mysql2/promise';
import { config } from './config.js';
import { db, kvGet, kvSet, now, log } from './db.js';
import { emit } from './bus.js';

let pool = null;
function getPool() {
  if (!pool) {
    pool = mysql.createPool({
      host: config.forumDb.host, port: config.forumDb.port,
      user: config.forumDb.user, password: config.forumDb.password,
      database: config.forumDb.database,
      waitForConnections: true, connectionLimit: 2,
      connectTimeout: 8000,
    });
  }
  return pool;
}

const insertEvent = db.prepare(`INSERT OR IGNORE INTO events
  (channel, source_id, parent_id, author_alias, partition_name, title, content, source_time, url, fetched_at)
  VALUES (@channel, @source_id, @parent_id, @author_alias, @partition_name, @title, @content, @source_time, @url, @fetched_at)`);

const postUrl = (id) => `https://ssemarket.cn/postdetail/${id}`;

/** 首次运行回看 24h，之后按水位线增量 */
function initWatermark(key, table, idCol, timeCol) {
  if (kvGet(key) !== null) return;
  return (async () => {
    const [r] = await getPool().query(
      `SELECT COALESCE(MIN(${idCol}), 0) - 1 AS wm FROM ${table} WHERE ${timeCol} >= DATE_SUB(NOW(), INTERVAL 24 HOUR)`);
    kvSet(key, r[0].wm ?? 0);
    log('info', 'ingest', `${key} 首次水位线初始化（回看24h）`, { wm: r[0].wm });
  })();
}

async function pollPosts() {
  const wm = Number(kvGet('wm_market_post', '0'));
  const [rows] = await getPool().query(
    'SELECT postID, `partition`, title, ptext, post_time, author_alias, is_anonymous FROM v_posts_public WHERE postID > ? ORDER BY postID LIMIT 200', [wm]);
  let added = 0, maxId = wm;
  for (const r of rows) {
    const info = insertEvent.run({
      channel: 'market-post', source_id: String(r.postID), parent_id: null,
      author_alias: r.is_anonymous ? '匿名' : (r.author_alias || ''),
      partition_name: r.partition || '', title: r.title || '',
      content: (r.ptext || '').slice(0, 8000),
      source_time: String(r.post_time), url: postUrl(r.postID), fetched_at: now(),
    });
    if (info.changes) { added++; emit('event', { channel: 'market-post', source_id: String(r.postID), title: r.title }); }
    maxId = Math.max(maxId, r.postID);
  }
  if (maxId > wm) kvSet('wm_market_post', maxId);
  return added;
}

async function pollComments() {
  const wm = Number(kvGet('wm_market_comment', '0'));
  const [rows] = await getPool().query(
    'SELECT pcommentID, ptargetID, pctext, time, author_alias FROM v_pcomments_public WHERE pcommentID > ? ORDER BY pcommentID LIMIT 300', [wm]);
  let added = 0, maxId = wm;
  for (const r of rows) {
    const info = insertEvent.run({
      channel: 'market-comment', source_id: String(r.pcommentID), parent_id: String(r.ptargetID),
      author_alias: r.author_alias || '', partition_name: null, title: null,
      content: (r.pctext || '').slice(0, 4000),
      source_time: String(r.time), url: postUrl(r.ptargetID), fetched_at: now(),
    });
    if (info.changes) { added++; emit('event', { channel: 'market-comment', source_id: String(r.pcommentID), parent_id: String(r.ptargetID) }); }
    maxId = Math.max(maxId, r.pcommentID);
  }
  if (maxId > wm) kvSet('wm_market_comment', maxId);
  return added;
}

let polling = false;
export async function pollOnce() {
  if (polling) return { skipped: true };
  polling = true;
  try {
    await initWatermark('wm_market_post', 'v_posts_public', 'postID', 'post_time');
    await initWatermark('wm_market_comment', 'v_pcomments_public', 'pcommentID', 'time');
    const posts = await pollPosts();
    const comments = await pollComments();
    if (posts + comments > 0) log('info', 'ingest', `采集新事件：帖子 ${posts}，评论 ${comments}`);
    emit('ingest', { posts, comments });
    return { posts, comments };
  } catch (e) {
    log('error', 'ingest', `采集失败：${e.message}`);
    return { error: e.message };
  } finally {
    polling = false;
  }
}

let timer = null;
export function startIngest() {
  void pollOnce();
  timer = setInterval(() => void pollOnce(), config.ingestIntervalMs);
  timer.unref?.();
  log('info', 'ingest', `集市主站入口已启动，轮询间隔 ${config.ingestIntervalMs / 1000}s`);
}

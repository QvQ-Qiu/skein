// UniSSO 登录（Authorization Code + PKCE S256）+ 本应用自己的会话与角色。
// 身份以 sub 为准（不用 email 做主键）；管理员由 .env YZ_ADMIN_SUBS/EMAILS 决定（应用自有授权）。
import crypto from 'node:crypto';
import { db, now, log } from './db.js';
import { config } from './config.js';

db.exec(`CREATE TABLE IF NOT EXISTS yz_users (
  sub TEXT PRIMARY KEY, email TEXT, name TEXT, username TEXT, picture TEXT,
  is_admin INTEGER NOT NULL DEFAULT 0, internal INTEGER, created_at TEXT, last_login TEXT
)`);
db.exec(`CREATE TABLE IF NOT EXISTS yz_sessions (
  token_hash TEXT PRIMARY KEY, sub TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT
)`);
db.exec(`CREATE TABLE IF NOT EXISTS sso_pending (
  state TEXT PRIMARY KEY, verifier TEXT NOT NULL, created_at TEXT NOT NULL
)`);

const SESSION_TTL_MS = 7 * 24 * 3600 * 1000;
const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
const b64url = (buf) => buf.toString('base64url');

export function parseCookies(req) {
  const out = {};
  for (const part of (req.headers.cookie || '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

export function sessionUser(req) {
  const tok = parseCookies(req).yz_sid;
  if (!tok) return null;
  const s = db.prepare('SELECT * FROM yz_sessions WHERE token_hash=?').get(sha(tok));
  if (!s || s.expires_at < now()) return null;
  return db.prepare('SELECT * FROM yz_users WHERE sub=?').get(s.sub) || null;
}

const isAdminIdentity = (sub, email) =>
  (config.adminSubs || []).includes(sub) || (config.adminEmails || []).includes(String(email || '').toLowerCase());

export function mountAuth(app) {
  const S = config.unisso;
  app.get('/auth/sso/login', (_req, res) => {
    if (!S.clientId) return res.status(503).send('UniSSO 未配置');
    const state = b64url(crypto.randomBytes(16));
    const verifier = b64url(crypto.randomBytes(32));
    const challenge = b64url(crypto.createHash('sha256').update(verifier).digest());
    db.prepare('INSERT INTO sso_pending(state,verifier,created_at) VALUES(?,?,?)').run(state, verifier, now());
    db.prepare("DELETE FROM sso_pending WHERE created_at < datetime('now','-10 minutes')").run();
    const u = new URL(S.issuer + '/authorize');
    u.searchParams.set('response_type', 'code');
    u.searchParams.set('client_id', S.clientId);
    u.searchParams.set('redirect_uri', S.redirectUri);
    u.searchParams.set('scope', S.scopes);
    u.searchParams.set('state', state);
    u.searchParams.set('code_challenge', challenge);
    u.searchParams.set('code_challenge_method', 'S256');
    res.redirect(u.toString());
  });

  app.get('/auth/sso/callback', async (req, res) => {
    const { code, state, error } = req.query;
    if (error) return res.status(400).send('SSO 拒绝：' + error);
    const pend = db.prepare('SELECT * FROM sso_pending WHERE state=?').get(state || '');
    db.prepare('DELETE FROM sso_pending WHERE state=?').run(state || '');
    if (!pend || !code) return res.status(400).send('state 校验失败');
    try {
      const tokRes = await fetch(S.issuer + '/api/oauth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'authorization_code', code, redirect_uri: S.redirectUri,
          client_id: S.clientId, client_secret: S.clientSecret, code_verifier: pend.verifier,
        }),
      });
      if (!tokRes.ok) throw new Error('token 交换失败 ' + tokRes.status);
      const tok = await tokRes.json();
      const uiRes = await fetch(S.issuer + '/api/oauth/userinfo', { headers: { Authorization: `Bearer ${tok.access_token}` } });
      if (!uiRes.ok) throw new Error('userinfo 失败 ' + uiRes.status);
      const ui = await uiRes.json();
      const sub = ui.sub || ui.sso_sub || tok.sso_sub;
      if (!sub) throw new Error('userinfo 无 sub');
      const admin = isAdminIdentity(sub, ui.email) ? 1 : 0;
      const ts = now();
      db.prepare(`INSERT INTO yz_users(sub,email,name,username,picture,is_admin,internal,created_at,last_login)
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sub) DO UPDATE SET email=excluded.email, name=excluded.name, username=excluded.username,
          picture=excluded.picture, is_admin=excluded.is_admin, internal=excluded.internal, last_login=excluded.last_login`)
        .run(sub, ui.email || '', ui.name || ui.preferred_username || '', ui.preferred_username || '', ui.picture || '', admin, ui.internal ? 1 : 0, ts, ts);
      const sid = b64url(crypto.randomBytes(24));
      db.prepare('INSERT INTO yz_sessions(token_hash,sub,expires_at,created_at) VALUES(?,?,?,?)')
        .run(sha(sid), sub, new Date(Date.now() + SESSION_TTL_MS).toISOString(), ts);
      db.prepare("DELETE FROM yz_sessions WHERE expires_at < ?").run(now());
      res.setHeader('Set-Cookie', `yz_sid=${sid}; Path=/; Max-Age=${SESSION_TTL_MS / 1000}; HttpOnly; SameSite=Lax; Secure`);
      log('info', 'auth', `SSO 登录 ${ui.email || sub}${admin ? '（管理员）' : ''}`);
      res.redirect('/');
    } catch (e) {
      log('error', 'auth', `SSO 回调失败：${e.message}`);
      res.status(500).send('登录失败：' + e.message);
    }
  });

  app.get('/auth/me', (req, res) => {
    const u = sessionUser(req);
    if (!u) return res.status(401).json({ error: '未登录' });
    res.json({ sub: u.sub, email: u.email, name: u.name || u.username, picture: u.picture, is_admin: !!u.is_admin });
  });
  app.post('/auth/logout', (req, res) => {
    const tok = parseCookies(req).yz_sid;
    if (tok) db.prepare('DELETE FROM yz_sessions WHERE token_hash=?').run(sha(tok));
    res.setHeader('Set-Cookie', 'yz_sid=; Path=/; Max-Age=0');
    res.json({ ok: true });
  });
}

export function requireUser(req, res, next) {
  const u = sessionUser(req);
  if (!u) return res.status(401).json({ error: '未登录' });
  req.yzUser = u;
  next();
}
export function requireAdmin(req, res, next) {
  if (!req.yzUser?.is_admin) return res.status(403).json({ error: '仅管理员可操作' });
  next();
}

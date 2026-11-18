// 雁阵面板浏览器验收：登录 → 各视图 → 抽屉 → 设计文档 mermaid → 截图 + 控制台错误收集
import { chromium } from '/root/SSE_Market/iWiki/browser-e2e/node_modules/playwright-core/index.mjs';
import { readFileSync, mkdirSync } from 'node:fs';

const env = Object.fromEntries(readFileSync('/root/yanzhen/.env', 'utf8').split('\n').filter(l => l.includes('=') && !l.startsWith('#')).map(l => [l.split('=')[0], l.split('=').slice(1).join('=')]));
const OUT = '/root/yanzhen/data/acceptance';
mkdirSync(OUT, { recursive: true });
const errors = [];

const browser = await chromium.launch({ executablePath: '/opt/google/chrome/chrome', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
page.on('pageerror', e => errors.push('PAGEERROR: ' + String(e).slice(0, 200)));

const shot = async (name) => { await page.screenshot({ path: `${OUT}/${name}.png` }); console.log('📸', name); };
const wait = (ms) => new Promise(r => setTimeout(r, ms));

await page.goto('https://yanzhen.ssemarket.cn/', { waitUntil: 'networkidle' });
await wait(1000);
await shot('01-login');
await page.fill('#login-pwd', env.PANEL_PASSWORD);
await page.click('#login-btn');
await page.waitForSelector('#app', { state: 'visible', timeout: 10000 });
await wait(2500);
await shot('02-overview');
const stats = await page.$$eval('#stat-cards .stat', els => els.map(e => e.textContent.trim()));
console.log('统计卡:', stats.join(' | '));

// 需求池
await page.click('nav button[data-view="tickets"]');
await wait(1500);
const ticketCount = await page.$$eval('#ticket-rows tr', r => r.length);
console.log('需求单行数:', ticketCount);
await shot('03-tickets');
// 打开第一张单的抽屉
await page.click('#ticket-rows tr:first-child');
await page.waitForSelector('.drawer.open', { timeout: 5000 });
await wait(800);
await shot('04-ticket-drawer');
const drawerTitle = await page.$eval('.drawer h3', e => e.textContent);
console.log('抽屉:', drawerTitle);
await page.click('.drawer .close');
await wait(400);

// 采集流
await page.click('nav button[data-view="events"]');
await wait(1500);
const evCount = await page.$$eval('#event-rows tr', r => r.length);
console.log('采集流行数:', evCount);
await shot('05-events');

// 工雁（空态 + 接入说明）
await page.click('nav button[data-view="workers"]');
await wait(1200);
await shot('06-workers');

// 雁鸣
await page.click('nav button[data-view="rooms"]');
await wait(1000);
await shot('07-rooms');

// 日志
await page.click('nav button[data-view="logs"]');
await wait(1200);
await shot('08-logs');

// 设计文档（mermaid CDN 渲染）
await page.click('nav button[data-view="design"]');
await wait(6000);
const svgCount = await page.$$eval('.mermaid-box svg', els => els.length).catch(() => 0);
console.log('mermaid 渲染图数:', svgCount);
await shot('09-design');

// SSE 连接状态
const conn = await page.$eval('#conn', e => e.className + ' ' + e.textContent);
console.log('SSE:', conn);

console.log('控制台错误:', errors.length ? errors : '无');
await browser.close();
console.log('验收完成，截图 →', OUT);

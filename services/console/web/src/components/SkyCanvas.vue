<script setup>
// 星野 + 程序燕子 V 形编队：落地页横穿入场，应用内上方巡航
import { onMounted, onBeforeUnmount } from 'vue';
const props = defineProps({ landing: Boolean });
let canvas, ctx, raf, W, H, stars = [], t0 = performance.now();
const FLOCK = [{ ox: 0, oy: 0, s: 1.25, p: 0 }, ...Array.from({ length: 10 }, (_, i) => {
  const side = i % 2 ? 1 : -1, rank = Math.floor(i / 2) + 1;
  return { ox: -rank * 34, oy: side * rank * 22, s: 1.05 - rank * .04, p: Math.random() * 6.28 };
})];
function resize() {
  W = canvas.width = innerWidth * devicePixelRatio; H = canvas.height = innerHeight * devicePixelRatio;
  canvas.style.width = innerWidth + 'px'; canvas.style.height = innerHeight + 'px';
  stars = Array.from({ length: 150 }, () => ({ x: Math.random() * W, y: Math.random() * H, r: Math.random() * 1.4 + .3, p: Math.random() * 6.28, s: .5 + Math.random() * 1.5 }));
}
function swallow(x, y, ang, flap, size, alpha, hue) {
  ctx.save(); ctx.translate(x, y); ctx.rotate(ang); ctx.scale(size * devicePixelRatio, size * devicePixelRatio);
  ctx.globalAlpha = alpha; ctx.strokeStyle = hue; ctx.lineWidth = 1.5; ctx.lineCap = 'round';
  ctx.shadowColor = hue; ctx.shadowBlur = 9;
  const w = Math.sin(flap) * 7;
  ctx.beginPath();
  ctx.moveTo(7, 0); ctx.quadraticCurveTo(0, -1.6, -6, 0);
  ctx.moveTo(-6, 0); ctx.lineTo(-10, -2.4); ctx.moveTo(-6, 0); ctx.lineTo(-10, 2.4);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(1, 0); ctx.quadraticCurveTo(-2, -6 - w, -8, -9 - w * 1.4);
  ctx.moveTo(1, 0); ctx.quadraticCurveTo(-2, 6 + w, -8, 9 + w * 1.4);
  ctx.stroke(); ctx.restore();
}
function frame(now) {
  raf = requestAnimationFrame(frame);
  const t = (now - t0) / 1000;
  ctx.clearRect(0, 0, W, H);
  for (const s of stars) {
    ctx.globalAlpha = .25 + .5 * Math.abs(Math.sin(t * s.s + s.p));
    ctx.fillStyle = '#9fb4ff'; ctx.beginPath(); ctx.arc(s.x, s.y, s.r * devicePixelRatio, 0, 6.28); ctx.fill();
  }
  ctx.globalAlpha = 1;
  let lx, ly, ang;
  if (props.landing) {
    const c = (t % 26) / 26;
    lx = (-.12 + c * 1.24) * W; ly = H * (.34 + .07 * Math.sin(c * 6.28 * 1.5));
    ang = Math.atan2(.07 * 6.28 * 1.5 * Math.cos(c * 6.28 * 1.5) * H / 26, 1.24 * W / 26);
  } else {
    lx = W * (.72 + .16 * Math.cos(t * .11)); ly = H * (.16 + .07 * Math.sin(t * .17));
    ang = Math.atan2(.07 * .17 * Math.cos(t * .17), -.16 * .11 * Math.sin(t * .11));
  }
  const sc = props.landing ? 1.5 : .8, al = props.landing ? .95 : .4;
  for (const b of FLOCK) {
    const jx = Math.sin(t * 1.3 + b.p) * 3, jy = Math.cos(t * 1.7 + b.p) * 2.4;
    swallow(lx + (b.ox + jx) * sc * devicePixelRatio, ly + (b.oy + jy) * sc * devicePixelRatio, ang, t * 7 + b.p, b.s * sc, al, b.ox === 0 ? '#f5b942' : '#bfe9f5');
  }
}
onMounted(() => { canvas = document.getElementById('sky'); ctx = canvas.getContext('2d'); resize(); addEventListener('resize', resize); raf = requestAnimationFrame(frame); });
onBeforeUnmount(() => { cancelAnimationFrame(raf); removeEventListener('resize', resize); });
</script>
<template><canvas id="sky"></canvas></template>

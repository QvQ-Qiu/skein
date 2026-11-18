<script setup>
import { ref, onMounted } from 'vue';
const html = ref('加载中…');
onMounted(async () => {
  const md = await (await fetch('/api/design', { credentials: 'same-origin' })).text();
  try {
    const [{ marked }, mermaid] = await Promise.all([
      import('https://cdn.jsdelivr.net/npm/marked@12.0.2/lib/marked.esm.js'),
      import('https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.esm.min.mjs'),
    ]);
    mermaid.default.initialize({ startOnLoad: false, theme: 'dark', themeVariables: { background: '#0a1024', primaryColor: '#151d3a', primaryTextColor: '#dfe6ff', lineColor: '#4fd8eb', fontSize: '14px' } });
    const blocks = [];
    const pre = md.replace(/```mermaid\n([\s\S]*?)```/g, (_, code) => { blocks.push(code); return `<div class="mermaid-box" data-mmd="${blocks.length - 1}"></div>`; });
    html.value = marked.parse(pre);
    await new Promise(r => setTimeout(r, 0));
    for (let i = 0; i < blocks.length; i++) {
      const el = document.querySelector(`[data-mmd="${i}"]`);
      if (!el) continue;
      try { const { svg } = await mermaid.default.render('mmd' + i + '-' + Date.now(), blocks[i]); el.innerHTML = svg; }
      catch { el.innerHTML = `<pre class="code">${blocks[i].replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}</pre>`; }
    }
  } catch {
    html.value = `<p class="dim">渲染器加载失败，源码：</p><pre class="code">${md.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}</pre>`;
  }
});
</script>
<template><section class="view active"><div class="doc" v-html="html"></div></section></template>

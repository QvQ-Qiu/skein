import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
export default defineConfig({
  plugins: [vue()],
  build: { outDir: 'dist', assetsDir: 'assets', chunkSizeWarningLimit: 900 },
  server: { proxy: { '/api': 'http://127.0.0.1:3030', '/auth': 'http://127.0.0.1:3030', '/dl': 'http://127.0.0.1:3030' } },
});

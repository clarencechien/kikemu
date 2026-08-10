import { defineConfig } from 'vite';

// 本機開發:vite dev 跑前端,API 與 WS 轉給 wrangler dev(npm run dev:worker)
export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8787',
      '/ws': { target: 'ws://localhost:8787', ws: true },
    },
  },
});

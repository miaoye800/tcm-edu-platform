import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端地址可覆盖：实拍要验证"还没进主分支的代码"时，可以另起一个后端端口，
// 用 `QH_API_TARGET=http://127.0.0.1:8010 npm run dev`，不必去动正在跑的那个进程
// （那个进程可能属于别人的会话，杀不掉）。默认仍然是 8000。
const apiTarget = process.env.QH_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  cacheDir: '.vite-cache',
  server: {
    proxy: {
      '/health': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})

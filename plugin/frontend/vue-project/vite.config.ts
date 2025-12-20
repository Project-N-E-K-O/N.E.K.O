import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 代理所有插件服务器 API 请求
      '/plugin': {
        target: 'http://localhost:48916',
        changeOrigin: true,
        secure: false
      },
      '/plugins': {
        target: 'http://localhost:48916',
        changeOrigin: true,
        secure: false
      },
      '/server': {
        target: 'http://localhost:48916',
        changeOrigin: true,
        secure: false
      },
      '/health': {
        target: 'http://localhost:48916',
        changeOrigin: true,
        secure: false
      },
      '/available': {
        target: 'http://localhost:48916',
        changeOrigin: true,
        secure: false
      }
    }
  }
})

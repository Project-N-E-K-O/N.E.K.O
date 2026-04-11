import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

const BACKEND_TARGET = process.env.VITE_BACKEND_URL || 'http://localhost:48916'

// https://vite.dev/config/
export default defineConfig({
  base: '/ui/',
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
    fs: {
      // 允许访问父目录（更安全的替代方案，而不是完全禁用严格模式）
      // 这样可以访问项目根目录之外的必要文件，同时保持文件系统的安全防护
      allow: ['..']
    },
    proxy: {
      // 代理所有插件服务器 API 请求
      '/plugin/': {
        target: BACKEND_TARGET,
        changeOrigin: true,
        secure: false
      },
      // 只代理精确匹配 /plugins 的 API 请求（不带路径参数）
      // 使用 bypass 函数区分 API 请求和前端路由
      // 只代理带有 Accept: application/json 的请求（API 请求）
      '^/plugins$': {
        target: BACKEND_TARGET,
        changeOrigin: true,
        secure: false,
        bypass(req, res, options) {
          // 检查是否是 API 请求（通过 Accept 头判断）
          const acceptHeader = req.headers.accept || ''
          const method = req.method || 'GET'
          // 如果是 API 请求（包含 application/json），则代理
          // 或者是非 GET 请求（POST/PUT/DELETE 通常是 API 调用）
          if (acceptHeader.includes('application/json') || (method !== 'GET' && method !== 'HEAD')) {
            return null // 继续代理
          }
          // 否则返回原路径，让 Vite 处理（前端路由）
          return req.url
        }
      },
      '/server': {
        target: BACKEND_TARGET,
        changeOrigin: true,
        secure: false
      },
      '/health': {
        target: BACKEND_TARGET,
        changeOrigin: true,
        secure: false
      },
      '/available': {
        target: BACKEND_TARGET,
        changeOrigin: true,
        secure: false
      },
      // WebSocket 代理
      '/ws': {
        target: BACKEND_TARGET,
        changeOrigin: true,
        secure: false,
        ws: true, // 启用 WebSocket 代理
        configure: (proxy, _options) => {
          let suppressedErrorCount = 0
          // 处理 WebSocket 代理错误，避免在连接关闭后继续写入
          proxy.on('error', (err, _req, _res) => {
            // 忽略常见的 WebSocket 关闭错误
            if (err.message && (
              err.message.includes('socket has been ended') ||
              err.message.includes('ECONNRESET') ||
              err.message.includes('EPIPE')
            )) {
              suppressedErrorCount++
              if (process.env.DEBUG || suppressedErrorCount % 10 === 0) {
                console.debug(
                  `[Vite] WebSocket connection closed (suppressed ${suppressedErrorCount} times):`,
                  err.message
                )
              }
              return
            }
            console.error('[Vite] WebSocket proxy error:', err.message)
          })
          
          proxy.on('close', () => {
            // 连接关闭时的清理
          })
        }
      }
    }
  }
})

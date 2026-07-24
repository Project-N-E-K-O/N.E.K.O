import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolveConfiguredPort } from './port-config.js'

// Match config/_read_port_env precedence: NEKO_<NAME> then bare <NAME>.
const mainServerPort = resolveConfiguredPort('MAIN_SERVER_PORT', 48911)
const cardForgePort = process.env.NEKO_CARD_FORGE_PORT || '3001'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/card-forge/active-character': {
        target: `http://localhost:${mainServerPort}`,
        changeOrigin: true,
      },
      '/forge/facts': {
        target: `http://localhost:${cardForgePort}`,
        changeOrigin: true,
      },
      '/forge/card-story': {
        target: `http://localhost:${cardForgePort}`,
        changeOrigin: true,
      },
    },
  },
})

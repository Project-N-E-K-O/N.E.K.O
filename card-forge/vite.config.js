import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/card-forge/active-character': {
        target: 'http://localhost:48911',
        changeOrigin: true,
      },
      '/arena/forge-facts': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
      '/arena/forge-card-story': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
    },
  },
})

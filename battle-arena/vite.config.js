import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      allow: [
        __dirname,
        'F:/NEKO_bugfix/NekoBrawl/Gif_source',
      ],
    },
    proxy: {
      '/battle-arena/avatar': {
        target: 'http://localhost:48911',
        changeOrigin: true,
      },
      '/arena': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
    },
  },
})

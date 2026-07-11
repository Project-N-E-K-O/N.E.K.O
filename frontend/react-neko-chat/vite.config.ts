import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    // App.test.tsx 用 `import ... from './styles.css?raw'` 读取样式原文做断言。Vitest 默认把所有 CSS
    // 导入打桩成空串（提速），会让 `?raw` 也拿到空串；这里仅放行 styles.css 走真实处理，其余 CSS 仍打桩。
    css: {
      include: [/styles\.css/],
    },
  },
  build: {
    lib: {
      entry: 'src/export.ts',
      name: 'NekoChatWindow',
      formats: ['iife', 'es'],
      fileName: (format) => `neko-chat-window.${format}.js`,
    },
    outDir: '../../static/react/neko-chat',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        intro: 'var process = (typeof globalThis !== "undefined" && globalThis.process) ? globalThis.process : { env: { NODE_ENV: "production" } };',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5174,
  },
});

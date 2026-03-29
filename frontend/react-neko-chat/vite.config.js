import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
    plugins: [react()],
    build: {
        lib: {
            entry: 'src/export.ts',
            name: 'NekoChatWindow',
            formats: ['iife', 'es'],
            fileName: function (format) { return "neko-chat-window.".concat(format, ".js"); },
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

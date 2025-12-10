import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@project_neko/components": path.resolve(__dirname, "packages/components/index.ts"),
      "@project_neko/common": path.resolve(__dirname, "packages/common/index.ts"),
      "@project_neko/request": path.resolve(__dirname, "packages/request/index.ts")
    }
  },
  build: {
    lib: {
      entry: path.resolve(__dirname, "src/web/main.tsx"),
      name: "WebApp",
      fileName: () => "react_web.js",
      formats: ["es"]
    },
    outDir: path.resolve(__dirname, "../static/bundles"),
    emptyOutDir: false,
    sourcemap: true,
    rollupOptions: {
      external: []
    }
  }
});


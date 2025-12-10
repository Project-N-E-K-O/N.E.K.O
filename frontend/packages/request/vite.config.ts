import path from "path";
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      // Web 侧打包使用 index.web.ts，避免引入 React Native 依赖
      entry: path.resolve(__dirname, "index.web.ts"),
      name: "NEKORequest",
      formats: ["es", "umd"],
      fileName: (format) => (format === "es" ? "request.es.js" : "request.js")
    },
    // 输出到仓库根的 static/bundles
    outDir: path.resolve(__dirname, "../../../static/bundles"),
    emptyOutDir: false,
    sourcemap: true,
    rollupOptions: {
      // web bundle 不需要 RN 依赖
      external: ["@react-native-async-storage/async-storage"]
    }
  }
});


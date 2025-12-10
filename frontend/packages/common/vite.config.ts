import path from "path";
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: path.resolve(__dirname, "index.ts"),
      name: "NEKOCommon",
      formats: ["es", "umd"],
      fileName: (format) => (format === "es" ? "common.es.js" : "common.js")
    },
    // 输出到仓库根的 static/bundles
    outDir: path.resolve(__dirname, "../../../static/bundles"),
    emptyOutDir: false,
    sourcemap: true,
    rollupOptions: {
      external: []
    }
  }
});


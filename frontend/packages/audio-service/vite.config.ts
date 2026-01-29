import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const isProduction = mode === "production";

  return {
    define: {
      "import.meta.env.MODE": JSON.stringify(mode),
      "import.meta.env.NODE_ENV": JSON.stringify(isProduction ? "production" : "development"),
      __NEKO_VITE_MODE__: JSON.stringify(mode),
      __NEKO_VITE_NODE_ENV__: JSON.stringify(isProduction ? "production" : "development"),
    },
    esbuild: { drop: [] },
    build: {
      lib: {
        // Web 侧打包使用 index.web.ts，避免引入 RN 专用入口
        entry: path.resolve(__dirname, "index.web.ts"),
        // UMD 全局名：ProjectNekoAudioService
        name: "ProjectNekoAudioService",
        formats: ["es", "umd"],
        fileName: (format) => (format === "es" ? "audio-service.es.js" : "audio-service.js"),
      },
      outDir: path.resolve(__dirname, "../../../static/bundles"),
      emptyOutDir: false,
      sourcemap: isProduction ? false : true,
      minify: isProduction ? "esbuild" : false,
      rollupOptions: {
        external: [],
      },
    },
  };
});


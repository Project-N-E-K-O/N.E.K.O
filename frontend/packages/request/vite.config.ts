import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  return {
    // 注入环境变量到代码中
    define: {
      // 注入构建模式，用于判断是否启用日志
      "import.meta.env.MODE": JSON.stringify(mode),
      "import.meta.env.NODE_ENV": JSON.stringify(mode === "production" ? "production" : "development"),
      // 同时注入不依赖 import.meta 的常量，便于跨端复用（Metro 可能无法解析 import.meta）
      __NEKO_VITE_MODE__: JSON.stringify(mode),
      __NEKO_VITE_NODE_ENV__: JSON.stringify(mode === "production" ? "production" : "development")
    },
    // 配置 esbuild，确保不移除 console.log
    esbuild: {
      // 不移除任何 console 语句（默认就是如此，但明确配置）
      drop: []
    },
    build: {
      lib: {
        // Web 侧打包使用 index.web.ts，避免引入 React Native 依赖
        entry: path.resolve(__dirname, "index.web.ts"),
        // UMD 全局名遵循包名 @project_neko/request -> ProjectNekoRequest
        name: "ProjectNekoRequest",
        formats: ["es", "umd"],
        fileName: (format) => (format === "es" ? "request.es.js" : "request.js")
      },
      // 输出到仓库根的 static/bundles
      outDir: path.resolve(__dirname, "../../../static/bundles"),
      emptyOutDir: false,
      sourcemap: mode === 'production' ? false : true, // 开发模式生成 sourcemap，生产模式不生成
      // 使用 esbuild 压缩（生产模式压缩，开发模式不压缩，确保保留 console.log）
      minify: mode === 'production' ? "esbuild" : false,
      rollupOptions: {
        // web bundle 不需要 RN 依赖
        external: ["@react-native-async-storage/async-storage"]
      }
    }
  };
});


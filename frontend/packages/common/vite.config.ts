import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const isProduction = mode === 'production';
  
  return {
    build: {
      lib: {
        entry: path.resolve(__dirname, "index.ts"),
        // UMD 全局名遵循包名 @project_neko/common -> ProjectNekoCommon
        name: "ProjectNekoCommon",
        formats: ["es", "umd"],
        fileName: (format) => (format === "es" ? "common.es.js" : "common.js")
      },
      // 输出到仓库根的 static/bundles
      outDir: path.resolve(__dirname, "../../../static/bundles"),
      emptyOutDir: false,
      sourcemap: isProduction ? false : true, // 开发模式生成 sourcemap，生产模式不生成
      minify: isProduction ? "esbuild" : false, // 生产模式压缩，开发模式不压缩
      rollupOptions: {
        external: []
      }
    }
  };
});


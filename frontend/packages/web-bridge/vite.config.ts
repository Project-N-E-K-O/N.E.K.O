import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const isProduction = mode === 'production';
  
  return {
    build: {
      lib: {
        entry: path.resolve(__dirname, "src/index.ts"),
        // UMD 全局名遵循包名 @project_neko/web-bridge -> ProjectNekoBridge
        name: "ProjectNekoBridge",
        formats: ["es", "umd"],
        fileName: (format) => (format === "es" ? "web-bridge.es.js" : "web-bridge.js")
      },
      outDir: path.resolve(__dirname, "../../../static/bundles"),
      emptyOutDir: false,
      sourcemap: isProduction ? false : true, // 开发模式生成 sourcemap，生产模式不生成
      minify: isProduction ? "esbuild" : false, // 生产模式压缩，开发模式不压缩
      rollupOptions: {
        external: ["@project_neko/request", "@project_neko/components", "react", "react-dom"],
        output: {
          globals: {
            "@project_neko/request": "ProjectNekoRequest",
            "@project_neko/components": "ProjectNekoComponents",
            react: "React",
            "react-dom": "ReactDOM"
          }
        }
      }
    }
  };
});


import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const isProduction = mode === 'production';
  
  return {
    define: {
      "process.env.NODE_ENV": JSON.stringify(isProduction ? "production" : "development"),
      process: JSON.stringify({ env: { NODE_ENV: isProduction ? "production" : "development" } })
    },
    // 组件库 UMD 不使用 React 插件转换，改用经典 JSX（esbuild 配置）
    plugins: [],
    build: {
      lib: {
        entry: path.resolve(__dirname, "index.ts"),
        // UMD 全局名遵循包名 @project_neko/components -> ProjectNekoComponents
        name: "ProjectNekoComponents",
        formats: ["es", "umd"],
        fileName: (format) => (format === "es" ? "components.es.js" : "components.js")
      },
      // 输出到仓库根的 static/bundles
      outDir: path.resolve(__dirname, "../../../static/bundles"),
      emptyOutDir: false,
      cssCodeSplit: false,
      sourcemap: isProduction ? false : true, // 开发模式生成 sourcemap，生产模式不生成
      minify: isProduction ? "esbuild" : false, // 生产模式压缩，开发模式不压缩
      // 使用经典 JSX 运行时，保证 UMD 在浏览器中与 React UMD 兼容
      esbuild: {
        jsx: "transform",
        jsxFactory: "React.createElement",
        jsxFragment: "React.Fragment"
      },
      rollupOptions: {
        external: ["react", "react-dom"],
        output: {
          globals: {
            react: "React",
            "react-dom": "ReactDOM"
          },
          assetFileNames: (assetInfo) => {
            if (assetInfo.name?.endsWith(".css")) {
              return "components.css";
            }
            return assetInfo.name || "[name][extname]";
          }
        }
      }
    }
  };
});


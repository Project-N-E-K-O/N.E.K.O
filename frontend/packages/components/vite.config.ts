import path from "path";
import { defineConfig } from "vite";

export default defineConfig({
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
    process: JSON.stringify({ env: { NODE_ENV: "production" } })
  },
  // 组件库 UMD 不使用 React 插件转换，改用经典 JSX（esbuild 配置）
  plugins: [],
  build: {
    lib: {
      entry: path.resolve(__dirname, "index.ts"),
      name: "NEKOComponents",
      formats: ["es", "umd"],
      fileName: (format) => (format === "es" ? "components.es.js" : "components.js")
    },
    // 输出到仓库根的 static/bundles
    outDir: path.resolve(__dirname, "../../../static/bundles"),
    emptyOutDir: false,
    cssCodeSplit: false,
    sourcemap: true,
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
});


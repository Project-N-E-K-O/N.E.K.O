import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const trimTrailingSlash = (url?: string): string => (url ? url.replace(/\/+$/, "") : "");

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiBase = trimTrailingSlash(env.VITE_API_BASE_URL) || "http://localhost:48911";
  const staticBase = trimTrailingSlash(env.VITE_STATIC_SERVER_URL) || apiBase;

  return {
    plugins: [react()],
    // 已有静态资源直接放在 ../static，由上层静态服务器提供，这里不再复制到 bundles，避免重复打包
    publicDir: false,
    resolve: {
      // 注意：alias 匹配有先后顺序；更具体的路径必须放在更泛的路径之前，
      // 否则 "@project_neko/audio-service" 可能会先匹配 "@project_neko/audio-service/web" 导致解析到 index.ts/web（不存在）。
      alias: [
        { find: "@project_neko/audio-service/web", replacement: path.resolve(__dirname, "packages/audio-service/index.web.ts") },
        { find: "@project_neko/audio-service", replacement: path.resolve(__dirname, "packages/audio-service/index.ts") },
        { find: "@project_neko/components", replacement: path.resolve(__dirname, "packages/components/index.ts") },
        { find: "@project_neko/common", replacement: path.resolve(__dirname, "packages/common/index.ts") },
        { find: "@project_neko/request", replacement: path.resolve(__dirname, "packages/request/index.ts") },
        { find: "@project_neko/realtime", replacement: path.resolve(__dirname, "packages/realtime/index.ts") },
      ],
    },
    server: {
      fs: {
        // 允许访问上层的 static 目录，以便加载 /static/icons 资源
        allow: [path.resolve(__dirname, "..")]
      },
      // 开发态直接转发 /static 与 /icons，避免 Vite 将请求回退到首页
      proxy: {
        "/static": {
          target: staticBase,
          changeOrigin: true
        },
        "/icons": {
          target: staticBase,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/icons/, "/static/icons")
        }
      }
    },
    build: {
      lib: {
        entry: path.resolve(__dirname, "src/web/main.tsx"),
        name: "WebApp",
        fileName: () => "react_web.js",
        formats: ["es"]
      },
      // WebApp 仅供开发/调试，产物不再写入 static/bundles，避免与静态资源混淆
      outDir: path.resolve(__dirname, "dist/webapp"),
      emptyOutDir: true,
      sourcemap: true,
      rollupOptions: {
        // 仅保留业务代码，React 相关依赖由外部全局或 CDN 提供
        external: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime"],
        output: {
          globals: {
            react: "React",
            "react-dom": "ReactDOM"
          }
        }
      }
    }
  };
});


import { defineConfig } from "vite";
import { resolve } from "path";
import tsconfigPaths from "vite-tsconfig-paths";
import type { Plugin } from "vite";

// 插件：处理 process.env 和外部依赖
function rewriteExternalImports(): Plugin {
  return {
    name: "rewrite-external-imports",
    generateBundle(options, bundle) {
      // 处理 JS 代码
      for (const fileName in bundle) {
        const chunk = bundle[fileName];
        if (chunk.type === "chunk" && chunk.code) {
          // 处理 process.env.NODE_ENV
          chunk.code = chunk.code.replace(
            /process\.env\.NODE_ENV/g,
            '"production"'
          );
          // 处理 process.env 的其他引用
          chunk.code = chunk.code.replace(
            /process\.env(?!\.)/g,
            '({ NODE_ENV: "production" })'
          );
        }
      }
    },
  };
}

export default defineConfig({
  plugins: [
    tsconfigPaths(), // 解析 TypeScript 路径别名（如 @xiao8/request）
    rewriteExternalImports(),
  ],
  define: {
    "process.env.NODE_ENV": '"production"',
  },
  build: {
    lib: {
      entry: resolve(__dirname, "app/api/request.global.ts"),
      name: "RequestGlobal",
      formats: ["es"],
      fileName: () => "request.global.js",
    },
    rollupOptions: {
      // 不将 axios 和 axios-auth-refresh 设为 external，打包进去
      // 这样可以在静态 HTML 中独立使用，无需额外依赖
      output: {
        format: "es",
        exports: "named",
        // 内联所有依赖
        inlineDynamicImports: true,
      },
    },
    // 压缩代码
    minify: "esbuild", // 使用 esbuild，更快且配置更简单
    outDir: "build/components",
    emptyOutDir: false, // 不清空目录，保留其他组件
  },
});


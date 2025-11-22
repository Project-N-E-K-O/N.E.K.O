import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve, join } from "path";
import { readFileSync, writeFileSync, existsSync, unlinkSync } from "fs";
import type { Plugin } from "vite";

// 插件：重写外部依赖的导入路径为 CDN URL，并处理 process.env
function rewriteExternalImports(): Plugin {
  
  return {
    name: "rewrite-external-imports",
    generateBundle(options, bundle) {
      // 处理 JS 代码
      for (const fileName in bundle) {
        const chunk = bundle[fileName];
        if (chunk.type === "chunk" && chunk.code) {
          // 将 react 和 react-dom 的导入重写为 CDN URL
          // 处理各种导入格式：import ... from "react" 或 import ... from 'react'
          chunk.code = chunk.code.replace(
            /from\s+["']react["']/g,
            'from "https://esm.sh/react@19"'
          );
          chunk.code = chunk.code.replace(
            /from\s+["']react-dom["']/g,
            'from "https://esm.sh/react-dom@19"'
          );
          chunk.code = chunk.code.replace(
            /from\s+["']react-dom\/client["']/g,
            'from "https://esm.sh/react-dom@19/client"'
          );
          // 处理 import() 动态导入
          chunk.code = chunk.code.replace(
            /import\(["']react["']\)/g,
            'import("https://esm.sh/react@19")'
          );
          chunk.code = chunk.code.replace(
            /import\(["']react-dom["']\)/g,
            'import("https://esm.sh/react-dom@19")'
          );
          // 处理 process.env.NODE_ENV - 替换为字符串字面量 "production"
          // 因为这是生产构建
          chunk.code = chunk.code.replace(
            /process\.env\.NODE_ENV/g,
            '"production"'
          );
          // 处理 process.env 的其他引用（如果存在）
          chunk.code = chunk.code.replace(
            /process\.env(?!\.)/g,
            '({ NODE_ENV: "production" })'
          );
        }
      }
    },
    writeBundle(options) {
      // 在文件写入后，读取 CSS 文件并注入到 JS 文件中
      const outDir = options.dir || "build/components";
      
      // 直接从文件系统查找所有 CSS 文件
      // 尝试已知的可能文件名（Vite 通常使用项目名作为 CSS 文件名）
      const cssFiles: string[] = [];
      const possibleCssFiles = ["react_web.css", "ExampleButton.css", "style.css"];
      for (const cssFile of possibleCssFiles) {
        const cssPath = join(outDir, cssFile);
        if (existsSync(cssPath)) {
          cssFiles.push(cssFile);
        }
      }
      
      // 读取 CSS 内容
      let cssContent = "";
      for (const cssFile of cssFiles) {
        const cssPath = join(outDir, cssFile);
        if (existsSync(cssPath)) {
          const content = readFileSync(cssPath, "utf-8");
          cssContent += content + "\n";
          console.log(`📦 读取 CSS 文件: ${cssFile} (${content.length} 字符)`);
          // 删除 CSS 文件
          unlinkSync(cssPath);
        }
      }
      
      // 如果有 CSS 内容，注入到 JS 文件中
      if (cssContent) {
        const jsFile = "ExampleButton.js";
        const jsPath = join(outDir, jsFile);
        if (existsSync(jsPath)) {
          const jsContent = readFileSync(jsPath, "utf-8");
          const styleId = "example-button-styles";
          const injectCSS = `// 注入 Tailwind CSS 样式
(function() {
  if (document.getElementById('${styleId}')) return;
  const style = document.createElement('style');
  style.id = '${styleId}';
  style.textContent = ${JSON.stringify(cssContent)};
  document.head.appendChild(style);
})();
`;
          const newContent = injectCSS + jsContent;
          writeFileSync(jsPath, newContent, "utf-8");
          console.log(`✅ 已注入 CSS 到 ${jsFile}，CSS 长度: ${cssContent.length} 字符`);
        }
      } else {
        console.warn("⚠️  未找到 CSS 文件");
      }
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    rewriteExternalImports(),
  ],
  define: {
    "process.env.NODE_ENV": '"production"',
  },
  build: {
    lib: {
      entry: resolve(__dirname, "app/components/ExampleButton.tsx"),
      name: "ExampleButton",
      formats: ["es"],
      fileName: () => "ExampleButton.js",
    },
    rollupOptions: {
      external: ["react", "react-dom", "react-dom/client"],
      output: {
        // ES module 格式
        format: "es",
        // 确保导出名称正确
        exports: "named",
      },
    },
    cssCodeSplit: false,
    // 生成 CSS 文件（插件会将其内联到 JS 中）
    cssMinify: true,
    outDir: "build/components",
    // 确保生成 CSS 文件（如果需要单独引入）
    emptyOutDir: true,
  },
});


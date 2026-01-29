import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// 从 frontend/vendor/react 复制到仓库根 static/bundles
const srcDir = path.resolve(__dirname, "../vendor/react");
const distDir = path.resolve(__dirname, "../../static/bundles");

const files = [
  "react.production.min.js",
  "react-dom.production.min.js"
];

try {
  if (!fs.existsSync(distDir)) {
    fs.mkdirSync(distDir, { recursive: true });
  }

  files.forEach((file) => {
    const src = path.join(srcDir, file);
    const dest = path.join(distDir, file);
    if (!fs.existsSync(src)) {
      throw new Error(`missing source: ${src}`);
    }
    fs.copyFileSync(src, dest);
    console.log(`[copy-react-umd] copied ${src} -> ${dest}`);
  });
} catch (err) {
  console.error("[copy-react-umd] failed:", err);
  process.exit(1);
}


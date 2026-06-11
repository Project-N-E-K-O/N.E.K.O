import { readdirSync, copyFileSync, statSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "static", "react", "neko-chat");
const assetsDir = join(root, "assets");

// 之前用 .find() 取首个 style-*.css，但 readdirSync 默认按字母排序，
// 旧 hash（如 BOci1Tpq）会先于新 hash（erkt3nz8），导致同步到旧文件。
// 改为按 mtime 取最新的；理想情况下旧 hash 应在 build 前清空。
const candidates = readdirSync(assetsDir)
  .filter((f) => f.startsWith("style-") && f.endsWith(".css"))
  .map((name) => ({ name, mtimeMs: statSync(join(assetsDir, name)).mtimeMs }))
  .sort((a, b) => b.mtimeMs - a.mtimeMs);

if (candidates.length > 0) {
  const file = candidates[0].name;
  copyFileSync(join(assetsDir, file), join(root, "neko-chat-window.css"));
  console.log(`[sync-css] copied ${file} -> neko-chat-window.css`);
} else {
  console.warn("[sync-css] no style-*.css found in assets/");
}

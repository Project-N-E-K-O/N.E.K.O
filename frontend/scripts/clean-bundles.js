import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// 清理仓库根的 static/bundles
const target = path.resolve(__dirname, "../../static/bundles");

try {
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(target, { recursive: true });
  console.log(`[clean-bundles] cleaned: ${target}`);
} catch (err) {
  console.error("[clean-bundles] failed:", err);
  process.exit(1);
}


import { readdirSync, copyFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "static", "react", "neko-chat");
const assetsDir = join(root, "assets");

const file = readdirSync(assetsDir).find((f) => f.startsWith("style-") && f.endsWith(".css"));
if (file) {
  copyFileSync(join(assetsDir, file), join(root, "neko-chat-window.css"));
  console.log(`[sync-css] copied ${file} -> neko-chat-window.css`);
} else {
  console.warn("[sync-css] no style-*.css found in assets/");
}

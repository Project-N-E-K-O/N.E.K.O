# 前提条件

| Tool | Source development requirement | Source |
| --- | --- | --- |
| Python | exactly 3.11.x | `pyproject.toml` |
| uv | current compatible release | `uv.lock` / workflows |
| Git | maintained release | repository operations |
| Node.js + npm | plugin manager: `^20.19.0 || >=22.12.0` | package.json |

Python dependencies は `uv sync`。手動 `pip install -r requirements.txt` を project install flow にしないでください。Frontend/docs は lockfile と `npm ci` を使います。

機能により Docker Engine + Compose、Steam environment、platform packaging dependencies、frontend／e2e test 用の Playwright Chromium（`uv run playwright install chromium`）、または real provider test credentials が必要です。

Agent のブラウザー操作には、このマシンにインストールされている Chromium 系ブラウザー（Chrome、Edge、Brave、Chromium のいずれか）を使います。アプリケーションにブラウザーは同梱されず、ダウンロードもされません。ブラウザーがない場合、ブラウザー操作は「使えるブラウザーが見つからない」と報告します。アプリケーションを動かすだけなら、Playwright でインストールするものはありません。

Current provider data に free route があっても availability/limits は external state で、development prerequisite や documentation promise ではありません。Windows long paths を有効にし、checkout を writable にしてください。

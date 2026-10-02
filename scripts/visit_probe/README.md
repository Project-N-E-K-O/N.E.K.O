# visit_probe — 猫娘串门 T1~T5 实测脚本

不是产品代码。用于在真实 Electron Pet 窗里复现 `docs/design/visit-infrastructure-t1-t5.md` 的结果。仅支持 Windows。

| 文件 | 作用 |
| --- | --- |
| `transport.html` / `transport.js` | iframe 子文档：T1 探针、2D / WebGL 打包、环回 RTCPeerConnection + `getStats`、透明 WebGL 解包显示 |
| `parent.js` | 经 CDP 注入 Pet 页：建 iframe、postrender 钩子（两道守卫 + 分数累加器 + 同步 `sink.onFrame`）、帧率模式切换 |
| `drive.mjs` | CDP 驱动，阶段 `env t1 t2 t5 t3 t4 trace` |
| `osprobe.py` | 区域截图、`SendInput` 移动鼠标、`WindowFromPoint` 命中判定、透明 / 合成误差计算 |
| `backdrop.ps1` | T3/T4 用的固定花纹背板窗口（不置顶，位于普通窗口之上、Pet 之下） |
| `results/{direct,compat}/results.json` | 2026-10-02 实测原始数据 |

## 步骤

1. 主仓库：把 `transport.html`、`transport.js` 临时拷到 `static/_visit_probe/`，然后 `uv run python launcher.py`。
2. 壳仓库（N.E.K.O.-PC）：`npx electron-forge start -- --remote-debugging-port=9222`。要测直通模式，设置 `NEKO_USER_DATA_DIR` 指向一个临时目录，把 `core_config.txt` 的 `compatibilityMode` 设为 `false`。
3. `node drive.mjs --label direct --out <dir> --shell <壳仓库路径> --repo <主仓库路径> env t1 t2 t5 t3 t4`（`--shell` / `--repo` 必填，也可用环境变量 `VISIT_PROBE_SHELL_DIR` / `VISIT_PROBE_REPO_DIR`）。
4. T3/T4 会移动鼠标、弹出背板窗口，期间不要操作电脑。截图只保存测试区域（外扩 40 px），不落全屏图。
5. 测完删除 `static/_visit_probe/`。

注意：T1 的正对照会让 Pet 主连接被 `PetWebSocket` 劫持，脚本随后会自动重载 Pet 与 Chat 窗。

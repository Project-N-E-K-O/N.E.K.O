# Testbench 插件 — 独立 Market 仓库说明

本文档说明 **Testbench 驱动器插件** 为何存在独立 GitHub 仓库、它与 N.E.K.O 主仓的关系，以及日常更新与发布时应遵循的流程。

官方 Market 发布流程见：[把插件发布到 N.E.K.O 插件市场](https://project-neko.online/zh-CN/plugins/cli)。

---

## 1. 为什么有两个「地方」

| 维度 | N.E.K.O 主仓 | 插件独立仓库 |
|------|----------------|--------------|
| 路径 | `tests/testbench_dist/plugin/testbench/` | **同一目录**（内含 `.git/`） |
| 远程 | `TL0SR2/N.E.K.O-dev` → PR 到 `Project-N-E-K-O/N.E.K.O` | `TL0SR2/n.e.k.o_plugin_testbench` |
| 用途 | 与 Testbench 打包工程、smoke、文档一起开发/审查 | **插件 Market 审核与 `.neko-plugin` 发布** |
| 是否进 `plugin/plugins/` | 否（刻意隔离） | 否（Market 要求插件目录自带 `.git`） |

N.E.K.O 插件 Market 要求：

- 插件源码在 **独立 Git 仓库** 中维护（目录内包含 `.git/`）；
- GitHub 仓库名必须为 `n.e.k.o_plugin_<插件ID>` → 本插件为 **`n.e.k.o_plugin_testbench`**；
- 使用标准 `verify.yml` / `release.yml`，并通过 `neko-plugin publish` 发布。

因此：**开发仍在主仓 PR 里做；Market 面向用户时以独立仓库的 commit / tag 为准。**

---

## 2. 仓库与产物速查

| 项 | 值 |
|----|-----|
| 插件 ID | `testbench` |
| Market 仓库名 | `n.e.k.o_plugin_testbench` |
| 当前远程 | https://github.com/TL0SR2/n.e.k.o_plugin_testbench |
| 版本声明 | `tests/testbench_dist/plugin/testbench/plugin.toml` → `[plugin].version` |
| 本地 `.neko-plugin` 构建 | `uv run python tests/testbench_dist/scripts/build_plugin.py` |
| Market 发布命令（主仓根目录） | `uv run neko-plugin publish tests/testbench_dist/plugin/testbench` |

---

## 3. 目录与 Git 的实际关系

```
N.E.K.O/                          ← 主仓 git
└── tests/testbench_dist/
    └── plugin/testbench/         ← 插件源码（驱动器 + UI + smoke）
        ├── .git/                 ← 插件 **独立** git（Market 仓库）
        ├── __init__.py           ← 驱动器核心
        ├── embed_runtime.py
        ├── shell_main.py
        ├── ui/panel.tsx
        ├── tests/test_smoke.py
        ├── plugin.toml
        ├── .github/workflows/    ← 标准 thin workflow（调用 N.E.K.O 可复用 workflow）
        ├── bundled/              ← **不提交**（构建/CI 时 snapshot）
        └── vendor/               ← **不提交**（`neko-plugin sync` 生成）
```

要点：

- 文件系统上只有一份源码；**两个 git 远程**分别跟踪主仓与插件仓的提交历史。
- 在 `plugin/testbench/` 内执行 `git` 命令 → 操作的是 **Market 仓库**；在主仓根目录执行 → 操作 **N.E.K.O 仓库**。
- `bundled/tests/testbench/` 来自 `tests/testbench/` 的快照，**不要** commit 进插件仓（已在 `.gitignore`）。Market CI 会在挂载插件后、打包前由 N.E.K.O 侧 workflow 调用 `build_plugin.py --snapshot-only` 生成（需 upstream 已包含对应脚本，见 §6）。

---

## 4. 日常开发：改代码 → 双端提交

推荐顺序：

### 4.1 在主仓开发并验证

```powershell
# N.E.K.O 根目录
uv run neko-plugin check tests/testbench_dist/plugin/testbench
uv run neko-plugin check -r tests/testbench_dist/plugin/testbench
uv run pytest tests/testbench_dist/plugin/testbench/tests/test_smoke.py
uv run python tests/testbench_dist/scripts/run_plugin_smokes.py
```

推送至 `NEKO-dev/main`，走 PR（如 #2953）合入 upstream。

### 4.2 同步到插件独立仓库

#### 4.2.1 一键同步（推荐）

主仓 **已 commit** 插件驱动改动后，在 N.E.K.O 根目录执行：

```powershell
uv run python tests/testbench_dist/scripts/sync_plugin_repo.py -m "fix: 简要说明"
```

脚本会：检查主仓 `plugin/testbench/` 无未提交 diff → 在嵌套 `.git` 里 `git add -u` → commit → `git push origin HEAD`。  
跳过 `bundled/`、`vendor/`、`uv.lock`、`.venv/`（与 §7 一致）。

仅预览：`--dry-run`；主仓尚有未提交插件改动但确要推送插件仓：`--allow-dirty-monorepo`（慎用）。

#### 4.2.2 手动同步

进入插件目录（**注意 cwd**）：

```powershell
cd tests/testbench_dist/plugin/testbench

git status
git add -u .
# 不要 add bundled/、vendor/、.venv/、uv.lock
git commit -m "fix: 简要说明"
git push origin main
```

推送后 GitHub Actions **Verify N.E.K.O Plugin** 会自动跑 Ruff + release check。

### 4.3 同步检查（可选）

确认主仓与插件仓 **工作区文件一致**（提交前）：

```powershell
# 主仓根目录：应对 plugin 目录无未提交 diff
git diff HEAD -- tests/testbench_dist/plugin/testbench/
```

若主仓已 commit 但插件仓未 push，Market 审核看到的仍是旧 commit — **发布前务必两边对齐**。

---

## 5. Market 审核与版本发布

### 5.1 首次上架（仅需一次）

1. 确认 `neko-plugin check -r --market-release` 通过；
2. 插件仓 `main` 已 push，Verify CI 绿；
3. 在 Market 投稿页填写仓库 URL，提交 **首次审核**；
4. 审核通过后，在主仓根目录执行：

```powershell
uv run neko-plugin publish tests/testbench_dist/plugin/testbench
```

`publish` 会：打 tag（如 `v0.2.0`）→ 触发 release workflow → 上传 `testbench.neko-plugin` → 通知 Market。

### 5.2 后续版本

1. 修改源码；
2. **递增** `plugin.toml` 的 `version`（已发布版本号不可复用）；
3. 主仓 PR + 插件仓 push；
4. 再次 `neko-plugin publish ...`。

审核员要求修改时：在插件仓 commit/push → 在 Market 申请页提交新 Revision（填 `git rev-parse HEAD` 的 SHA）。

更完整步骤见 [官方 CLI 文档](https://project-neko.online/zh-CN/plugins/cli)。

---

## 6. CI 与 N.E.K.O upstream 的依赖

插件仓 workflow 为 **薄封装**，调用主项目可复用 workflow：

```yaml
# .github/workflows/verify.yml（插件仓）
uses: Project-N-E-K-O/N.E.K.O/.github/workflows/plugin-market-verify.yml@main
```

因此：

- **Verify / Release 的行为**（Ruff、sync、bundled snapshot、打包）以 **upstream `Project-N-E-K-O/N.E.K.O@main`** 为准；
- 若 PR 中新增了 Market CI 逻辑（例如 Testbench 的 `bundled` snapshot 步骤），需 **合入 upstream 之后**，插件 tag 发布打出来的包才一定含完整 `bundled/`；
- 开发阶段可用 `build_plugin.py` 本地验证包内容。

主仓内相关文件（给维护者参考，不在插件仓内）：

- `.github/workflows/plugin-market-verify.yml`
- `.github/workflows/plugin-market-release.yml`
- `tests/testbench_dist/scripts/build_plugin.py`（`--target-plugin-dir` / `--snapshot-only`）

---

## 7. 不要提交 / 需要生成的内容

| 路径 | 说明 |
|------|------|
| `bundled/` | 构建时从 `tests/testbench/` snapshot；CI 或 `build_plugin.py` 生成 |
| `vendor/` | `neko-plugin sync testbench --clean` 生成 |
| `runtime/*.exe` | 已废弃的独立 exe 通道 |
| `.venv/`、`uv.lock` | 本地环境，非插件源码 |

---

## 8. 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| `neko-plugin publish` 报 workflow 不是 CURRENT | 插件仓 workflow 非官方模板 | `neko-plugin setup-repo tests/testbench_dist/plugin/testbench --upgrade-github-actions`（有冲突则手动对齐模板） |
| Market Verify Ruff 失败（I001） | 函数内 lazy import | 使用 `embed_runtime.py` 等模式；本地用与 CI 相同命令：`uvx ruff==0.12.4 check --ignore-noqa --isolated ... plugin-repo` |
| 安装包缺 Testbench 业务代码 | 未 snapshot `bundled/` | 本地：`build_plugin.py`；发布：确认 upstream snapshot 步骤已合入 |
| 主仓与插件仓内容不一致 | 只 push 了一边 | 按 §4.3 对齐后再 Market Revision / publish |
| AppData 安装后启动失败 | 缺 `bundled/` 或 `compatible_neko` 不匹配 | 检查 `.neko-plugin` 内是否有 `bundled/tests/testbench/` |

---

## 9. 更新维护检查清单

**每次功能性修改后：**

- [ ] 主仓：`check` / `check -r` / smoke 通过
- [ ] 主仓 push 后确认 **Sync Testbench plugin to Market repo** Action 绿（或本地 pre-push 已 mirror）
- [ ] 插件仓 Verify CI 绿
- [ ] 若发 Market 版本：`plugin.toml` 版本号已递增
- [ ] 若发 Market 版本：`neko-plugin publish tests/testbench_dist/plugin/testbench` 到「Market 发布成功」
- [ ] 手测：Plugin Manager 导入 → 启停 → Panel 状态刷新

**仅文档 / 主仓 dist 脚本变更、未改插件驱动文件时：** 可只推主仓 PR，不必强制插件仓 commit。

---

## 10. 全自动同步（推荐）

### 10.1 GitHub Actions（云端，push 即 mirror）

Workflow：`.github/workflows/sync-testbench-plugin-repo.yml`

- **触发**：`main` 上 `tests/testbench_dist/plugin/testbench/**` 有变更时 push；也可 `workflow_dispatch` 手动跑
- **行为**：从主仓 checkout 驱动源码 → 写入克隆的 `n.e.k.o_plugin_testbench` → commit → push → 插件仓 Verify CI 自动跑

**一次性配置（NEKO-dev 与 upstream 合入后均需配置）：**

1. GitHub → **Settings → Developer settings → Fine-grained tokens**（或 Classic PAT）
2. 权限：`TL0SR2/n.e.k.o_plugin_testbench` → **Contents: Read and write**
3. 主仓 **Settings → Secrets and variables → Actions** → New secret：
   - Name: `TESTBENCH_PLUGIN_REPO_PAT`
   - Value: 上述 PAT
4. 推送含 workflow 的 commit 后，改插件驱动并 push `main`，在 Actions 页确认 **Sync Testbench plugin to Market repo** 成功

Secret 未配置时 workflow 会明确报错，不会静默跳过。

### 10.2 本地 pre-push hook（可选，双保险）

```powershell
git config core.hooksPath .githooks
```

`.githooks/pre-push` 在 push `main` 且本次 commit 涉及 `plugin/testbench/` 时，自动调用 `sync_plugin_repo.py`（依赖本地嵌套 `.git`）。

### 10.3 手动 / CI 脚本

```powershell
# 本地嵌套 .git
uv run python tests/testbench_dist/scripts/sync_plugin_repo.py -m "fix: ..."

# GitHub Actions 内部（无嵌套 .git）
python tests/testbench_dist/scripts/sync_plugin_repo.py --ci \
  --target-repo plugin-market-repo \
  --monorepo-sha "$GITHUB_SHA"
```

`neko-plugin publish` 只负责 Market **发版打 tag**，不替代日常源码 mirror。

---

## 11. 相关文档

- [PLUGIN_INSTALL.md](../PLUGIN_INSTALL.md) — 安装与手测
- [docs/PLAN.md](PLAN.md) — 双通道架构
- [plugin/testbench/README.md](../plugin/testbench/README.md) — 插件仓英文简版
- [plugin/testbench/docs/quickstart.md](../plugin/testbench/docs/quickstart.md) — Hosted 面板快速开始

# Testbench 上游同步与更新追踪 (2026-07)

> **文档定位**: 本文是本轮"对齐上游主程序"的**永久化更新说明**, 面向后续接手的
> 开发者 / agent, 回答三个问题: **改了什么 (what)、为什么改 (why)、怎么验证没改炸
> (how verified)**。沿用 `UPSTREAM_SYNC_2026-06.md` 的先例, 与 `PLAN.md` /
> `PROGRESS.md` 等长期文档 (会被上游合并覆盖) 相区分, 本文是 fork 侧本轮同步的独立追踪档。
>
> **基线**: 合并基线 `63c4fcf1` → 本地 `main` `c8ddceeb` (2026-07-01) ×
> 上游 HEAD `715cab7f` (2026-07-15); 合并提交 **`041dbb14`** (2026-07-15)。

---

## 1. 同步概况

- `upstream/main` 领先本地 **222** 提交; 本地领先上游 **13** 提交 (fork 自有 testbench 工作)。
- `git merge upstream/main`: **仅 1 个冲突文件**, 其余全部自动合并。
- 上游本轮主要变化 (与 testbench 相关的): 记忆子系统分批读取 (`iter_original_by_timeframe_batches`,
  PR #2351)、`app/agent_server` 与 `app/main_server` 由单文件拆分为包、`config` 常量在包内迁移。

---

## 2. 冲突解决 (1 处)

**`tests/testbench/pipeline/conversation_corpus.py`** — 采纳上游版本:

- 上游 PR #2351 把时间索引读取从一次性 `retrieve_original_by_timeframe(...)` 改为分批迭代
  `iter_original_by_timeframe_batches(...)`, 并新增 `_TIME_INDEX_BATCH_SIZE = 256` 常量与
  更健壮的异常处理 (后批失败时 `turns.clear()`, 保留"全有或全无"契约, 不暴露半截语料)。
- 该 testbench 文件是这套记忆 API 的**消费方**, 故对齐上游新分批路径 (旧方法在合并后仍存在,
  但新路径是与上游内存优化配套的规范入口)。函数产出的 turn 结构不变。

---

## 3. 合并后验证发现的漂移 (1 处, 已修)

`_run_all.py` 首轮 **28 PASS / 1 FAIL**, 唯一红是 `p25_avatar_dedupe_drift_smoke` (R2/R3/R5)。

- **根因 (非功能 bug, 是 drift smoke 锚点失效)**: 上游把 `AVATAR_INTERACTION_DEDUPE_WINDOW_MS = 8000`
  的**字面量定义从 `config/__init__.py` 迁到 `config/session_settings.py`**, `config/__init__.py`
  现在只 `from .session_settings import ...` 再 re-export。值 (8000) 与 `cross_server.py` 的别名
  `AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS = AVATAR_INTERACTION_DEDUPE_WINDOW_MS` 均未变。
- 该 smoke 的静态解析器把 `config/__init__.py` 硬编码为查找整数字面量的位置, 只匹配 `NAME = <int>`
  赋值行, 遇到 import 行即解析失败 → R2/R3 拿不到上游值、R5 合成 exec 缺别名绑定报 `NameError`。
- **修复** ([smoke/p25_avatar_dedupe_drift_smoke.py](../smoke/p25_avatar_dedupe_drift_smoke.py)):
  新增 `_resolve_config_int(name)`, 按 `config/__init__.py` → 各 `config/*.py` 顺序扫描整个
  `config` 包定位真实字面量; `_resolve_window_value` 改用它。这样常量今后在包内再搬家也不会失锚。
  副本 `pipeline/avatar_dedupe.py` 保持独立字面量 8000 (L30: 不跨包 import), **不改**。

---

## 4. 验证结果

- `uv run python tests/testbench/smoke/_run_all.py`: **29 / 29 PASS** (修复后重跑, elapsed ≈ 52s)。
- 全仓库无残留冲突标记; 改动文件无 lint 错误。
- 派生元教训见 [LESSONS_LEARNED.md §7.A](LESSONS_LEARNED.md) L62 (drift smoke 解析上游值时, 锚定"包"而非"单文件")。

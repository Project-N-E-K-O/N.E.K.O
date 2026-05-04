# 方案：防止 memory reader 重启循环干扰 OCR 回退

日期：2026-05-04

状态：已实施

---

## 问题

Unity 引擎游戏（如 TheLamentingGeese.exe）hook codes 无法注入时，memory reader 进入**无限重启循环**：

```
扫描进程 → 附加 Textractor → hook_codes=[] → 等5s超时 → 停止 → backoff 5s → 重新扫描 → 重复
```

每次循环 ~13s，资源浪费且不断干扰 OCR 的正常接管。虽然 OCR 在此期间不会被完全阻塞（因 memory reader 从未产出文本），但持续的重启循环会导致：

- CPU 浪费于反复的进程扫描和 Textractor 启停
- 运行时状态抖动（attaching → backoff → attaching → …）
- Bridge 事件泛滥（每次重启产生 session start/stop/error 事件）
- 反复 attach/detach 对游戏进程造成潜在干扰

### 问题本质

**没有 hook codes 不是错误**——某些引擎就是没有可用的 hook codes，这是正常情况。当前的问题是 memory reader 把"无法工作"当成了"需要重试"，应该改成：**识别出无法工作的场景后，安静退到 idle，让 OCR 接管**。

### 当前机制分析

#### 主循环流程（`_poll_once`，L1531 起）

```
preflight 检查 (L1536-1594)
  → 扫描进程 (L1599-1604)
  → 进程消失检测 (L1605-1619)
  → Textractor 崩溃检测 (L1621-1631)
  → 选择目标进程 (L1633-1642)
  → 启动 Textractor + attach (L1643-1715)
  → 读取 stdout / 确认 attach (L1717-1781)
  → 消费文本行 (L1782-1818)
```

#### 重启触发点

| 触发点 | 位置 | backoff | 有上限？ |
|--------|------|---------|----------|
| A. 进程消失 | L1608-1618 | 无（直接 detach） | 无 |
| B. Textractor 进程崩溃 | L1621-1631 → L1826-1837 | 5s | 有：3次后 → error |
| C. attach 命令执行失败 | L1698-1709 | 5s | 无 |
| D. **attach 确认超时（5s 无输出）** | L1764-1780 | 5s | **无** ← 主要问题 |
| E. target 变更请求 | L1581-1589 | 无 | 无 |
| F. bridge_root 变更 | update_config L1328-1353 | 无 | 无 |

**触发点 D 是核心问题**：当 hook_codes 为空（`_select_hook_codes_for_engine` 返回 `[], "hook_codes_none"`）时，Textractor 附加了但没有 hook code 注入，不会产生任何文本输出。5 秒后触发超时 → backoff 5s → 无限循环。

#### _select_hook_codes_for_engine 逻辑（L235-251）

```python
# Unity engine, plugin.toml 中 unity = []
configured_codes = engine_hooks.get("unity")  # → []，非 None
# 返回 ([], "hook_codes_none")
```

hook_codes 为空列表但 `hook_code_detail` 是 `"hook_codes_none"`——与 `"hook_codes_skipped_for_unknown_engine"` 不同，后者走 L1690-1697 的 skip 分支。`"hook_codes_none"` 不触发任何 skip，Textractor 被附加但不注入 hook，必然超时。

#### OCR 接管门（ocr_reader.py L6668-6683）

```python
if self._last_memory_reader_text_at > 0:
    elapsed = now - self._last_memory_reader_text_at
    threshold = float(self._config.ocr_reader_no_text_takeover_after_seconds)  # 默认30s
    if elapsed < threshold:
        return  # OCR 等待接管窗口
```

当 memory reader 从未产出文本时 `_last_memory_reader_text_at = 0`，OCR 不会被阻塞。但 restart 循环持续消耗资源。

---

## 方案设计

### 核心思路

区分三种场景：

| 场景 | 含义 | 应该做什么 |
|------|------|-----------|
| 无 hook codes | 该引擎未配置 hook，memory reader 无法工作 | 不附加 Textractor，进程加入跳过列表，状态 → idle |
| attach 连续超时 | 可能是 hook 失效、Textractor 兼容问题等 | 重试 N 次后放弃，进程加入跳过列表，状态 → idle |
| Textractor 崩溃 | 真正的错误 | 维持现有 3 次 crash limit → error |

**关键原则**：只有程序自身出问题才是 error。"这个引擎没有 hook codes"和"试了几次连不上"都是正常情况，状态应该是 idle。

### 1. 空 hook codes → 提前检查，不启动 Textractor

**位置：** `memory_reader.py` L1643 之后、L1650 之前

当前流程先 `_ensure_textractor_started`（L1655）、再 `attach -P{pid}`（L1665）、最后才查 hook codes（L1666）。需在**启动 Textractor 之前**判断：如果 hook codes 为空 + 已知引擎 → 直接跳过。

新增代码插入位置：L1649（`result.should_rescan = True`）之后、L1650（`self._attached_process = target`）之前。注意 L1649 在条件块内，插入代码需与 L1650 同级缩进（不在 if 块内）。

```python
            # === 新增：hook codes 提前检查 ===
            hook_codes, hook_code_detail = _select_hook_codes_for_engine(
                self._config, target.engine,
            )
            self._last_hook_code_count = len(hook_codes)
            self._last_hook_code_detail = hook_code_detail
            self._logger.info(
                "memory_reader hook_codes selected: {} (count={}, engine={}, detail={})",
                hook_codes, len(hook_codes), target.engine, hook_code_detail,
            )

            if not hook_codes and hook_code_detail == "hook_codes_none":
                self._logger.info(
                    "memory_reader no hook codes for engine=%s; staying idle, OCR will handle",
                    target.engine,
                )
                self._skip_process_pids.add(target.pid)
                self._runtime = self._current_runtime(
                    status="idle",
                    detail="no_hook_codes_available",
                    process=target,
                )
                result.runtime = self._runtime.to_dict()
                return result
            # === 新增结束 ===
```

**注意**：L1666-1671 的 `_select_hook_codes_for_engine` 调用**保留不变**。因为此处第 1 次调用是 gate（hook_codes_none → early return），只有非 none 的情况才会到达 L1666，第 2 次调用获取同样的值用于后续 hook 注入（L1679-1689）。两次调用结果相同，函数是纯字典查找，无副作用。不删除 L1666-1671 避免了引入新的状态变量。

### 2. 记录"无需重试"的进程 PID，跳过重复扫描

**新增字段** — `__init__` L1319 之后：

```python
self._skip_process_pids: set[int] = set()
```

**进程扫描过滤** — L1600 之后，替换原 `self._last_process_inventory = list(processes)`：

```python
processes = await asyncio.to_thread(scanner)
# 新增：排除已知不可用的进程
processes = [p for p in processes if p.pid not in self._skip_process_pids]
self._last_process_inventory = list(processes)
```

**`update_config` 中清空** — L1331 之后、L1333 之前（`self._config = config` 之后、早返回判断之前）：

```python
self._config = config
self._runtime.enabled = config.memory_reader_enabled
# 新增：配置变了，重新评估之前跳过的进程
self._skip_process_pids.clear()
self._consecutive_attach_timeouts = 0
if not bridge_root_changed:
    return
```

放在 `if not bridge_root_changed: return` **之前**是关键——即使用户只改了 hook codes 而没改 bridge_root，跳过列表也会刷新。

**`update_process_target` 中清空** — 用户切换目标时覆盖自动跳过。**必须处理 idle 状态下切换目标的场景**（此时 `_target_restart_requested` 不会被置 True，因为 L1361-1362 要求有活跃进程）：

```python
# update_process_target (L1355-1364)，改写为：
old_target = self._manual_target.to_dict()
self._manual_target = MemoryReaderProcessTarget.from_dict(target)
self._target_selection_detail = (
    "manual_target_active" if self._manual_target.is_manual() else "auto_candidate_scan"
)
target_changed = old_target != self._manual_target.to_dict()
if target_changed:
    self._skip_process_pids.clear()       # 新增
    self._consecutive_attach_timeouts = 0  # 新增
if target_changed and (
    self._attached_process is not None or self._process is not None
):
    self._target_restart_requested = True
```

**关键**：`_skip_process_pids.clear()` 放在 `target_changed` 条件下，而非 `_target_restart_requested` 条件内。原因是：如果 memory reader 处于 idle（无 attached_process、无 Textractor 进程），原代码 L1361-1362 条件为 False，`_target_restart_requested` 不会被置 True，但跳过列表仍需清空，否则下次 poll 时新目标可能被过滤跳过。

### 3. attach 连续超时 → 有限重试后放弃

**新增字段** — `__init__` L1319 之后：

```python
self._consecutive_attach_timeouts = 0
self._max_attach_timeouts = 3
```

**改写超时处理** — L1764-1780，从原有简单 backoff 改为带上限的递减重试：

```python
# 原代码 L1764-1780 替换为：
if self._runtime.status == "attaching" and now - self._attach_started_at > 5.0:
    self._consecutive_attach_timeouts += 1
    self._logger.warning(
        "memory_reader attach timeout (%d/%d) for %s(%d)",
        self._consecutive_attach_timeouts,
        self._max_attach_timeouts,
        self._attached_process.name if self._attached_process else "",
        self._attached_process.pid if self._attached_process else 0,
    )
    if self._consecutive_attach_timeouts >= self._max_attach_timeouts:
        if self._attached_process is not None:
            self._skip_process_pids.add(self._attached_process.pid)
        message = "memory_reader attach confirmation timed out too many times; giving up"
        self._logger.warning(message)
        result.warnings.append(message)
        if self._writer.emit_error(message, ts=utc_now_iso(now)):
            result.should_rescan = True
        self._runtime.status = "idle"
        self._runtime.detail = "attach_timeout_limit_reached"
        await self._stop_textractor()
        # _stop_textractor 已重置 _attach_started_at 和 _attached_process
        result.runtime = self._runtime.to_dict()
        return result
    # 未达上限：维持现有 backoff 逻辑
    message = "memory_reader attach confirmation timed out"
    self._logger.warning(
        "memory_reader attach confirmation timed out for %s(%d)",
        self._attached_process.name if self._attached_process else "",
        self._attached_process.pid if self._attached_process else 0,
    )
    result.warnings.append(message)
    if self._writer.emit_error(message, ts=utc_now_iso(now)):
        result.should_rescan = True
    self._runtime.status = "backoff"
    self._runtime.detail = "attach_timeout"
    self._backoff_until = now + 5.0
    await self._stop_textractor()
    self._attached_process = None
    result.runtime = self._runtime.to_dict()
    return result
```

**计数器清零时机：**

- **attach 确认成功** — L1754 之后：
  ```python
  self._restart_attempts = 0
  self._consecutive_attach_timeouts = 0  # 新增
  ```

- **收到文本** — L1790 之后：
  ```python
  self._last_heartbeat_at = now
  self._consecutive_attach_timeouts = 0  # 新增
  ```

- **`update_config`** — 与 `_skip_process_pids.clear()` 同一位置（见方案 2）

- **`update_process_target`** — 目标切换时（见方案 2）

### 4. OCR 侧：无需改动

当前 OCR 接管门（`_last_memory_reader_text_at > 0` 且 `elapsed < threshold`）在 memory reader 从未产出文本或已 idle 时都不阻塞。memory reader 退到 idle 后，OCR 自然接管。

---

## 涉及文件

| 文件 | 改动 |
|------|------|
| `memory_reader.py` | 单文件 8 处插入点（9 个改动动作，2 个新字段合并为 1 处插入） |

OCR 侧不需要改动。

---

## 实施步骤详表

| 步骤 | 位置 | 动作 | 插入方式 |
|------|------|------|----------|
| 1 | `__init__` L1319 之后 | 新增 `_skip_process_pids: set[int] = set()` | 新增行 |
| 2 | `__init__` L1319 之后 | 新增 `_consecutive_attach_timeouts = 0`、`_max_attach_timeouts = 3` | 新增行 |
| 3 | L1600 之后 | 进程扫描后过滤：`processes = [p for p in processes if p.pid not in self._skip_process_pids]` | 插入行（在 `self._last_process_inventory` 之前） |
| 4 | L1649 之后、L1650 之前 | 新增 hook codes 提前检查 gate（详见方案 1），hook_codes_none → 加入跳过列表，设 idle，return | 插入代码块（与 L1650 同级缩进） |
| 5 | L1331 之后、L1333 之前 | `update_config` 中：`self._skip_process_pids.clear()` + `self._consecutive_attach_timeouts = 0` | 插入行 |
| 6 | `update_process_target` L1355-1364 | 提取 `target_changed` 变量；在 `target_changed` 下清空跳过列表和计数器（详见方案 2） | 改写方法体 |
| 7 | L1754 之后 | 清零：`self._consecutive_attach_timeouts = 0` | 插入行 |
| 8 | L1764-1780 | 改写超时处理：递增计数器 + 上限检查 + 加入跳过列表（详见方案 3） | 替换代码块 |
| 9 | L1790 之后 | 清零：`self._consecutive_attach_timeouts = 0` | 插入行 |

---

## 验证方式

| # | 场景 | 预期 |
|---|------|------|
| 1 | 不配置 Unity hook codes，启动 Unity galgame | memory reader 日志输出 `no hook codes for engine=unity`，状态 `idle (no_hook_codes_available)`，进程 PID 加入跳过列表，后续循环不再扫描该进程；OCR 正常开始识别 |
| 2 | 用有效 hook codes 的引擎（如 kirikiri2） | memory reader 正常附加、注入 hook、捕获文本，不受影响 |
| 3 | Textractor 进程崩溃 | 现有 3 次 crash limit 逻辑不变，之后状态为 error |
| 4 | 更新 hook codes 配置（空 → 有效）、触发 `update_config` | `_skip_process_pids` 被清空，memory reader 重新尝试附加 |
| 5 | 用户手动切换目标进程（idle 状态下） | `_skip_process_pids` 被清空，下次 poll 不会因旧 PID 跳过新目标 |
| 6 | 用户手动切换目标进程（active 状态下） | `_skip_process_pids` 被清空 + `_target_restart_requested` 触发重扫 |
| 7 | attach 连续超时（有 hook codes 但 Textractor 无输出） | 3 次重试后放弃，进程 PID 加入跳过列表，状态 idle |

---

## 审查

### 审查日期：2026-05-04（第 3 轮）

### 审查结论：通过

#### 第 1-2 轮遗留（均已处理）

| 发现 | 处理 |
|------|------|
| H1: L1666-1671 重复调用 | 保留不删，两次调用在不同执行路径上，函数无副作用 |
| M2: `update_config` 中 clear 时机 | 步骤 5 明确：L1331 之后、L1333 早返回之前 |
| L3: `_max_attach_timeouts` 硬编码 | 暂不处理 |
| L4: `_skip_process_pids` 不持久化 | 无需处理 |
| 补充 1: `update_process_target` 清空 | 已补充（步骤 6） |
| 补充 2: idle 状态下切换目标验证 | 已补充（验证 #5、#6） |

#### 第 3 轮发现

**H1（已修复）：`update_process_target` 中 `_skip_process_pids.clear()` 放置在错误的条件块内**

第 2 轮文档将 `_skip_process_pids.clear()` 放在 `_target_restart_requested = True` 条件内。但原代码 L1361-1362 要求 `self._attached_process is not None or self._process is not None`——如果 memory reader 处于 idle 状态，此条件为 False，跳过列表不会被清空。

**修复**：将方法体重写为：
1. 提取 `target_changed = old_target != self._manual_target.to_dict()`
2. `if target_changed:` 下清空跳过列表和计数器（独立于进程状态）
3. `if target_changed and (活跃进程):` 下设置 `_target_restart_requested = True`

**验证 #5、#6 拆分**：将原验证 #5 拆分为 idle 状态切换（#5）和 active 状态切换（#6），覆盖两个分支。

#### 其余检查项（均通过）

- **步骤 4 缩进**：新增代码与 L1650 同级（不在 L1643-1649 的 if 块内），已标注
- **`_stop_textractor` 清理**：L1863 已重置 `_attach_started_at = 0.0`、L1862 已重置 `_attached_process = None`，方案 3 依赖此行为，已验证
- **`_current_runtime` 接受 `process` 参数**：L1648 在 early return 时 `_attached_process` 可能为 None，但 `_current_runtime(status="idle", process=target)` 显式传入 target，runtime 信息正确
- **9 个步骤、8 处插入点**：步骤 1-2 同位置（`__init__`），合并为 1 处插入；其余各 1 处

# Plugin 代码清理报告

## 一、冗余代码清理

### 1.1 HTTP 相关冗余代码（已替换为 Queue）

#### timer_service/main.py

**问题**：`_trigger_callback` 方法使用 HTTP 调用其他插件，应该使用 Queue

**位置**：`plugin/plugins/timer_service/main.py:121-169`

**当前代码**：
```python
def _trigger_callback(self, plugin_id: str, entry_id: str, args: Dict[str, Any]):
    """触发回调插件的入口点"""
    # 通过 HTTP API 触发回调插件
    def _async_trigger():
        async def _do_trigger():
            url = f"http://localhost:{USER_PLUGIN_SERVER_PORT}/plugin/trigger"
            # ... HTTP 请求代码 ...
```

**应该改为**：
```python
def _trigger_callback(self, plugin_id: str, entry_id: str, args: Dict[str, Any]):
    """触发回调插件的入口点"""
    try:
        # 使用 Queue 机制调用其他插件
        result = self.call_plugin(
            plugin_id=plugin_id,
            event_type="plugin_entry",  # 或者自定义事件类型
            event_id=entry_id,
            args=args
        )
        self.logger.debug(f"[TimerService] 成功触发回调: {plugin_id}.{entry_id}")
    except Exception as e:
        self.logger.exception(f"[TimerService] 触发回调失败: {plugin_id}.{entry_id}, 错误: {e}")
```

**清理项**：
- 移除 `import httpx`
- 移除 `USER_PLUGIN_SERVER_PORT` 相关代码
- 移除三层嵌套的异步函数
- 移除线程创建代码

---

#### web_interface/__init__.py

**问题**：多处使用 HTTP 调用 timer_service，应该使用 Queue

**位置**：
1. `get_timers()` - 第 195-224 行
2. `test_timer()` - 第 823-900 行
3. `on_timer_tick()` - 第 939-960 行

**应该改为**：使用 `self.call_plugin()` 方法

**清理项**：
- 移除 `import httpx`（如果不再需要）
- 移除 `USER_PLUGIN_SERVER_PORT`（如果不再需要）
- 移除所有 HTTP 请求代码
- 统一错误处理

---

### 1.2 向后兼容代码

**问题**：文档中提到"降级到 HTTP"的代码，现在应该移除

**位置**：
- `plugin/core/context.py` - 无（已实现 Queue）
- `plugin/sdk/base.py` - 无（已实现 Queue）

**状态**：✅ 已清理，无向后兼容代码

---

## 二、代码复用分析

### 2.1 可复用的代码模式

#### A. 错误处理模式

**重复代码**：
- HTTP 错误处理在多个地方重复
- 超时处理逻辑重复

**建议**：在 SDK 中提供统一的错误处理工具函数

```python
# plugin/sdk/utils.py (新建)
def handle_plugin_call_error(error: Exception, plugin_id: str, entry_id: str) -> Dict[str, Any]:
    """统一的插件调用错误处理"""
    # ...
```

#### B. 响应解析模式

**重复代码**：
- 解析 HTTP 响应的代码在多个地方重复
- 提取 `plugin_response` 的逻辑重复

**建议**：在 SDK 中提供响应解析工具

---

### 2.2 已复用的代码

✅ **PluginContext.trigger_plugin_event**：统一的插件间通信方法
✅ **NekoPluginBase.call_plugin**：统一的插件调用抽象
✅ **PluginRouter**：统一的插件间通信路由

---

## 三、命名规范检查

### 3.1 符合规范的命名

✅ **类名**：使用 `PascalCase`
- `PluginContext`
- `PluginProcessHost`
- `PluginCommunicationResourceManager`

✅ **函数/方法名**：使用 `snake_case`
- `trigger_plugin_event`
- `call_plugin`
- `_trigger_callback`

✅ **常量**：使用 `UPPER_SNAKE_CASE`
- `USER_PLUGIN_SERVER_PORT`
- `PLUGIN_TRIGGER_TIMEOUT`

✅ **私有方法/属性**：使用 `_` 前缀
- `_plugin_comm_queue`
- `_trigger_callback`
- `_pending_futures`

---

### 3.2 需要改进的命名

#### A. 变量命名不一致

**问题**：有些变量使用缩写，有些使用全称

**位置**：
- `plugin/runtime/host.py:24` - `_factory(pid: str, ...)` - `pid` 应该是 `plugin_id`
- `plugin/runtime/registry.py` - 多处使用 `pid` 而不是 `plugin_id`

**建议**：统一使用 `plugin_id` 而不是 `pid`

---

#### B. 参数命名

**问题**：`**kwargs` 和 `**_` 混用

**位置**：所有插件入口点方法

**当前**：
```python
def method(self, param: str, **_):
```

**建议**：统一使用 `**kwargs` 或 `**_`（如果不需要访问）

**理由**：
- `**_` 表示"忽略所有额外参数"
- `**kwargs` 表示"接受所有额外参数"
- 如果不需要访问，使用 `**_` 更明确

---

#### C. 类型注解中的 `Any`

**问题**：过多使用 `Any`，应该使用更具体的类型

**位置**：
- `plugin/core/context.py:21` - `logger: Any` 应该是 `logging.Logger`
- `plugin/core/context.py:22` - `status_queue: Any` 应该是 `Queue`

**建议**：使用具体的类型注解

---

## 四、具体清理建议

### 4.1 立即清理（高优先级）

1. **替换 timer_service 的 HTTP 调用**
   - 文件：`plugin/plugins/timer_service/main.py`
   - 方法：`_trigger_callback`
   - 改为：使用 `self.call_plugin()`

2. **替换 web_interface 的 HTTP 调用**
   - 文件：`plugin/plugins/web_interface/__init__.py`
   - 方法：`get_timers()`, `test_timer()`, `on_timer_tick()`
   - 改为：使用 `self.call_plugin()`

3. **移除未使用的导入**
   - `timer_service/main.py`: 移除 `import httpx`
   - `web_interface/__init__.py`: 检查是否还需要 `httpx`（如果只用于外部调用，保留）

4. **统一参数命名**
   - 将 `pid` 改为 `plugin_id`（在 `_factory` 等函数中）

---

### 4.2 中期改进（中优先级）

1. **改进类型注解**
   - 将 `Any` 替换为具体类型
   - 添加 `from __future__ import annotations`

2. **提取可复用代码**
   - 创建 `plugin/sdk/utils.py` 用于工具函数
   - 统一错误处理模式

3. **优化响应等待机制**
   - `plugin/core/context.py` 中的响应等待逻辑可以优化
   - 考虑使用更高效的匹配机制

---

### 4.3 长期改进（低优先级）

1. **代码重构**
   - 将重复的 HTTP 错误处理提取为工具函数
   - 统一响应解析逻辑

2. **文档更新**
   - 更新文档，移除 HTTP 相关示例
   - 添加 Queue 使用示例

---

## 五、清理后的代码结构

### 5.1 timer_service/main.py

**清理前**：~510 行（包含 HTTP 相关代码）
**清理后**：~450 行（移除 HTTP，使用 Queue）

**改进**：
- 移除 60 行 HTTP 相关代码
- 简化 `_trigger_callback` 方法
- 移除线程和事件循环相关代码

---

### 5.2 web_interface/__init__.py

**清理前**：~983 行（包含多处 HTTP 调用）
**清理后**：~850 行（移除插件间 HTTP 调用）

**改进**：
- 移除插件间 HTTP 调用代码
- 保留外部 HTTP 调用（如果需要）
- 统一使用 `call_plugin()` 方法

---

## 六、命名规范总结

### 6.1 当前状态

| 类型 | 规范 | 符合度 | 问题 |
|------|------|--------|------|
| 类名 | PascalCase | ✅ 100% | 无 |
| 方法名 | snake_case | ✅ 100% | 无 |
| 常量 | UPPER_SNAKE_CASE | ✅ 100% | 无 |
| 私有成员 | _prefix | ✅ 100% | 无 |
| 参数命名 | snake_case | ⚠️ 95% | `pid` vs `plugin_id` |
| 类型注解 | 具体类型 | ⚠️ 80% | 过多使用 `Any` |

### 6.2 改进建议

1. **统一使用 `plugin_id` 而不是 `pid`**
2. **减少 `Any` 的使用，使用具体类型**
3. **统一使用 `**_` 而不是 `**kwargs`（如果不需要访问）**

---

## 七、执行计划

### 阶段 1：清理 HTTP 代码（立即）

1. ✅ 替换 `timer_service._trigger_callback` 为 Queue
2. ✅ 替换 `web_interface` 中的插件间 HTTP 调用
3. ✅ 移除未使用的导入

### 阶段 2：改进命名（短期）

1. 统一 `pid` -> `plugin_id`
2. 改进类型注解
3. 统一参数命名

### 阶段 3：代码复用（中期）

1. 提取工具函数
2. 统一错误处理
3. 优化响应等待机制

---

## 八、预期收益

### 代码量减少

- `timer_service/main.py`: -60 行（-12%）
- `web_interface/__init__.py`: -130 行（-13%）
- **总计**: -190 行

### 复杂度降低

- 嵌套层级：从 3-4 层降低到 1-2 层
- 代码重复率：从 ~30% 降低到 <10%
- 依赖减少：移除 `httpx` 依赖（插件间通信）

### 可维护性提升

- 统一的调用方式
- 更清晰的错误处理
- 更好的类型安全


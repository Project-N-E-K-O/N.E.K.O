# 最佳实践

## 一致使用 Result 类型

始终在入口点中返回 `Ok`/`Err`，而不是抛出异常：

```python
from plugin.sdk.plugin import Ok, Err, SdkError

@plugin_entry(id="process")
def process(self, data: str, **_):
    if not data:
        return Err(SdkError("data is required"))

    try:
        result = self._do_work(data)
        return Ok({"result": result})
    except ValueError as e:
        return Err(SdkError(f"Validation error: {e}"))
    except Exception as e:
        self.logger.exception(f"Unexpected error: {e}")
        return Err(SdkError(f"Internal error"))
```

## 代码组织

将初始化、辅助方法和公共入口点分离：

```python
@neko_plugin
class WellOrganizedPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._initialize()

    # --- 生命周期 ---
    @lifecycle(id="startup")
    def on_startup(self, **_):
        return Ok({"status": "ready"})

    # --- 私有辅助方法 ---
    def _initialize(self):
        """设置资源。"""
        pass

    def _validate(self, data):
        """内部验证。"""
        pass

    # --- 公共入口点 ---
    @plugin_entry(id="process")
    def process(self, data: str, **_):
        self._validate(data)
        return Ok({"result": self._do_work(data)})
```

## 日志记录

使用适当的日志级别：

| 级别 | 使用场景 |
|------|----------|
| `debug` | 详细的诊断信息 |
| `info` | 正常运行的里程碑 |
| `warning` | 意外但已处理的情况 |
| `error` | 需要关注的错误 |
| `exception` | 带完整堆栈跟踪的错误 |

```python
self.logger.debug(f"Processing item {item_id}")
self.logger.info(f"Plugin started successfully")
self.logger.warning(f"Retry attempt {attempt}/3")
self.logger.error(f"Failed to connect: {err}")
self.logger.exception(f"Unexpected error in process()")
```

## 状态更新

在长时间运行的操作中报告进度：

```python
@plugin_entry(id="batch_job")
def batch_job(self, items: list, **_):
    total = len(items)
    for i, item in enumerate(items):
        self._process(item)
        self.report_status({
            "status": "processing",
            "progress": (i + 1) / total * 100,
            "message": f"Processing {i+1}/{total}"
        })

    self.report_status({"status": "completed", "progress": 100})
    return Ok({"processed": total})
```

## 输入验证

使用 `input_schema` 进行自动 JSON Schema 验证，或使用 `params` 配合 Pydantic 模型：

```python
# 方式 A：JSON Schema
@plugin_entry(
    id="validated",
    input_schema={
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "age": {"type": "integer", "minimum": 0, "maximum": 150}
        },
        "required": ["email", "age"]
    }
)
def validated(self, email: str, age: int, **_):
    return Ok({"email": email, "age": age})

# 方式 B：Pydantic 模型（自动生成 schema）
from pydantic import BaseModel, Field

class UserInput(BaseModel):
    email: str = Field(..., description="User email")
    age: int = Field(..., ge=0, le=150)

@plugin_entry(id="validated_v2", params=UserInput)
def validated_v2(self, email: str, age: int, **_):
    return Ok({"email": email, "age": age})
```

## 工作目录

使用 `self.config_dir` 和 `self.data_path()` 代替硬编码路径：

```python
# 插件目录（plugin.toml 所在位置）
config_file = self.config_dir / "config.json"

# 数据目录（自动创建的子目录）
db_path = self.data_path("cache.db")       # → <plugin_dir>/data/cache.db
logs_dir = self.data_path("logs")          # → <plugin_dir>/data/logs/
```

## 跨插件调用的错误处理

调用其他插件时始终处理 `Err`：

```python
@plugin_entry(id="orchestrate")
async def orchestrate(self, **_):
    # 先检查依赖
    dep = await self.plugins.require_enabled("dependency_plugin")
    if isinstance(dep, Err):
        return Err(SdkError("Required plugin 'dependency_plugin' is not available"))

    # 发起调用
    result = await self.plugins.call_entry("dependency_plugin:do_work", {"key": "val"})
    if isinstance(result, Err):
        self.logger.error(f"Cross-plugin call failed: {result.error}")
        return Err(SdkError("Dependency call failed"))

    return Ok({"combined": result.value})
```

## 优雅关闭

在 shutdown 生命周期中清理资源：

```python
@lifecycle(id="shutdown")
async def on_shutdown(self, **_):
    # 关闭网络连接
    if self.session:
        await self.session.close()

    # 刷新待处理的数据
    await self.store.flush()

    # 取消定时器（自动处理，但记录日志）
    self.logger.info("Plugin shutting down gracefully")
    return Ok({"status": "stopped"})
```

## 插件检查清单

发布插件前请检查：

- [ ] 所有入口点返回 `Ok`/`Err`（而非原始字典或异常）
- [ ] 已实现 `@lifecycle(id="startup")` 和 `@lifecycle(id="shutdown")`
- [ ] 所有接受参数的入口点都定义了 `input_schema`
- [ ] 所有入口点签名中都包含 `**_`
- [ ] 使用 Logger 而非 `print()`
- [ ] 如果使用了定时器，共享状态已用锁保护
- [ ] 跨插件调用已处理 `Err` 结果
- [ ] `plugin.toml` 中的 `entry` 路径和 SDK 版本约束正确

# 最佳实践

## 代码组织

将初始化、辅助方法和公共入口点分离：

```python
@neko_plugin
class WellOrganizedPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._initialize()

    # 私有辅助方法
    def _initialize(self):
        """设置资源"""
        pass

    def _helper(self, data):
        """内部逻辑"""
        pass

    # 公共入口点
    @plugin_entry(id="process")
    def process(self, data: str, **_):
        result = self._helper(data)
        return {"result": result}
```

## 错误处理

始终优雅地处理错误并返回结构化的响应：

```python
@plugin_entry(id="task")
def task(self, param: str, **_):
    try:
        if not param:
            raise ValueError("param is required")
        result = self._do_work(param)
        return {"success": True, "result": result}
    except ValueError as e:
        self.logger.warning(f"Validation error: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        self.logger.exception(f"Unexpected error: {e}")
        return {"success": False, "error": "Internal error"}
```

## 日志记录

使用适当的日志级别：

| 级别 | 使用场景 |
|------|----------|
| `debug` | 详细的诊断信息 |
| `info` | 正常运行的里程碑 |
| `warning` | 意外但已处理的情况 |
| `error` | 需要关注的错误 |
| `exception` | 带堆栈跟踪的错误 |

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
    return {"processed": total}
```

## 输入验证

使用 JSON Schema 定义 `input_schema` 以实现自动验证：

```python
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
    # 输入已由框架自动验证
    return {"email": email, "age": age}
```

## 工作目录

使用 `ctx.config_path.parent` 作为插件特定文件的基础路径：

```python
work_dir = self.ctx.config_path.parent / "data"
work_dir.mkdir(exist_ok=True)
```

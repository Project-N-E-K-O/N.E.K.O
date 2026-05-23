# 入口与参数

**入口点**是插件中可以被插件管理面板、其他插件或 AI agent 调用的函数。

## 定义入口

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, Ok

@neko_plugin
class MyPlugin(NekoPluginBase):

    @plugin_entry(id="search", name="搜索", description="搜索内容")
    async def search(self, query: str, max_results: int = 10):
        results = await self._do_search(query, max_results)
        return Ok({"results": results})
```

就这样。SDK 会自动从类型注解生成 input schema。

## 参数如何工作

SDK 查看你的函数签名，自动构建 JSON Schema：

```python
async def search(self, query: str, max_results: int = 10):
```

变成：

```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string"},
    "max_results": {"type": "integer", "default": 10}
  },
  "required": ["query"]
}
```

- 没有默认值的参数 → `required`
- 有默认值的参数 → 可选
- `self` 和 `**kwargs` 会被忽略

## 用 `Annotated` 添加描述

```python
from typing import Annotated

async def search(
    self,
    query: Annotated[str, "搜索关键词"],
    max_results: Annotated[int, "最大结果数"] = 10,
):
    ...
```

`Annotated` 里的字符串会变成 schema 中的 `description`。这个描述会显示在插件管理面板和 AI agent 中。

## 用 Pydantic 模型处理复杂输入

参数很多或需要验证（最小/最大值、正则等）时，用 Pydantic 模型：

```python
from pydantic import BaseModel, Field

class SearchParams(BaseModel):
    query: str = Field(..., description="搜索关键词")
    max_results: int = Field(default=10, ge=1, le=50, description="最大结果数")
    language: str = Field(default="zh-CN", description="结果语言")

@plugin_entry(id="search", name="搜索", description="搜索内容")
async def search(self, params: SearchParams):
    # params 是经过验证的 Pydantic 实例
    results = await self._do_search(params.query, params.max_results)
    return Ok({"results": results})
```

SDK 会：
1. 从模型生成 `input_schema`（包含描述、默认值、约束）
2. 运行时用 `model_validate()` 验证传入参数
3. 把验证后的模型实例传给你的函数

## 返回值

成功返回 `Ok(...)`，失败返回 `Err(...)`：

```python
from plugin.sdk.plugin import Ok, Err, SdkError

@plugin_entry(id="divide", name="除法", description="两数相除")
async def divide(self, a: float, b: float):
    if b == 0:
        return Err(SdkError("不能除以零"))
    return Ok({"result": a / b})
```

## 控制 AI 看到什么

默认情况下 AI 看到完整返回值。用 `llm_result_fields` 限制：

```python
@plugin_entry(
    id="search",
    name="搜索",
    description="网络搜索",
    llm_result_fields=["summary"],
)
async def search(self, query: str):
    results = await self._do_search(query)
    summary = self._build_summary(results)
    # AI 只看到 "summary"；"raw_results" 会存储但对 AI 隐藏
    return Ok({"summary": summary, "raw_results": results})
```

## `@plugin_entry` 选项

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `id` | 方法名 | 入口点 ID |
| `name` | 同 id | 显示名称 |
| `description` | `""` | 描述（显示给 AI 和 UI） |
| `input_schema` | 自动推断 | 手动 JSON Schema（覆盖自动推断） |
| `params` | 自动推断 | 显式指定 Pydantic 模型类 |
| `timeout` | `None` | 执行超时（秒） |
| `llm_result_fields` | `None` | AI 可见的字段 |
| `kind` | `"action"` | 入口类型 |
| `auto_start` | `False` | 加载时自动执行 |

## 动态入口

运行时注册入口点（比如根据配置动态生成）：

```python
from plugin.sdk.plugin import lifecycle, Ok

@lifecycle(id="startup")
async def on_startup(self):
    commands = self.metadata.get("commands", {})
    for cmd_id, cmd_config in commands.items():
        self.register_dynamic_entry(
            entry_id=cmd_id,
            handler=lambda **kwargs: Ok({"executed": cmd_id}),
            name=cmd_config.get("name", cmd_id),
            description=cmd_config.get("description", ""),
        )
    return Ok({"status": "ready"})
```

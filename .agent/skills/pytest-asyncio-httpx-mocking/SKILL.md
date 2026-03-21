---
name: pytest-asyncio-httpx-mocking
description: When masking httpx.AsyncClient with unittest.mock in Pytest, AsyncMock must be used instead of MagicMock for async methods like post/get to prevent TypeError when awaited.
---

# httpx.AsyncClient Mocking with AsyncMock

## 症状
- 在测试中使用 `patch.object(httpx.AsyncClient, 'post', return_value=mock_response)`。
- 运行时，代码中包含 `response = await client.post(...)` 的地方抛出 `TypeError: object MagicMock can't be used in 'await' expression`。

## 根本原因
### 原因 1: 异步函数的返回值必须是 Coroutine
- **问题**: `httpx.AsyncClient.post` 是一个 `async def` 方法，调用它会返回一个可等待（awaitable）的协程。
- **为什么发生**: 默认的 `patch` 或 `MagicMock` 没有自动推断对象的异步特性时，它只是同步地返回了 `return_value`。当事件循环试图 `await` 这个同步的 `MagicMock` 对象时，就会报错。
- **解决方案**: 在 `patch` 参数里显式使用 `new=AsyncMock(return_value=...)` 或 `new_callable=AsyncMock`。

## 代码解决方案

**❌ 错误写法:**
```python
from unittest.mock import patch, MagicMock

mock_response = MagicMock(status_code=200)
# 当被 await 时会触发 TypeError!
with patch.object(httpx.AsyncClient, 'post', return_value=mock_response):
    await my_crawler.fetch()
```

**✅ 正确写法:**
```python
from unittest.mock import patch, MagicMock, AsyncMock

mock_response = MagicMock(status_code=200) # Response 对象本身及其方法通常是同步的
# 正确！覆盖掉原来的方法，使其行为成为一个 AsyncMock
with patch.object(httpx.AsyncClient, 'post', new=AsyncMock(return_value=mock_response)):
    await my_crawler.fetch()
```

使用 `side_effect` 模拟循序多次请求：
```python
with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(side_effect=[mock_1, mock_2])):
    ...
```

## 关键经验
- 针对任何 `async def` 的 Mock，必须保证它被调用时能走协程语境。
- 严格区分 **异步的请求方法** 与 **同步的响应对象**：`httpx.AsyncClient.get` 是异步的（需 `AsyncMock`），但它返回的 `Response` 对象上的 `.json()` 是同步的（需 `MagicMock` 即可）。

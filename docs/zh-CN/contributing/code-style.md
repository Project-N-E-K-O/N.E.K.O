# 代码风格

## Python

- **Python 3.11** — 必须使用此版本；不要使用 3.12+ 的特性
- **类型提示** — 在实际可行处使用，特别是公共 API
- **异步** — 在 FastAPI 处理程序中对 I/O 操作使用 `async/await`
- **导入** — 标准库优先，然后是第三方库，最后是本地模块
- **行长度** — 无严格限制，但请保持合理（约 120 字符）

## JavaScript

- **ES6+** — 使用现代语法（箭头函数、const/let、模板字符串）
- **无框架** — 前端设计上使用原生 JS
- **国际化** — 所有用户可见的字符串应使用语言系统

## 提交信息

尽可能遵循约定式提交规范：

```
feat: add voice preview for custom voices
fix: resolve WebSocket reconnection on character switch
docs: update API reference for memory endpoints
refactor: extract TTS queue logic into separate module
```

## Pull Request

- 保持 PR 专注于单一关注点
- 包含变更内容和原因的描述
- 如适用，关联相关 issue
- 确保 `uv run pytest` 通过

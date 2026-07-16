# 记忆浏览器粒子消散

> **文档性质：current implementation record。** 本功能只是 `/memory_browser` 删除/清空 `recent.json` 对话项时的前端反馈；它不是记忆系统的数据模型，也不能代表 facts、reflections、persona 或索引已被同样删除。

## 当前入口

- `static/js/memory_browser.js`：删除请求、CSRF、DOM 状态与动画；
- 记忆浏览器模板和样式：页面结构与粒子视觉；
- `tests/frontend/test_memory_browser.py`：删除、失败恢复和静态合同。

## 行为合同

1. 用户确认删除后，目标条目进入 pending 状态并禁止重复操作。
2. 客户端携带当前 CSRF token 调用后端变更端点。
3. 只有后端确认成功后才完成消散并从列表移除。
4. 请求失败、CSRF 刷新失败或超时时，恢复条目和按钮状态并显示错误。
5. 清空操作使用同一成功/失败语义，不能先永久移除 DOM 再赌请求成功。

粒子层应使用 `pointer-events: none`，动画结束后清理节点、timer 和 listener。`prefers-reduced-motion` 下应缩短或跳过粒子动画，但仍显示确定的成功/失败状态。

## 数据边界

本页只描述 recent conversation 的 UI 删除反馈。实际记忆数据、事件日志和证据链见记忆架构与 memory RFC。不要从“卡片消散”推断向量索引、长期事实或远端副本已经删除。

## 验证

```bash
uv run pytest tests/frontend/test_memory_browser.py -q
```

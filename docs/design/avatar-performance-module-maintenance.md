# AvatarPerformance 模块维护指南

> **文档性质：current implementation record。** 本页描述本仓库现有的头像表演编排层，不是七日教程的历史施工计划，也不保证所有模型拥有相同动作资源。

## 当前入口

`static/avatar/avatar-performance-stage.js` 导出两个浏览器全局：

- `window.AvatarPerformanceStage`：执行单个 sequence；
- `window.AvatarPerformance`：协调 session、能力和调用方。

它位于教程/业务和具体 avatar driver 之间：上层请求“看向、表情、动作、参数变化”等语义步骤，下层 driver 决定当前模型是否支持。没有能力时应安全 no-op，不得让教程或聊天链路失败。

## 运行合同

- 每段表演属于一个 session；旧 session 的延迟回调不能写入新 session。
- 同一能力使用锁协调，释放必须幂等；取消、异常和 teardown 都要释放。
- sequence 按声明顺序执行，并在每步前检查 session 是否仍有效。
- driver 可以实现 frame、motion、expression、emotion、lookAt、parameter 或 preset 的子集。
- 缺失动作/表情是正常资源差异，不能用某个默认模型的文件名硬编码所有模型。
- reduced-motion 环境应缩短或跳过非必要移动，仍要执行状态清理。
- 表演结束后恢复普通口型、视线、idle 和业务控制权，不能长期占用 avatar。

## 与七日教程的关系

教程的当前编排位于 `static/tutorial/yui-guide/`，通过 director 和 day 脚本调用表演能力。教程生命周期、聊天锁和跨窗口 relay 由教程模块管理；AvatarPerformance 不应复制这些状态机，也不应依赖已经删除的历史设计页。

## 扩展 driver

新增 Live2D、VRM、MMD 或其他载体支持时：

1. 先声明可检测的 capability；
2. 把具体 SDK 调用封装在 driver 内；
3. 对缺失资源返回 no-op/降级结果；
4. 实现取消和恢复；
5. 用同一 sequence 验证不同载体的对偶行为。

不要在 sequence 中读取 SDK 私有字段，也不要为了新载体改变旧 driver 的正常链路。

## 验证

```bash
uv run pytest tests/frontend/test_yui_guide_avatar_performance_flow.py tests/test_agent_rewrite_regression.py tests/test_emotion_heuristic.py -q
uv run python -m py_compile main_routers/pages_router.py config/prompts/prompts_emotion.py
```

浏览器验收还应覆盖：重复启动、途中取消、模型切换、缺失动作、reduced motion 和教程 teardown。

## 维护禁区

- 不绕过 session/锁直接写 avatar；
- 不把调用方私有状态塞进全局 coordinator；
- 不让演出异常阻断普通聊天或模型加载；
- 不把外部 Electron overlay 当成本仓库已验证实现。

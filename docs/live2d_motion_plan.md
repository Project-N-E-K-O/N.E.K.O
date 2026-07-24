# Live2D 待机动作选择与恢复

> **文档性质：current implementation record。** 本页记录本仓库当前可验证的 Live2D 待机动作保存、加载与恢复合同；它不是旧版实施计划。N.E.K.O-PC 的窗口和 preload 行为在外部仓库，本页不声称已验证。

## 当前行为

模型管理页可选择一个 `.motion3.json` 动作并随角色配置保存。规范化存储位置是：

```text
_reserved.avatar.live2d.idle_animation
```

保存请求仍使用兼容字段 `live2d_idle_animation`，后端在 `main_routers/characters_router/live2d_models.py` 校验后写入 reserved schema。`utils/config_manager/reserved_schema.py` 负责旧平铺字段的读取和迁移；新代码不要继续创建新的顶层存储格式。

空字符串或 `null` 表示清除待机动作。非空路径必须是模型资源内允许的相对动作路径，后端会拒绝绝对路径和越界路径。

## 代码入口

- `static/js/model_manager/page-controller.js`：动作选择器、保存 payload、预览和异步状态守卫；
- `main_routers/characters_router/live2d_models.py`：保存与路径校验；
- `utils/config_manager/reserved_schema.py`：reserved schema 与 legacy 兼容；
- `static/app/app-interpage/bootstrap-resources-and-model-reload.js`：主页恢复、motion group 安全注入与模型切换清理；
- `static/live2d/live2d-init.js`：模型 ready 后触发恢复；
- `static/live2d/live2d-model.js`：模型加载、动作和口型运行时；
- `static/live2d/live2d-interaction.js`：交互结束后复用统一待机恢复入口。

## 恢复合同

1. 角色配置读取优先使用 reserved 字段，只为历史数据兼容旧字段。
2. 等待当前 Live2D 模型 ready，再对同一模型执行恢复。
3. 注入动作定义时把 SDK 的 definitions 与已加载 motionGroups 分开；不能把配置对象伪装成已加载实例。
4. 恢复前后检查 load token/模型身份，旧请求不得覆盖新模型。
5. 循环、结束回调和计时器必须通过当前 SDK 能力检测，不依赖某个版本私有字段必然存在。
6. 清除表情、临时交互动作或预览后，统一调用 `restoreLive2DIdleAnimationOnMainPage()`，不要复制另一套恢复逻辑。

## 竞态与性能

- 模型切换、快速重复保存和预览都可能让异步 fetch 过期；每个入口要验证自己的 token。
- `setMouth` 是高频路径，参数索引缓存和 destroyed guard 不能被待机动作逻辑绕过。
- 延迟回调执行前必须确认 core model 仍是原实例。
- 页面卸载或模型销毁时清理循环、回调和临时 motion group。
- 恢复失败应保留普通 idle 行为，不能让整个 Live2D 加载失败。

## 验证

重点回归包括保存/清除、legacy 迁移、非法路径、快速模型切换、预览后恢复、口型与待机动作共存。现有静态合同主要位于 `tests/unit/test_live2d_parameter_persistence_static.py` 及 Live2D 相关前端测试。

```bash
uv run pytest tests/unit/test_live2d_parameter_persistence_static.py -q
```

## 剩余工作

- 新 SDK 版本需要重新验证动作结束回调和循环语义；
- 若未来支持动作列表或随机 idle，应扩展 reserved schema，而不是复用字符串塞入多种类型；
- 外部 Electron 宿主的加载时序需在 N.E.K.O-PC 仓库单独验证。

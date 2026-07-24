# 七日悬浮角色教程：当前实现记录

> **文档性质：current implementation record。** 本页记录本仓库截至当前版本可验证的七日教程实现，不是逐日开发计划。Electron 全局透明 overlay 属于 N.E.K.O-PC；本仓库只能验证 renderer 侧 relay 契约，不能证明外部仓库当前实现。

## 当前代码入口

| 职责 | 当前路径 |
| --- | --- |
| 七日内容 | `static/tutorial/yui-guide/days/day1-home-guide.js` 至 `day7-graduation-guide.js` |
| 场景导演 | `static/tutorial/yui-guide/director/` |
| 通用生命周期 | `static/tutorial/core/` |
| 高亮、光标、转场 | `static/tutorial/visual/` |
| 角色替身与重载 | `static/tutorial/avatar/` |
| 首页接线 | `static/tutorial/yui-guide/` |
| 跨窗口 relay | `static/app/app-interpage/` |

Day 1–7 已经是源码中的独立日模块，不再由文档中的临时 scene 清单或旧 operation 表驱动。修改顺序应是：先定位对应 `days/dayN-*.js`，再检查 director、core、visual 是否需要公共能力；不要在某一天复制生命周期实现。

## 运行模型

`UniversalTutorialManager` 负责启动、完成、跳过、重置和恢复；director 把日模块的 round/scene 交给通用 timeline 与 scene orchestrator；visual controllers 只负责 spotlight、ghost cursor 与转场呈现。

稳定约束：

- 同一时刻只允许一个教程生命周期持有交互接管。
- scene 目标必须通过当前 target/geometry 注册表解析，不能依赖截图坐标。
- 跳过、销毁、`pagehide` 与错误都要走统一清理，释放 timer、listener、overlay、cursor 和临时角色状态。
- 正常完成转场不能在 skip/cancel 路径播放。
- 真实 UI 点击必须由 scene 明确声明；展示入口不等于触发点击。
- 临时切换到教程角色后，无论完成还是中断都必须恢复原角色和可见性。
- `prefers-reduced-motion` 下应缩短或跳过装饰动画，但不能跳过状态提交和清理。

## React 聊天与多窗口边界

教程涉及聊天时，目标是 `frontend/react-neko-chat/` 构建出的唯一 React 聊天 UI。`#chat-container` 是隐藏兼容壳，不能作为教程目标。

浏览器内可使用 `BroadcastChannel('neko_page_channel')` 和同源 `postMessage`；Electron 下 renderer 会把 overlay/cursor 请求 relay 给 preload/主进程。外部聊天窗口、全局透明 overlay、系统鼠标隐藏等宿主行为不在本仓库实现，修改时必须在 N.E.K.O-PC 另行验证。

## 七日内容边界

日模块文件名是当前内容边界：

1. Day 1：首页与基础交互。
2. Day 2：屏幕与语音。
3. Day 3：角色互动。
4. Day 4：陪伴设置。
5. Day 5：个性化。
6. Day 6：Agent 能力。
7. Day 7：毕业与收尾。

具体 scene、台词 key、目标和时序以对应日模块为准，不在文档里复制一份会漂移的逐行清单。

## 状态与兼容

教程状态由 `static/tutorial/core/lifecycle-state-store.js` 与 manager 统一管理。新增状态必须有版本/默认值并允许旧存储缺字段；重置必须通过正式 manager/reset 入口，不能只删一两个 `localStorage` 键。

旧教程脚本或历史 operation 名只能用于迁移读取，不能重新成为事实源。若要兼容旧事件，适配应集中在 normalizer/command registry，而不是散落在日模块中。

## 验证

优先运行：

```powershell
uv run pytest tests/unit/test_avatar_floating_day1_round_contracts.py tests/unit/test_avatar_floating_i18n_contracts.py tests/unit/test_universal_tutorial_manager_static.py tests/unit/test_tutorial_timeline_engine.py tests/unit/test_tutorial_script_normalizer.py -q
```

视觉或浏览器行为再运行相关 `tests/frontend/test_tutorial_*.py`。Electron overlay 与系统光标行为必须在 N.E.K.O-PC 的对应测试和实际多窗口环境中验证。

## 剩余工作规则

- 新教程内容进入现有 Day 文件，不新增第二套“首页教程框架”。
- 公共能力进入 `static/tutorial/core/`、`visual/` 或 `director/`，并补契约测试。
- 新文案必须同步主前端 8 个 locale；冰破流程的独立 locale 也要按其自身 8 文件同步。
- 任何外部宿主改动必须明确记录 N.E.K.O-PC 版本/提交；未检查时写“待外部验证”。

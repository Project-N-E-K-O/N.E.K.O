# 翻译字幕面板

> **文档性质：current implementation record + external host boundary。** 本页记录本仓库共享字幕状态和 Web/独立字幕页。`window.nekoSubtitle` 的原生窗口能力由 N.E.K.O-PC preload 提供，本仓库只能验证调用合同和测试替身。

## 当前入口

- `static/subtitle/subtitle-shared.js`：设置、渲染、面板状态、弹幕布局和 host adapter；
- `static/subtitle/subtitle-window.js`：独立 `/subtitle` 页面；
- `templates/subtitle.html`：独立页面结构；
- `tests/frontend/test_subtitle_incremental.py`：增量文本、弹幕、持久化与 native bridge 回归。

## 状态合同

共享设置包括启用状态、语言、透明度、字体大小、配色、面板位置/尺寸/锁定、交互穿透和弹幕模式。规范化与 localStorage key 由 `subtitle-shared.js` 统一管理；主页和独立页不能维护两套不兼容 schema。

- 文本增量更新不能整页重建设置面板；
- 关闭/禁用后停止动画和跟踪，并清理临时节点；
- 弹幕模式按当前 avatar 头部/面板 geometry 跟踪，但 native bounds 失败时不能随意移动窗口；
- 字体、配色和透明度改变应立即反映且持久化；
- 锁定与 interaction passthrough 是不同状态；
- 页面初始化和重复挂载必须幂等。

## Web 与 Electron

Web 模式直接在页面内布局字幕。独立 Electron 字幕页通过 `window.nekoSubtitle` 请求原生 bounds、窗口移动或穿透。bridge 不存在时必须安全退回 Web 行为；存在测试替身只证明消费者合同，不证明外部 preload 已同步实现。

主页 `/` 与独立 `/subtitle` 使用不同页面/窗口，静态资源和初始化不得假定同一个 DOM。

## 验证

```bash
uv run pytest tests/frontend/test_subtitle_incremental.py -q
```

外部 Electron 验收还需覆盖多显示器、DPI、原生 bounds 失败、窗口穿透和重启后设置恢复。

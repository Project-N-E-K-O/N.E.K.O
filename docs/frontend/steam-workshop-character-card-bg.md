# Steam 创意工坊管理页面视觉优化记录

---

## 问题一：角色卡区块背景显示为黑色（深色模式）

在 `/steam_workshop_manager` 页面「角色卡」标签页中，以下三个区块的背景在深色模式下显示为黑色（`#2d2d2d` / `#333`），需改为透明：

1. **角色卡信息区块**（左上）— `#card-info-preview`
2. **Live2D 预览区块**（右上）— `#live2d-preview-container` 及其控制栏 `#live2d-preview-controls`
3. **标签展示区块**（右下）— `#character-card-tags-wrapper`

### 根本原因

`static/css/dark-mode.css` 中对这些元素有专门的深色模式覆盖规则，使用了 `!important`，优先级高于 HTML 内联样式和普通 CSS 类规则。

### 修改：`static/css/dark-mode.css`

```css
/* 修改前 */
[data-theme="dark"] #card-info-preview,
[data-theme="dark"] #live2d-preview-container {
  background-color: #2d2d2d !important;
  border-color: #444 !important;
}

[data-theme="dark"] #live2d-preview-controls {
  background-color: #333 !important;
  border-top-color: #444 !important;
}

[data-theme="dark"] #character-card-tags-wrapper {
  background-color: #2d2d2d !important;
  border-color: #444 !important;
}

/* 修改后 */
[data-theme="dark"] #card-info-preview,
[data-theme="dark"] #live2d-preview-container {
  background: transparent !important;
}

[data-theme="dark"] #live2d-preview-controls {
  background: transparent !important;
}

[data-theme="dark"] #character-card-tags-wrapper {
  background: transparent !important;
  border-color: #444 !important;
}
```

---

## 问题二：角色卡信息和 Live2D 预览的黑色边框

深色模式下，`#card-info-preview` 和 `#live2d-preview-container` 的边框被覆盖为 `#444`；亮色模式下 HTML 内联样式也包含 `border: 1px solid #eaeaea`，需将边框全部去除。

### 修改：`templates/steam_workshop_manager.html`

```html
<!-- #card-info-preview（约第 179 行）— 删除 border -->
<!-- 修改前 -->
<div id="card-info-preview" style="padding: 12px; border: 1px solid #eaeaea; border-radius: 4px;">
<!-- 修改后 -->
<div id="card-info-preview" style="padding: 12px; border-radius: 4px;">

<!-- #live2d-preview-container（约第 190 行）— 删除 border -->
<!-- 修改前 -->
<div id="live2d-preview-container" style="width: 100%; height: 400px; border: 1px solid #eaeaea; border-radius: 4px; overflow: hidden; display: flex; flex-direction: column; position: relative;">
<!-- 修改后 -->
<div id="live2d-preview-container" style="width: 100%; height: 400px; border-radius: 4px; overflow: hidden; display: flex; flex-direction: column; position: relative;">
```

`dark-mode.css` 中对应的 `border-color: #444 !important` 覆盖已在问题一修改中同步去除。

---

## 问题三：窗口变大时 Live2D 预览被白色遮挡

### 根本原因

`resizePreviewModel` 函数在窗口尺寸变化时只重新计算模型的位置和缩放，但没有同步调用 `pixi_app.renderer.resize()` 更新 PIXI 渲染器的视口尺寸。当容器变大后，渲染器视口停留在旧尺寸，未覆盖的区域因 canvas 透明而透出父元素的白色背景。

### 修改：`static/js/steam_workshop_manager.js`（`initLive2DPreview` 函数内，约第 3902 行）

```javascript
// 修改前
function resizePreviewModel() {
    if (live2dPreviewManager && live2dPreviewManager.currentModel) {
        live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
    }
}

// 修改后（先 resize 渲染器，再重新定位模型）
function resizePreviewModel() {
    const container = document.getElementById('live2d-preview-content');
    if (live2dPreviewManager && live2dPreviewManager.pixi_app && container &&
        container.clientWidth > 0 && container.clientHeight > 0) {
        live2dPreviewManager.pixi_app.renderer.resize(container.clientWidth, container.clientHeight);
    }
    if (live2dPreviewManager && live2dPreviewManager.currentModel) {
        live2dPreviewManager.applyModelSettings(live2dPreviewManager.currentModel, {});
    }
}
```

> **注意**：`live2d-core.js` 内的 `_screenChangeHandler` 只在物理显示器分辨率变化时触发，不响应 Electron 窗口大小变化，因此必须在 `steam_workshop_manager.js` 层处理。

---

## 配套清理（HTML / CSS）

以下改动不影响视觉效果，仅清理冗余样式：

| 文件 | 位置 | 操作 |
|------|------|------|
| `steam_workshop_manager.html:194` | `#live2d-preview-canvas` | 追加 `background: transparent` |
| `steam_workshop_manager.html:207` | `#live2d-preview-controls` | 删除 `background-color: #f0f0f0` |
| `steam_workshop_manager.html:273` | `#character-card-tags-wrapper` | 删除 `background-color: #f9f9f9`、`border`、`border-radius`（改由 CSS 统一管理） |
| `steam_workshop_manager.css:233` | `.character-card-top-row` | 删除 `background-color: #fff` |
| `steam_workshop_manager.css:271` | `.character-card-bottom-row` | 删除 `background-color: #fff` |

---

## 问题四：角色卡信息字体颜色及行分割线

角色卡信息区块（`#card-info-dynamic-content`）的每行数据由 JS 动态生成，字体颜色偏暗（`#555` / `#b0b0b0`），且行与行之间缺少视觉分隔。

### 修改：`static/js/steam_workshop_manager.js`（约第 3760 行）

```javascript
// 修改前
row.style.cssText = `color: ${isDark ? '#b0b0b0' : '#555'}; margin-bottom: 8px;`;

// 修改后（字体改为黑色，每行底部加淡蓝色分割线）
row.style.cssText = `color: ${isDark ? '#e0e0e0' : '#000'}; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid ${isDark ? 'rgba(64, 197, 241, 0.25)' : 'rgba(64, 197, 241, 0.45)'};`;
```

### 修改：`static/css/dark-mode.css`

```css
[data-theme="dark"] #character-card-description {
  color: #e0e0e0 !important;
}
```

---

## 问题五：按钮圆角与角色管理页不一致

Steam 创意工坊管理页的按钮使用 `border-radius: 8px` / `12px`（微圆角），而角色管理页（`chara_manager.css`）统一使用 `border-radius: 999px`（胶囊形）。

### 修改：`static/css/steam_workshop_manager.css`

```css
/* .btn（约第 425 行） */
.btn {
    border-radius: 999px;  /* 修改前: 8px */
}

/* .card-actions button, .card-actions .button（约第 1251 行） */
.card-actions button,
.card-actions .button {
    border-radius: 999px;  /* 修改前: 12px */
}
```

> **参考来源**：`static/css/chara_manager.css` `.btn { border-radius: 999px; }`

---

## 文本框胶囊样式（订阅部分 & 角色卡部分）

将「订阅内容」标签页的搜索框和「角色卡」标签页中的所有文本框/输入框，统一改为与角色管理页（`chara_manager`）一致的胶囊（pill）输入框风格，参考 `.field-row` 样式。

### 视觉特征

| 属性 | 参考来源（`chara_manager.css`） | 应用值 |
|------|------|------|
| 边框颜色 | `.field-row { border: 2px solid #b3e5fc; }` | `#b3e5fc` |
| 聚焦边框 | `.field-row:focus-within { border-color: #40C5F1; }` | `#40C5F1` |
| 文字颜色 | `.field-row input { color: #40C5F1; }` | `#40C5F1` |
| Placeholder | `.field-row input::placeholder { color: #b3e5fc; }` | `#b3e5fc` |
| 标签颜色 | `.field-row-wrapper label { color: #40C5F1; font-weight: 700; }` | `#40C5F1 + bold` |
| 圆角（单行） | `.field-row { border-radius: 50px; }` | `50px` |
| 圆角（多行域/标签区） | `.field-row textarea { border-radius: 48px; }` | `16px`（适配高文本域） |

### 覆盖元素

| 区块 | 元素 | 说明 |
|------|------|------|
| 订阅内容 | `#search-subscription` (`.control-input`) | 搜索框 |
| 订阅内容 | `.filter-label` | 搜索/排序标签文字 |
| 角色卡 | `#character-card-select` | 角色卡选择下拉框 |
| 角色卡 | `#preview-motion-select` | 动作选择下拉框 |
| 角色卡 | `#preview-expression-select` | 表情选择下拉框 |
| 角色卡 | `#character-card-tag-input` | 标签输入框 |
| 角色卡 | `#character-card-description` | 描述文本域（多行，`border-radius: 16px`） |
| 角色卡 | `#character-card-tags-wrapper` | 标签展示区外框（`border-radius: 16px`） |
| 角色卡 | `.control-label` | 表单字段标签文字 |

### 修改：`static/css/steam_workshop_manager.css`（文件末尾）

```css
/* ===== 订阅部分 & 角色卡部分 - 文本框胶囊样式（参考角色管理） ===== */

#subscriptions-content .control-input {
    border: 2px solid #b3e5fc;
    border-radius: 50px;
    color: #40C5F1;
    background: #fff;
    box-shadow: none;
    transition: border-color 0.2s;
}
#subscriptions-content .control-input:focus { outline: none; border-color: #40C5F1; box-shadow: none; }
#subscriptions-content .control-input::placeholder { color: #b3e5fc; }
#subscriptions-content .filter-label { color: #40C5F1; font-weight: 700; }

#character-cards-content .control-input:not(textarea) {
    border: 2px solid #b3e5fc;
    border-radius: 50px;
    color: #40C5F1;
    background: #fff;
    box-shadow: none;
    transition: border-color 0.2s;
}
#character-cards-content .control-input:not(textarea):focus { outline: none; border-color: #40C5F1; box-shadow: none; }
#character-cards-content .control-input:not(textarea)::placeholder { color: #b3e5fc; }
#character-cards-content .control-label { color: #40C5F1; font-weight: 700; font-size: 1.1rem; }

/* 使用更高特异性选择器 #character-cards-content #id 替代 !important */
#character-cards-content #character-card-description {
    border: 2px solid #b3e5fc;
    border-radius: 16px;
    color: #40C5F1;
    background: #fff;
    box-shadow: none;
    transition: border-color 0.2s;
}
#character-cards-content #character-card-description:focus {
    outline: none;
    border-color: #40C5F1;
    box-shadow: none;
}
#character-card-description::placeholder { color: #b3e5fc; }
#character-card-tag-input::placeholder { color: #b3e5fc; }

#character-cards-content #character-card-tags-wrapper {
    border: 2px solid #b3e5fc;
    border-radius: 16px;
    background: #fff;
}
```

> 暗色模式无额外适配，胶囊边框颜色在暗色下保持原样。

---

## 页面整体样式统一

### 背景颜色

将页面整体背景由 `transparent` 改为浅蓝色，与整体配色一致。

**修改：`static/css/steam_workshop_manager.css`**

```css
html, body {
    background: #e1f4ff;  /* 修改前: transparent */
}
```

### 标题栏样式（参考角色管理 `.container-header`）

将 `.page-title-bar` 的样式统一为与角色管理页 `.container-header` 一致的风格：蓝色背景、渐变描边标题文字、图片关闭按钮。

**修改：`static/css/steam_workshop_manager.css`**

```css
.page-title-bar {
    background: #40C5F1;
    padding: 18px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-radius: 0;
    position: sticky;
    top: 0;
    z-index: 100;
    -webkit-app-region: drag;
}

/* h1/h2 渐变描边效果（::before 描边 + ::after 渐变填充，参考 container-header h2） */
.page-title-bar h1,
.page-title-bar h2 {
    margin: 0;
    font-size: 1.5rem;
    font-weight: 600;
    position: relative;
    font-family: 'Comic Neue', 'Source Han Sans CN', 'Noto Sans SC', sans-serif;
    letter-spacing: 1px;
    color: transparent;
    --button-text-stroke-color: #22b3ff;
}
.page-title-bar h1::before,
.page-title-bar h2::before,
.page-title-bar h1::after,
.page-title-bar h2::after {
    /* 与 chara_manager.css .container-header h2 完全相同 */
}

.page-title-bar .close-btn {
    background: transparent;
    border: none;
    cursor: pointer;
    padding: 0;
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
    transition: background 0.2s, transform 0.1s ease;
    -webkit-app-region: no-drag;
}
.page-title-bar .close-btn:hover { background: rgba(255,255,255,0.2); transform: translateY(-1px); }
.page-title-bar .close-btn:active { background: rgba(255,255,255,0.3); transform: translateY(1px) scale(0.95); opacity: 0.9; }
```

**修改：`templates/steam_workshop_manager.html`（约第 20 行）**

```html
<!-- 修改前 -->
<h1 data-i18n="steam.workshopManager">Steam 创意工坊管理 - Project N.E.K.O.</h1>
<button class="close-btn" ... style="width:36px; height:36px; background:#ff5f57; border-radius:50%; ...">
    <svg>...</svg>
</button>

<!-- 修改后 -->
<h1 data-i18n="steam.workshopManager" data-text="Steam 创意工坊管理">Steam 创意工坊管理 - Project N.E.K.O.</h1>
<button class="close-btn" onclick="window.close(); window.history.back();" data-i18n-title="common.close" title="关闭">
    <img src="/static/icons/close_button.png" data-i18n-alt="common.close">
</button>
```

> `data-text` 属性供 CSS `::before` / `::after` 的 `content: attr(data-text)` 使用，提供渐变标题所需文本内容。

---

## 页面布局调整

### sidebar 与 main-content 宽度比例

将侧边栏和主内容区域从固定像素宽度（300px）改为 1:4 的弹性比例：

**修改：`static/css/steam_workshop_manager.css`**

```css
/* sidebar（约第 131 行） */
#sidebar {
    flex: 0 0 20%;  /* 修改前: min-width: 280px; width: 300px; */
    min-width: 200px;
    background: transparent;  /* 修改前: background: #fff; */
}

/* main-content（约第 188 行） */
.main-content {
    flex: 0 0 80%;  /* 修改前: flex: 1; max-width: calc(100% - 300px); */
    background: transparent;  /* 修改前: background: #fff; */
}
```

### 角色卡封面图展示区域

在侧边栏底部添加角色卡封面图展示区域，显示当前选中角色的封面图片。

**修改：`templates/steam_workshop_manager.html`**（约第 53-62 行）

```html
<!-- 角色卡封面图展示区域 -->
<div class="menu-section character-card-cover-section">
    <div id="character-card-cover" class="character-card-cover">
        <img id="character-card-cover-img" src="/static/background/default_character_card.png" alt="角色卡封面">
        <div id="character-card-cover-placeholder" class="cover-placeholder">
            <p data-i18n="steam.selectCharacterForCover">请选择角色查看封面</p>
        </div>
    </div>
</div>
```

**修改：`static/css/steam_workshop_manager.css`**（文件末尾追加）

```css
/* 角色卡封面图展示区域 */
.character-card-cover-section {
    margin-top: 10px;
}

.character-card-cover {
    width: 100%;
    aspect-ratio: 3/4;
    background: transparent;
    border-radius: 8px;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
}

.character-card-cover img {
    width: 100%;
    height: 100%;
    object-fit: contain;  /* 完整显示图片 */
    display: block;
}

/* 响应式：当浏览器过窄时隐藏封面图 */
@media (max-width: 1024px) {
    .character-card-cover-section {
        display: none;
    }
}
```

### tab-contents 背景与透明元素

为 tab-contents 设置背景图，并使其内部元素（除 section-header 外）保持透明：

**修改：`static/css/steam_workshop_manager.css`**

```css
.tab-contents {
    background-image: url('/static/icons/subscriptions-content_bg.png');
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
}

/* tab-contents 内部元素透明背景、无边框（除 section-header 外） */
.tab-contents .tab-content .section-card,
.tab-contents .filter-controls,
.tab-contents .cards-container,
.tab-contents .card,
.tab-contents .empty-state,
.tab-contents .pagination,
.tab-contents .control-input,
.tab-contents .control-select,
.tab-contents #subscriptions-result,
.tab-contents #local-items-list {
    background: transparent;
    border: none;
}

/* 保持 section-header 的背景 */
.tab-contents .section-header {
    background: #fff;
}
```

---

## 订阅内容筛选区域控件优化

### sort-subscription 与 search-subscription 边框样式统一

为 `#sort-subscription` 添加显式 CSS 规则，确保其边框样式与搜索框完全一致。

**修改：`static/css/steam_workshop_manager.css`**

```css
/* sort-subscription 与 search-subscription 边框样式统一 */
#sort-subscription {
    border: 2px solid #b3e5fc;
    border-radius: 50px;
    background: #fff;
    color: #40C5F1;
}

#sort-subscription:focus {
    outline: none;
    border-color: #40C5F1;
    box-shadow: 0 0 0 3px rgba(64, 197, 241, 0.15);
}
```

### 下拉框添加下拉箭头图标

为排序下拉框内部右侧添加 `drop_down_arrow_icon.png` 图标。

**修改：`templates/steam_workshop_manager.html`**

```html
<div class="filter-group">
    <div class="select-input-wrapper">
        <select id="sort-subscription" class="control-select" onchange="applySort(this.value)">
            <!-- 选项省略 -->
        </select>
        <img src="/static/icons/drop_down_arrow_icon.png" class="select-arrow-icon" alt="下拉箭头">
    </div>
</div>
```

**修改：`static/css/steam_workshop_manager.css`**

```css
/* 下拉框图标容器 */
.select-input-wrapper {
    position: relative;
    display: flex;
    align-items: center;
}

.select-input-wrapper .control-select {
    width: 100%;
    padding-right: 40px; /* 为图标留出空间 */
}

.select-arrow-icon {
    position: absolute;
    right: 12px;
    width: 16px;
    height: 16px;
    pointer-events: none;
}
```

### 刷新按钮添加刷新图标

为"刷新订阅内容"按钮内部右侧添加 `roload_icon.png` 图标。

**修改：`templates/steam_workshop_manager.html`**

```html
<div class="filter-group">
    <div class="refresh-button-wrapper">
        <button class="btn btn-primary" onclick="loadSubscriptions()" data-i18n="steam.refresh">刷新订阅内容</button>
        <img src="/static/icons/roload_icon.png" class="refresh-icon" alt="刷新">
    </div>
</div>
```

**修改：`static/css/steam_workshop_manager.css`**

```css
/* 刷新按钮图标容器 */
.refresh-button-wrapper {
    position: relative;
    display: inline-flex;
    align-items: center;
}

.refresh-button-wrapper .btn {
    padding-right: 36px; /* 为图标留出空间 */
}

.refresh-icon {
    position: absolute;
    right: 12px;
    width: 16px;
    height: 16px;
    pointer-events: none;
}
```

### 排序下拉框向右移动

将排序下拉框向右移动，使其位于刷新按钮的左侧（增加左边距，但不越过刷新按钮）。

**修改：`static/css/steam_workshop_manager.css`**

```css
/* 排序下拉框向右移动，但保持在刷新按钮左侧 */
.filter-group:has(#sort-subscription) {
    margin-left: auto;
}
```

---

## 相关文件

- [static/css/steam_workshop_manager.css](../../static/css/steam_workshop_manager.css) — 页面主 CSS（标题栏、背景、按钮、文本框胶囊样式）
- [static/css/dark-mode.css](../../static/css/dark-mode.css) — 深色模式覆盖（背景/边框/字色）
- [static/css/chara_manager.css](../../static/css/chara_manager.css) — 角色管理页样式参考（`.container-header`、`.field-row`、`.btn`）
- [templates/steam_workshop_manager.html](../../templates/steam_workshop_manager.html) — 页面 HTML 结构
- [static/js/steam_workshop_manager.js](../../static/js/steam_workshop_manager.js) — `resizePreviewModel`、动态行样式
- [static/live2d-core.js](../../static/live2d-core.js) — PIXI 初始化（已含 `transparent: true`，无需修改）

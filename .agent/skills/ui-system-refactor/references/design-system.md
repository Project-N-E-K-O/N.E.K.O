# 🐾 Project N.E.K.O. 设计系统规范 (V1.0)

本规范定义了 Project N.E.K.O. 的前端视觉识别系统与交互标准，是所有 UI 组件开发的权威指南。

## 1. 核心设计理念

*   **胶囊化 (Capsule-centric)**：所有交互元素（输入框、按钮、标签）均采用大圆角设计，避免生硬的直角。
*   **品牌蓝 (Neko Blue)**：以明亮的“天蓝色”作为核心识别色。
*   **圆润描边 (Round Stroke)**：大标题和重点按钮文字采用高密度阴影矩阵实现的圆润描边效果。

## 2. CSS 变量定义

### 2.1 颜色系统
```css
:root {
    /* 品牌核心色 */
    --color-n-main: #40C5F1;      /* 主品牌蓝：标题、主按钮、激活态 */
    --color-n-deep: #22b3ff;      /* 描边/深层蓝：文字描边、聚焦光晕 */
    --color-n-light: #e3f4ff;     /* 浅背景蓝：整体背景、容器背景 */
    --color-n-border: #b3e5fc;    /* 辅助边框蓝：胶囊框线、分割线 */
    
    /* 语义状态色 */
    --color-success: #2ecc71;     /* 成功、已安装 */
    --color-error: #ff5252;       /* 错误、删除、危险 */
    --color-warning: #f39c12;     /* 警告、待定 */
    
    /* 文字色 */
    --color-text-main: #40C5F1;   /* 品牌文字色 */
    --color-text-dark: #222222;   /* 正文深色 */
    --color-text-muted: #666666;  /* 辅助文字 */
}
```

### 2.2 圆角与间距
```css
:root {
    --radius-capsule: 50px;       /* 胶囊圆角 */
    --radius-pill: 999px;         /* 药丸圆角 */
    --radius-card: 20px;          /* 容器/卡片圆角 */
    
    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing-md: 12px;
    --spacing-lg: 16px;
    --spacing-xl: 24px;
}
```

## 3. 字体规范

*   **西文/数字**：`'Comic Neue'`, `'Segoe UI'`, `Arial`
*   **中文**：`'Source Han Sans CN'`, `'Noto Sans SC'`, `'微软雅黑'`
*   **技术字段 (API Key/ID)**：必须使用 **`'Courier New', monospace`**

## 4. 组件样式规范

### 4.1 胶囊表单组件 (`.field-row`)
```css
.field-row input, .field-row select {
    border-radius: var(--radius-capsule);
    border: 2px solid var(--color-n-border);
    color: var(--color-n-main);
    padding: 10px 16px;
    transition: all 0.2s ease;
}

.field-row input:focus {
    border-color: var(--color-n-main);
    box-shadow: 0 0 0 3px rgba(64, 197, 241, 0.15);
}
```

### 4.2 特效文本 (Round Stroke Text)
用于 `h2` 标题或特殊按钮。
```css
.round-stroke {
    position: relative;
    color: transparent;
    --button-text-stroke-color: var(--color-n-deep);
}

.round-stroke::before {
    content: attr(data-text);
    position: absolute;
    -webkit-text-stroke: 1px var(--button-text-stroke-color);
    text-shadow: 2px 0 0 var(--button-text-stroke-color), /* ... 20层矩阵 ... */;
    z-index: -1;
}

.round-stroke::after {
    content: attr(data-text);
    position: absolute;
    background: linear-gradient(to bottom, #96e8ff, #e3f4ff, #ffffff);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    z-index: 10;
}
```

## 5. 响应式适配

*   **Tablet (800px)**: 容器宽度调整，内边距缩小。
*   **Mobile (600px)**: `.field-row-wrapper` 变为 `flex-direction: column`，标签全宽。

---
*Last Updated: 2026-01-26*

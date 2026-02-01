# 🐾 Project N.E.K.O. UI 设计规范 (V1.0)

本规范定义了 Project N.E.K.O. 的前端视觉识别系统与交互标准，旨在确保所有功能模块在视觉上高度统一，提升品牌感与用户体验。

---

## 1. 核心设计理念 (Core Philosophy)

*   **胶囊化 (Capsule-centric)**：所有交互元素（输入框、按钮、标签）均采用大圆角设计，避免生硬的直角，营造亲和、现代的视觉感受。
*   **品牌蓝 (Neko Blue)**：以明亮的“天蓝色”作为核心识别色，传达科技感与猫娘主题的灵动感。
*   **圆润描边 (Round Stroke)**：标题和重点按钮文字采用高密度阴影矩阵实现的圆润描边效果，增强层次感。

---

## 2. 色彩系统 (Color Palette)

### 2.1 品牌核心色
| 用途 | 颜色值 | 示例 |
| :--- | :--- | :--- |
| **主品牌蓝 (Main)** | `#40C5F1` | 标题文字、主按钮背景、激活状态边框 |
| **描边/深层蓝 (Deep)** | `#22b3ff` | 文字描边、深色渐变起点、聚焦光晕 |
| **浅背景蓝 (Light)** | `#e3f4ff` | 页面整体背景、容器内部背景 |
| **辅助边框蓝 (Border)** | `#b3e5fc` | 胶囊框线、分割线、占位符颜色 |

### 2.2 语义状态色
| 状态 | 颜色值 | 用途 |
| :--- | :--- | :--- |
| **成功 (Success)** | `#2ecc71` | 成功提示、已安装状态、在线指示 |
| **错误 (Error)** | `#ff5252` | 删除按钮、错误警告、危险操作 |
| **警告 (Warning)** | `#f39c12` | 待定状态、注意提示、中间态 |

---

## 3. 字体与文本规范 (Typography)

### 3.1 字体族 (Font Family)
*   **西文/数字**：优先使用 `'Comic Neue'`, `'Segoe UI'`, `Arial`。
*   **中文**：优先使用 `'Source Han Sans CN'`, `'Noto Sans SC'`, `'微软雅黑'`。
*   **技术/代码字段**：API Key、ID、路径等必须使用 **`'Courier New', monospace`** (等宽字体)。

### 3.2 特效文本 (Round Stroke Text)
用于页面大标题 (`h2`) 或装饰性功能按钮：
*   **技术实现**：通过 `::before` 伪元素配合 20 层 `text-shadow` 矩阵实现。
*   **文字填充**：`linear-gradient(to bottom, #96e8ff, #e3f4ff, #ffffff)`。
*   **描边属性** `-webkit-text-stroke: 1px var(--button-text-stroke-color)`。

---

## 4. 组件规范 (Components)

### 4.1 胶囊输入框与下拉框 (Capsule Form)
*   **形状**：`border-radius: 50px` (或 `999px`)。
*   **边框**：`2px solid #b3e5fc`。
*   **文字**：颜色统一为 `#40C5F1`，字号 `1rem`。
*   **交互**：聚焦时边框变为 `#40C5F1`，并增加 `box-shadow: 0 0 0 3px rgba(64, 197, 241, 0.15)`。

### 4.2 按钮 (Buttons)
*   **标准按钮**：背景 `#40C5F1`，文字白色，`border-radius: 999px`。
*   **危险按钮**：背景 `#ff5252`，文字白色。
*   **特效按钮**：使用 `static/icons/bar_bg_1.png` 作为背景，配合圆润描边文字。
*   **动效**：
    *   悬停：`transform: translateY(-1px); box-shadow: 0 4px 12px rgba(64, 197, 241, 0.3);`
    *   点击：`transform: translateY(1px) scale(0.98); opacity: 0.95;`

### 4.3 容器与卡片 (Containers & Cards)
*   **主容器**：`max-width: 800px~900px`，`border-radius: 20px`，大阴影。
*   **内容卡片**：背景白色或 `#f0f8ff`，`border: 2px solid #b3e5fc`，`border-radius: 20px`。

---

## 5. 响应式与交互 (UX & Responsive)

### 5.1 滚动条样式 (Scrollbar)
```css
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: rgba(240, 248, 255, 0.3); }
::-webkit-scrollbar-thumb { background: rgba(150, 232, 255, 0.6); border-radius: 4px; }
```

### 5.2 移动端适配
*   **断点**：`800px` (平板) / `600px` (手机)。
*   **策略**：表单标签与输入框由左右排列转换为上下垂直排列，宽度自动占满 `100%`。

---

## 6. 代码实践建议
1.  **复用性**：在编写新 CSS 时，优先参考 `chara_manager.css` 的结构。
2.  **单位**：布局优先使用 `rem` 或 `px`，确保在不同缩放下的稳定性。
3.  **变量**：建议在 `:root` 中定义常用的品牌色变量。

---
*Last Updated: 2026-01-26*

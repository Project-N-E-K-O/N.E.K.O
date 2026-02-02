# N.E.K.O 引导系统规范文档

## 📋 目录

1. [系统概述](#系统概述)
2. [技术架构](#技术架构)
3. [核心配置](#核心配置)
4. [页面引导配置](#页面引导配置)
5. [引导步骤规范](#引导步骤规范)
6. [存储管理](#存储管理)
7. [事件处理](#事件处理)
8. [样式定制](#样式定制)
9. [最佳实践](#最佳实践)
10. [常见问题](#常见问题)

---

## 系统概述

N.E.K.O 引导系统基于 **driver.js v1.0+** 构建，提供统一的新手引导体验。系统支持多页面、自动检测、全屏模式、自动滚动等高级功能。

### 核心特性

- ✅ **多页面支持**：支持 7 个不同页面的独立引导
- ✅ **自动页面检测**：根据 URL 自动识别当前页面类型
- ✅ **首次访问触发**：使用 localStorage 记录用户引导状态
- ✅ **全屏模式**：提供沉浸式引导体验
- ✅ **自动滚动**：自动滚动到目标元素
- ✅ **元素可见性检测**：自动显示隐藏的元素
- ✅ **浮动工具栏保护**：防止引导过程中工具栏被隐藏
- ✅ **国际化支持**：支持多语言（通过 window.t 函数）

### 支持的页面

| 页面类型 | 路径匹配 | 引导步骤数 | localStorage 键 | 说明 |
|---------|---------|-----------|----------------|------|
| 主页 | `/`, `/index.html` | 22 步 | `neko_tutorial_home` | 无全屏提示 |
| 模型管理 (Live2D) | `/model_manager`, `/l2d` | 8 步 | `neko_tutorial_model_manager_live2d` | 根据当前模型类型动态调整 |
| 模型管理 (VRM) | `/model_manager`, `/l2d` | 7 步 | `neko_tutorial_model_manager_vrm` | 根据当前模型类型动态调整 |
| 角色管理 | `/chara_manager` | 13 步 | `neko_tutorial_chara_manager` | 需要全屏提示 |
| API 设置 | `/api_key`, `/settings` | 7 步 | `neko_tutorial_settings` | - |
| 语音克隆 | `/voice_clone` | 6 步 | `neko_tutorial_voice_clone` | - |
| Steam Workshop | `/steam_workshop` | 5 步 | `neko_tutorial_steam_workshop` | - |
| 内存浏览器 | `/memory_browser` | 6 步 | `neko_tutorial_memory_browser` | - |

**注意**：模型管理页面会根据当前选择的模型类型（Live2D 或 VRM）使用不同的 localStorage 键，这样用户可以分别体验两种模型类型的引导。

---

## 技术架构

### 文件结构

```
N.E.K.O/
├── static/
│   └── universal-tutorial-manager.js    # 通用引导管理器
├── templates/
│   ├── index.html                       # 主页（引入引导系统）
│   ├── model_manager.html               # 模型管理页面
│   ├── chara_manager.html               # 角色管理页面
│   ├── api_key_settings.html            # API 设置页面
│   ├── voice_clone.html                 # 语音克隆页面
│   ├── steam_workshop_manager.html      # Steam Workshop 页面
│   └── memory_browser.html              # 内存浏览器页面
└── docs/
    └── TUTORIAL_SYSTEM_SPECIFICATION.md # 本规范文档
```

### 类结构

```javascript
class UniversalTutorialManager {
    // 核心属性
    STORAGE_KEY_PREFIX = 'neko_tutorial_'
    driver = null                    // driver.js 实例
    isInitialized = false            // 初始化状态
    isTutorialRunning = false        // 运行状态
    currentPage = ''                 // 当前页面类型
    currentStep = 0                  // 当前步骤索引

    // 核心方法
    detectPage()                     // 检测页面类型
    waitForDriver()                  // 等待 driver.js 加载
    initDriver()                     // 初始化 driver.js
    checkAndStartTutorial()          // 检查并启动引导
    getStepsForPage()                // 获取页面引导步骤
    startTutorial()                  // 启动引导
    onStepChange()                   // 步骤变化回调
    onTutorialEnd()                  // 引导结束回调
    restartTutorial()                // 重新启动引导
    resetAllTutorials()              // 重置所有引导状态
}
```

---

## 核心配置

### Driver.js 配置

```javascript
this.driver = new DriverClass({
    padding: 8,                      // 高亮区域内边距（像素）
    allowClose: true,                // 允许用户关闭引导
    overlayClickNext: false,         // 点击遮罩层不会进入下一步
    animate: true,                   // 启用动画效果
    className: 'neko-tutorial-driver', // 自定义 CSS 类名
    disableActiveInteraction: false  // 允许与高亮元素交互
});
```

### 全屏模式配置

系统支持为特定页面启用全屏提示。在 `startTutorial()` 方法中配置：

```javascript
// 需要全屏提示的页面列表
const pagesNeedingFullscreen = [
    // 'home',              // 主页 - 不需要全屏
    // 'model_manager',     // 模型管理 - 根据需要添加
    // 'chara_manager',     // 角色管理 - 根据需要添加
];
```

**默认行为**：所有页面都**不显示**全屏提示，直接开始引导。如需为某个页面启用全屏提示，将页面类型添加到 `pagesNeedingFullscreen` 数组中。

### 配置说明

| 参数 | 类型 | 默认值 | 说明 |
|-----|------|--------|------|
| `padding` | number | 8 | 高亮区域与元素边缘的距离 |
| `allowClose` | boolean | true | 是否允许用户点击关闭按钮 |
| `overlayClickNext` | boolean | false | 点击遮罩层是否进入下一步 |
| `animate` | boolean | true | 是否启用过渡动画 |
| `className` | string | 'neko-tutorial-driver' | 自定义 CSS 类名 |
| `disableActiveInteraction` | boolean | false | 是否禁用与高亮元素的交互 |

---

## 页面引导配置

### 添加新页面引导

要为新页面添加引导，需要完成以下步骤：

#### 1. 在 `detectPage()` 方法中添加页面检测逻辑

```javascript
detectPage() {
    const path = window.location.pathname;

    // 添加新页面检测
    if (path.includes('your_new_page')) {
        return 'your_new_page';
    }

    return 'unknown';
}
```

#### 2. 在 `getStepsForPage()` 方法中添加配置映射

```javascript
getStepsForPage() {
    const configs = {
        home: this.getHomeSteps(),
        your_new_page: this.getYourNewPageSteps(), // 添加新页面
    };

    return configs[this.currentPage] || [];
}
```

#### 3. 创建页面引导步骤方法

```javascript
/**
 * 新页面引导步骤
 */
getYourNewPageSteps() {
    return [
        {
            element: '#element-selector',
            popover: {
                title: '步骤标题',
                description: '步骤描述',
                side: 'bottom',      // 可选：top, right, bottom, left
                align: 'center'      // 可选：start, center, end
            }
        },
        // 更多步骤...
    ];
}
```

#### 4. 在 HTML 模板中引入引导系统

```html
<!-- 在页面底部引入 -->
<script src="/static/universal-tutorial-manager.js"></script>
<script>
    document.addEventListener('DOMContentLoaded', function() {
        if (typeof initUniversalTutorialManager === 'function') {
            initUniversalTutorialManager();
        }
    });
</script>
```

#### 5. 更新 `resetAllTutorials()` 方法

```javascript
resetAllTutorials() {
    const pages = [
        'home',
        'model_manager',
        'chara_manager',
        'settings',
        'voice_clone',
        'steam_workshop',
        'memory_browser',
        'your_new_page'  // 添加新页面
    ];
    pages.forEach(page => {
        localStorage.removeItem(this.STORAGE_KEY_PREFIX + page);
    });
}
```

---

## 引导步骤规范

### 基本步骤结构

```javascript
{
    element: '#element-id',           // 必需：CSS 选择器
    popover: {
        title: '步骤标题',            // 必需：步骤标题
        description: '步骤描述',      // 必需：步骤描述
        side: 'bottom',               // 可选：弹窗位置
        align: 'center'               // 可选：弹窗对齐方式
    },
    action: 'click'                   // 可选：自动执行的操作
}
```

### 参数详解

#### `element` (必需)

- **类型**：`string`
- **说明**：目标元素的 CSS 选择器
- **示例**：
  ```javascript
  element: '#live2d-container'           // ID 选择器
  element: '.catgirl-block:first-child'  // 类选择器 + 伪类
  element: 'input[name="档案名"]'         // 属性选择器
  ```

#### `popover.title` (必需)

- **类型**：`string`
- **说明**：步骤标题，建议包含 emoji 增强视觉效果
- **最佳实践**：
  - 使用 emoji 前缀（如 👋、💬、🎮）
  - 简洁明了，不超过 15 个字符
  - 支持国际化：`window.t ? window.t('key', 'default') : 'default'`

#### `popover.description` (必需)

- **类型**：`string`
- **说明**：步骤详细描述
- **最佳实践**：
  - 清晰说明该元素的功能和用途
  - 1-3 句话，不超过 100 个字符
  - 使用友好的语气
  - 支持国际化

#### `popover.side` (可选)

- **类型**：`'top' | 'right' | 'bottom' | 'left'`
- **默认值**：自动计算
- **说明**：弹窗相对于元素的位置
- **选择建议**：
  - 顶部元素：使用 `'bottom'`
  - 底部元素：使用 `'top'`
  - 左侧元素：使用 `'right'`
  - 右侧元素：使用 `'left'`

#### `popover.align` (可选)

- **类型**：`'start' | 'center' | 'end'`
- **默认值**：`'center'`
- **说明**：弹窗在指定方向上的对齐方式

#### `action` (可选)

- **类型**：`'click'`
- **说明**：进入该步骤时自动执行的操作
- **用途**：自动展开折叠的面板、打开下拉菜单等
- **示例**：
  ```javascript
  {
      element: '.fold-toggle',
      popover: { /* ... */ },
      action: 'click'  // 自动点击展开
  }
  ```

### 步骤设计原则

1. **循序渐进**：从主要功能到次要功能，从简单到复杂
2. **重点突出**：优先介绍核心功能和常用操作
3. **避免过载**：单个页面引导步骤不超过 25 步
4. **逻辑清晰**：按照用户的操作流程顺序设计
5. **友好提示**：使用积极、鼓励的语言

### 示例：完整的引导步骤

```javascript
getExampleSteps() {
    return [
        // 欢迎步骤
        {
            element: '#main-container',
            popover: {
                title: '👋 欢迎使用',
                description: '这是一个简短的引导，帮助你快速了解主要功能。',
                side: 'bottom',
                align: 'center'
            }
        },

        // 功能介绍
        {
            element: '#feature-button',
            popover: {
                title: '🎯 核心功能',
                description: '点击这个按钮可以访问核心功能。这是最常用的操作之一。',
                side: 'right',
                align: 'start'
            }
        },

        // 自动展开
        {
            element: '#advanced-panel-toggle',
            popover: {
                title: '⚙️ 高级设置',
                description: '点击展开高级设置面板。这里包含更多自定义选项。',
                side: 'left',
                align: 'center'
            },
            action: 'click'  // 自动点击展开
        },

        // 完成步骤
        {
            element: '#main-container',
            popover: {
                title: '✅ 引导完成',
                description: '恭喜！你已经了解了所有主要功能。现在可以开始使用了。',
                side: 'bottom',
                align: 'center'
            }
        }
    ];
}
```

---

## 存储管理

### 存储键命名规范

```javascript
// 格式：neko_tutorial_{page_type}
'neko_tutorial_home'              // 主页引导状态
'neko_tutorial_model_manager'     // 模型管理页面引导状态
'neko_tutorial_chara_manager'     // 角色管理页面引导状态
// ... 其他页面
```

### 存储值

- `'true'`：用户已完成该页面的引导
- `null` 或不存在：用户未完成引导

### 相关方法

```javascript
// 检查用户是否已看过引导
hasSeenTutorial(page = null) {
    const targetPage = page || this.currentPage;
    const storageKey = this.STORAGE_KEY_PREFIX + targetPage;
    return localStorage.getItem(storageKey) === 'true';
}

// 重置单个页面的引导状态
restartTutorial() {
    const storageKey = this.STORAGE_KEY_PREFIX + this.currentPage;
    localStorage.removeItem(storageKey);
    // ...
}

// 重置所有页面的引导状态
resetAllTutorials() {
    const pages = ['home', 'model_manager', /* ... */];
    pages.forEach(page => {
        localStorage.removeItem(this.STORAGE_KEY_PREFIX + page);
    });
}
```

---

## 事件处理

### Driver.js 事件

```javascript
// 引导销毁事件（用户完成或跳过引导）
this.driver.on('destroy', () => this.onTutorialEnd());

// 下一步事件
this.driver.on('next', () => this.onStepChange());

// 上一步事件（可选）
this.driver.on('previous', () => this.onStepChange());
```

### 自定义事件处理

#### `onStepChange()` - 步骤变化回调

```javascript
onStepChange() {
    this.currentStep = this.driver.currentStep || 0;
    console.log(`[Tutorial] 当前步骤: ${this.currentStep + 1}`);

    // 获取当前步骤配置
    const steps = this.getStepsForPage();
    if (this.currentStep < steps.length) {
        const currentStepConfig = steps[this.currentStep];
        const element = document.querySelector(currentStepConfig.element);

        if (element) {
            // 检查元素可见性
            if (!this.isElementVisible(element)) {
                this.showElementForTutorial(element, currentStepConfig.element);
            }

            // 执行自动操作
            if (currentStepConfig.action === 'click') {
                setTimeout(() => {
                    element.click();
                }, 300);
            }
        }
    }
}
```

#### `onTutorialEnd()` - 引导结束回调

```javascript
onTutorialEnd() {
    // 1. 重置运行标志
    this.isTutorialRunning = false;

    // 2. 退出全屏模式
    this.exitFullscreenMode();

    // 3. 保存引导完成状态
    const storageKey = this.STORAGE_KEY_PREFIX + this.currentPage;
    localStorage.setItem(storageKey, 'true');

    // 4. 清除全局标记
    window.isInTutorial = false;

    // 5. 恢复页面交互
    // - 恢复对话框拖动
    // - 恢复 Live2D 模型拖动
    // - 清除浮动工具栏保护定时器
    // ...
}
```

---

## 样式定制

### CSS 类名

引导系统使用自定义类名 `neko-tutorial-driver`，可以通过 CSS 进行样式定制。

### 推荐样式

```css
/* 自定义引导遮罩层 */
.neko-tutorial-driver .driver-overlay {
    background: rgba(0, 0, 0, 0.8);
    backdrop-filter: blur(4px);
}

/* 自定义弹窗样式 */
.neko-tutorial-driver .driver-popover {
    background: rgba(30, 30, 40, 0.95);
    border: 2px solid #44b7fe;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(10px);
}

/* 自定义标题样式 */
.neko-tutorial-driver .driver-popover-title {
    color: #44b7fe;
    font-size: 18px;
    font-weight: 600;
}

/* 自定义描述样式 */
.neko-tutorial-driver .driver-popover-description {
    color: rgba(255, 255, 255, 0.85);
    line-height: 1.6;
}

/* 自定义按钮样式 */
.neko-tutorial-driver .driver-popover-next-btn {
    background: linear-gradient(135deg, #44b7fe 0%, #40C5F1 100%);
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
}

.neko-tutorial-driver .driver-popover-next-btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(68, 183, 254, 0.4);
}

/* 自定义关闭按钮 */
.neko-tutorial-driver .driver-popover-close-btn {
    color: rgba(255, 255, 255, 0.6);
    transition: color 0.2s ease;
}

.neko-tutorial-driver .driver-popover-close-btn:hover {
    color: #44b7fe;
}
```

---

## 最佳实践

### 1. 引导时机

- ✅ **首次访问自动触发**：新用户首次访问时自动启动引导
- ✅ **延迟启动**：等待 DOM 完全加载后延迟 1.5 秒启动
- ✅ **全屏提示**：引导前提示用户进入全屏模式

### 2. 元素选择

- ✅ **使用稳定的选择器**：优先使用 ID 选择器
- ✅ **避免动态选择器**：避免依赖可能变化的类名或结构
- ✅ **检查元素存在性**：启动前过滤不存在的元素

```javascript
const validSteps = steps.filter(step => {
    const element = document.querySelector(step.element);
    if (!element) {
        console.warn(`[Tutorial] 元素不存在: ${step.element}`);
        return false;
    }
    return true;
});
```

### 3. 元素可见性处理

- ✅ **自动显示隐藏元素**：引导时自动显示 `display: none` 的元素
- ✅ **保护关键元素**：防止浮动工具栏等关键元素被自动隐藏
- ✅ **恢复原始状态**：引导结束后恢复元素原始样式

### 4. 自动滚动

- ✅ **检查视口**：判断元素是否在可见区域
- ✅ **平滑滚动**：使用 `behavior: 'smooth'` 提供流畅体验
- ✅ **居中显示**：滚动时将元素居中显示

### 5. 全屏模式

- ✅ **提供选择**：让用户选择是否进入全屏
- ✅ **兼容性处理**：支持多种浏览器的全屏 API
- ✅ **自动退出**：引导结束后自动退出全屏

### 6. 国际化

```javascript
// 使用国际化函数
title: window.t ? window.t('tutorial.step1.title', '👋 欢迎') : '👋 欢迎'
```

### 7. 错误处理

```javascript
try {
    this.driver = new DriverClass({ /* ... */ });
    this.isInitialized = true;
} catch (error) {
    console.error('[Tutorial] driver.js 初始化失败:', error);
}
```

### 8. 防止重复启动

```javascript
if (this.isTutorialRunning) {
    console.warn('[Tutorial] 引导已在运行中，跳过重复启动');
    return;
}
this.isTutorialRunning = true;
```

### 9. 日志记录

- ✅ 使用统一的日志前缀 `[Tutorial]`
- ✅ 记录关键操作和状态变化
- ✅ 使用不同级别：`console.log`、`console.warn`、`console.error`

### 10. 性能优化

- ✅ **按需加载**：只在需要时初始化引导系统
- ✅ **轮询优化**：使用合理的轮询间隔（100ms）和超时时间（10s）
- ✅ **清理资源**：引导结束后清理定时器和事件监听器

---

## 常见问题

### Q1: 引导不自动启动？

**可能原因：**
1. driver.js 未加载
2. 元素不存在
3. 用户已完成引导

**解决方案：**
```javascript
// 检查 driver.js 是否加载
console.log('driver.js 已加载:', typeof window.driver !== 'undefined');

// 检查引导状态
console.log('已完成引导:', tutorialManager.hasSeenTutorial());

// 重置引导状态
tutorialManager.restartTutorial();
```

### Q2: 元素被遮挡或不可见？

**解决方案：**
```javascript
// 系统会自动处理隐藏元素
// 如果仍有问题，检查元素的 z-index 和 position
```

### Q3: 如何跳过某个页面的引导？

**解决方案：**
```javascript
// 手动标记为已完成
localStorage.setItem('neko_tutorial_home', 'true');
```

### Q4: 如何重置所有引导？

**解决方案：**
```javascript
// 使用全局实例
window.universalTutorialManager.resetAllTutorials();

// 或者手动清除
localStorage.removeItem('neko_tutorial_home');
localStorage.removeItem('neko_tutorial_model_manager');
// ...
```

### Q5: 如何自定义引导样式？

**解决方案：**
在页面的 CSS 文件中添加自定义样式（参考"样式定制"章节）。

### Q6: 引导过程中页面卡顿？

**可能原因：**
- 步骤过多
- 自动滚动频繁
- 元素渲染复杂

**解决方案：**
- 减少引导步骤数量
- 优化页面性能
- 增加步骤间的延迟

### Q7: 如何在引导中执行自定义操作？

**解决方案：**
```javascript
{
    element: '#my-element',
    popover: { /* ... */ },
    action: 'click'  // 支持 'click' 操作
}

// 如需更复杂的操作，在 onStepChange() 中添加逻辑
```

### Q8: 移动端适配问题？

**建议：**
- 使用响应式选择器
- 测试不同屏幕尺寸
- 考虑禁用移动端的全屏模式

---

## 附录

### A. Driver.js 官方文档

- 官网：https://driverjs.com/
- GitHub：https://github.com/kamranahmedse/driver.js
- NPM：https://www.npmjs.com/package/driver.js

### B. 相关文件

- 引导管理器：`/static/universal-tutorial-manager.js`
- 主页模板：`/templates/index.html`
- 其他页面模板：`/templates/*.html`

### C. 版本历史

| 版本 | 日期 | 变更说明 |
|-----|------|---------|
| 2.0.0 | 2024-02 | 统一为通用引导系统，支持多页面 |
| 1.0.0 | 2024-01 | 初始版本，仅支持主页 |

---

## 维护者

如有问题或建议，请联系项目维护团队。

**文档版本**：2.0.0
**最后更新**：2024-02-02

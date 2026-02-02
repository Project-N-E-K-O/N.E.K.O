# N.E.K.O 通用新手引导系统 - 集成指南

## 概述

`universal-tutorial-manager.js` 是一个通用的引导系统，支持所有页面的新手引导。

**特点**：
- ✅ 自动检测当前页面
- ✅ 为每个页面独立管理引导状态
- ✅ 支持多页面配置
- ✅ localStorage 记录每个页面的引导状态

---

## 支持的页面

| 页面 | 路径 | 页面类型 |
|------|------|---------|
| 主页 | `/` 或 `/index.html` | `home` |
| 模型管理 | `/model_manager` 或 `/l2d` | `model_manager` |
| 角色管理 | `/chara_manager` | `chara_manager` |
| 设置 | `/api_key` 或 `/settings` | `settings` |
| 语音克隆 | `/voice_clone` | `voice_clone` |
| Steam Workshop | `/steam_workshop` | `steam_workshop` |
| 内存浏览器 | `/memory_browser` | `memory_browser` |

---

## 集成步骤

### 步骤 1：在页面中引入脚本

在 HTML 的 `</body>` 前添加：

```html
<!-- Driver.js 库 -->
<script src="/static/libs/driver.min.js"></script>
<link rel="stylesheet" href="/static/libs/driver.min.css">

<!-- 通用教程管理器 -->
<script src="/static/universal-tutorial-manager.js"></script>

<!-- 初始化 -->
<script>
    document.addEventListener('DOMContentLoaded', function() {
        if (typeof initUniversalTutorialManager === 'function') {
            initUniversalTutorialManager();
        }
    });
</script>
```

### 步骤 2：为页面添加引导步骤

编辑 `universal-tutorial-manager.js`，找到对应页面的方法，添加步骤配置。

例如，为模型管理页面添加引导：

```javascript
getModelManagerSteps() {
    return [
        {
            element: '#model-list',  // 你的元素选择器
            popover: {
                title: '📋 模型列表',
                description: '这里显示所有可用的模型。点击选择要使用的模型。',
            }
        },
        {
            element: '#model-preview',
            popover: {
                title: '👁️ 模型预览',
                description: '这是选中模型的实时预览。',
            }
        },
        // 添加更多步骤...
    ];
}
```

---

## 使用方法

### 自动启动

首次访问页面时，引导会自动启动（如果该页面的引导未被标记为已看过）。

### 手动启动

在浏览器控制台执行：

```javascript
// 重新启动当前页面的引导
window.universalTutorialManager.restartTutorial();

// 重置所有页面的引导状态
window.universalTutorialManager.resetAllTutorials();

// 检查是否已看过某个页面的引导
window.universalTutorialManager.hasSeenTutorial('home');  // true/false

// 手动启动引导
window.universalTutorialManager.startTutorial();
```

---

## 页面检测逻辑

系统通过 `window.location.pathname` 自动检测当前页面：

```javascript
detectPage() {
    const path = window.location.pathname;

    if (path === '/' || path === '/index.html') {
        return 'home';
    }
    if (path.includes('model_manager') || path.includes('l2d')) {
        return 'model_manager';
    }
    // ... 其他页面
}
```

如果你的页面路径不同，需要修改这个方法。

---

## localStorage 键名规则

每个页面的引导状态存储在 localStorage 中，键名格式为：

```
neko_tutorial_{page_type}
```

例如：
- `neko_tutorial_home` - 主页引导状态
- `neko_tutorial_model_manager` - 模型管理页面引导状态
- `neko_tutorial_chara_manager` - 角色管理页面引导状态

---

## 添加新页面的步骤

### 1. 在 `detectPage()` 中添加页面检测

```javascript
detectPage() {
    const path = window.location.pathname;

    // ... 其他页面

    // 新页面
    if (path.includes('my_new_page')) {
        return 'my_new_page';
    }

    return 'unknown';
}
```

### 2. 在 `getStepsForPage()` 中添加配置

```javascript
getStepsForPage() {
    const configs = {
        // ... 其他页面
        my_new_page: this.getMyNewPageSteps(),  // 添加这一行
    };

    return configs[this.currentPage] || [];
}
```

### 3. 添加步骤方法

```javascript
getMyNewPageSteps() {
    return [
        {
            element: '#element-id',
            popover: {
                title: '标题',
                description: '描述文本',
            }
        },
        // 更多步骤...
    ];
}
```

### 4. 在页面中集成

在你的新页面 HTML 中添加：

```html
<!-- Driver.js 库 -->
<script src="/static/libs/driver.min.js"></script>
<link rel="stylesheet" href="/static/libs/driver.min.css">

<!-- 通用教程管理器 -->
<script src="/static/universal-tutorial-manager.js"></script>

<!-- 初始化 -->
<script>
    document.addEventListener('DOMContentLoaded', function() {
        if (typeof initUniversalTutorialManager === 'function') {
            initUniversalTutorialManager();
        }
    });
</script>
```

---

## 常见问题

### Q: 引导不显示？

**A**: 检查以下几点：
1. 确保 driver.js 已加载：`console.log(window.driver)`
2. 确保元素存在：`document.querySelector('#element-id')`
3. 查看控制台是否有错误信息

### Q: 如何禁用某个页面的自动引导？

**A**: 在 `checkAndStartTutorial()` 中添加条件：

```javascript
checkAndStartTutorial() {
    // 禁用某些页面的自动引导
    if (this.currentPage === 'settings') {
        return;
    }

    const storageKey = this.STORAGE_KEY_PREFIX + this.currentPage;
    const hasSeen = localStorage.getItem(storageKey);

    if (!hasSeen) {
        setTimeout(() => {
            this.startTutorial();
        }, 1500);
    }
}
```

### Q: 如何修改引导延迟时间？

**A**: 修改 `checkAndStartTutorial()` 中的延迟时间（单位：毫秒）：

```javascript
setTimeout(() => {
    this.startTutorial();
}, 2000);  // 改为 2 秒
```

---

## 文件位置

```
N.E.K.O/
├── static/
│   ├── libs/
│   │   ├── driver.min.js
│   │   └── driver.min.css
│   ├── tutorial-manager.js          (主页专用)
│   ├── universal-tutorial-manager.js (通用系统)
│   ├── css/
│   │   └── tutorial-styles.css
│   └── ...
└── ...
```

---

**现在可以为所有页面添加引导了！** 🎉

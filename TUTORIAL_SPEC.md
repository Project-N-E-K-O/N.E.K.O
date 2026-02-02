# N.E.K.O 新手引导系统规范文档

**项目**: N.E.K.O (二次元/元宇宙风格 Web 应用)
**功能**: 基于 driver.js v1.0+ 的新手引导系统
**创建日期**: 2026-02-02
**状态**: ✅ 已完成集成

---

## 📋 目录

1. [系统概述](#系统概述)
2. [文件清单](#文件清单)
3. [集成步骤](#集成步骤)
4. [配置说明](#配置说明)
5. [使用指南](#使用指南)
6. [故障排查](#故障排查)
7. [后续维护](#后续维护)

---

## 系统概述

### 功能特性

✨ **自动首次访问检测**
- 使用 `localStorage` 记录用户是否已看过引导
- 键名: `neko_has_seen_tutorial`
- 只在首次访问时自动触发（延迟 1.5 秒）

🎨 **深色磨砂风格**
- 主题色: 蓝色 `#44b7fe` (与项目风格一致)
- 磨砂玻璃效果: `backdrop-filter: blur(10px)`
- 高亮框脉冲动画效果
- 响应式设计 (移动设备适配)

🌍 **多语言支持**
- 集成 i18next 翻译系统
- 支持语言: 中文简体、中文繁体、英文、日文
- 自动根据用户语言显示对应文案

📱 **无障碍支持**
- ARIA 标签支持
- 键盘导航支持
- 焦点可见性支持

---

## 文件清单

### 新增文件

| 文件路径 | 说明 | 大小 |
|---------|------|------|
| `static/tutorial-manager.js` | 核心教程管理器类 | ~8KB |
| `static/css/tutorial-styles.css` | 深色磨砂风格定制 | ~6KB |
| `TUTORIAL_SPEC.md` | 本规范文档 | - |

### 修改文件

| 文件路径 | 修改内容 |
|---------|---------|
| `templates/index.html` | 添加 driver.js CDN 和教程样式链接 |
| `static/app.js` | 在 DOMContentLoaded 事件中初始化教程管理器 |
| `static/locales/zh-CN.json` | 添加教程翻译 (中文简体) |
| `static/locales/zh-TW.json` | 添加教程翻译 (中文繁体) |
| `static/locales/en.json` | 添加教程翻译 (英文) |
| `static/locales/ja.json` | 添加教程翻译 (日文) |

---

## 集成步骤

### ✅ 已完成的集成

#### 1. 核心模块创建
```
✓ 创建 static/tutorial-manager.js
  - TutorialManager 类定义
  - 自动检测和启动逻辑
  - localStorage 管理
  - 事件处理
```

#### 2. 样式定制
```
✓ 创建 static/css/tutorial-styles.css
  - driver.js 全局样式覆盖
  - 磨砂玻璃效果
  - 蓝色主题配色
  - 响应式设计
  - 动画效果
```

#### 3. HTML 集成
```
✓ 修改 templates/index.html
  - 在 <head> 中添加:
    * driver.js CDN 脚本
    * driver.js 默认样式
    * tutorial-styles.css 定制样式
  - 在 </body> 前添加:
    * tutorial-manager.js 脚本
```

#### 4. 应用初始化
```
✓ 修改 static/app.js
  - 在 DOMContentLoaded 事件中调用 initTutorialManager()
  - 添加错误处理和日志记录
```

#### 5. 多语言翻译
```
✓ 修改所有翻译文件:
  - static/locales/zh-CN.json
  - static/locales/zh-TW.json
  - static/locales/en.json
  - static/locales/ja.json

  添加内容:
  "tutorial": {
    "step1": { "title": "...", "desc": "..." },
    "step2": { "title": "...", "desc": "..." },
    "step3": { "title": "...", "desc": "..." },
    "step4": { "title": "...", "desc": "..." },
    "completed": "..."
  }
```

---

## 配置说明

### 引导步骤配置

教程包含 4 个步骤，在 `tutorial-manager.js` 的 `getSteps()` 方法中定义:

```javascript
[
  {
    element: '#live2d-container',      // 虚拟伙伴容器
    popover: {
      title: '👋 欢迎来到 N.E.K.O',
      description: '这是你的虚拟伙伴...',
      side: 'left',
      align: 'center'
    }
  },
  {
    element: '#chat-container',        // 对话区域
    popover: { ... }
  },
  {
    element: '#textInputBox',          // 文本输入框
    popover: { ... }
  },
  {
    element: '#button-group',          // 按钮组
    popover: { ... }
  }
]
```

### 选择器说明

| 选择器 | 对应元素 | 说明 |
|--------|---------|------|
| `#live2d-container` | Live2D 虚拟伙伴 | 第一步：介绍虚拟伙伴 |
| `#chat-container` | 对话容器 | 第二步：介绍对话区域 |
| `#textInputBox` | 文本输入框 | 第三步：介绍输入方式 |
| `#button-group` | 按钮组 | 第四步：介绍快速操作 |

**⚠️ 重要**: 如果 HTML 结构改变，需要更新这些选择器！

### 样式定制

主要样式变量在 `tutorial-styles.css` 中:

```css
/* 主题色 */
--primary-color: #44b7fe;      /* 蓝色 */
--primary-dark: #3aa8f0;       /* 深蓝 */

/* 背景 */
--bg-dark: rgba(30, 30, 40, 0.95);
--overlay-dark: rgba(0, 0, 0, 0.6);

/* 文本 */
--text-primary: #44b7fe;       /* 标题 */
--text-secondary: rgba(255, 255, 255, 0.85);  /* 描述 */
```

---

## 使用指南

### 用户体验流程

#### 首次访问
```
1. 页面加载完成
   ↓
2. 延迟 1.5 秒（确保 DOM 完全加载）
   ↓
3. 检查 localStorage 中的 'neko_has_seen_tutorial'
   ↓
4. 如果不存在，自动启动引导
   ↓
5. 用户浏览 4 个步骤
   ↓
6. 完成或跳过后，写入 localStorage 标记
   ↓
7. 下次访问不再显示
```

#### 再次访问
```
1. 页面加载
   ↓
2. 检查 localStorage 标记
   ↓
3. 标记存在 → 跳过引导
   ↓
4. 正常使用应用
```

### 开发者操作

#### 重新启动引导（测试用）
```javascript
// 在浏览器控制台执行
window.tutorialManager.restartTutorial();
```

#### 重置引导状态
```javascript
// 在浏览器控制台执行
window.tutorialManager.resetTutorialState();
```

#### 检查引导状态
```javascript
// 在浏览器控制台执行
window.tutorialManager.hasSeenTutorial();  // 返回 true/false
```

#### 手动启动引导
```javascript
// 在浏览器控制台执行
window.tutorialManager.startTutorial();
```

#### 销毁引导实例
```javascript
// 在浏览器控制台执行
window.tutorialManager.destroy();
```

---

## 故障排查

### 问题 1: 引导不显示

**症状**: 首次访问时引导没有出现

**排查步骤**:
1. 检查浏览器控制台是否有错误信息
2. 验证 driver.js CDN 是否加载成功
   ```javascript
   console.log(typeof window.driver);  // 应该输出 'object'
   ```
3. 检查 localStorage 是否被禁用
   ```javascript
   console.log(localStorage.getItem('neko_has_seen_tutorial'));
   ```
4. 验证 tutorial-manager.js 是否加载
   ```javascript
   console.log(typeof window.tutorialManager);  // 应该输出 'object'
   ```

**解决方案**:
- 清除浏览器缓存和 localStorage
- 检查网络连接（CDN 加载）
- 查看浏览器控制台的详细错误信息

### 问题 2: 样式显示不正确

**症状**: 弹窗样式不是深色磨砂风格

**排查步骤**:
1. 检查 tutorial-styles.css 是否加载
   ```javascript
   // 在浏览器开发者工具中查看 Network 标签
   ```
2. 验证 CSS 是否被应用
   ```javascript
   const popover = document.querySelector('.driver-popover');
   console.log(window.getComputedStyle(popover).background);
   ```

**解决方案**:
- 硬刷新浏览器 (Ctrl+Shift+R)
- 检查 CSS 文件路径是否正确
- 查看浏览器开发者工具的 Elements 标签

### 问题 3: 翻译显示为 key 值

**症状**: 弹窗显示 `tutorial.step1.title` 而不是翻译文本

**排查步骤**:
1. 检查 i18n 是否初始化
   ```javascript
   console.log(typeof window.t);  // 应该输出 'function'
   ```
2. 验证翻译文件是否加载
   ```javascript
   window.t('tutorial.step1.title');  // 应该返回翻译文本
   ```

**解决方案**:
- 检查翻译文件 JSON 格式是否正确
- 验证翻译 key 是否存在
- 清除浏览器缓存重新加载

### 问题 4: 高亮元素不存在

**症状**: 控制台显示 "元素不存在" 警告

**排查步骤**:
1. 验证选择器是否正确
   ```javascript
   document.querySelector('#live2d-container');  // 应该返回元素
   ```
2. 检查元素是否在 DOM 中
   ```javascript
   document.querySelectorAll('#live2d-container').length;
   ```

**解决方案**:
- 更新 `tutorial-manager.js` 中的选择器
- 确保元素在页面加载时存在
- 检查 HTML 结构是否改变

---

## 后续维护

### 定期检查清单

- [ ] 每次更新 HTML 结构时，验证选择器是否仍然有效
- [ ] 添加新语言时，在所有翻译文件中添加教程翻译
- [ ] 定期测试各语言的翻译显示
- [ ] 监控用户反馈，优化引导文案
- [ ] 检查 driver.js 库的更新版本

### 扩展功能建议

#### 1. 添加更多步骤
```javascript
// 在 getSteps() 方法中添加新步骤
{
  element: '#new-feature',
  popover: {
    title: '新功能',
    description: '这是新功能的说明...',
    side: 'right',
    align: 'center'
  }
}
```

#### 2. 条件性显示
```javascript
// 根据用户权限或设置显示不同步骤
if (window.userRole === 'admin') {
  steps.push({
    element: '#admin-panel',
    popover: { ... }
  });
}
```

#### 3. 自定义完成回调
```javascript
// 在 onTutorialEnd() 中添加自定义逻辑
window.tutorialManager.driver.on('destroy', () => {
  // 发送分析事件
  trackEvent('tutorial_completed');
  // 显示特殊奖励
  showReward();
});
```

#### 4. 分步骤分析
```javascript
// 记录用户在哪一步放弃
this.driver.on('next', () => {
  trackEvent('tutorial_step_' + this.currentStep);
});
```

### 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-02-02 | 初始版本，基于 driver.js v1.3.1 |

---

## 快速参考

### 文件位置速查表

```
N.E.K.O/
├── templates/
│   └── index.html                    ← 修改：添加 CDN 和脚本
├── static/
│   ├── tutorial-manager.js           ← 新增：核心管理器
│   ├── app.js                        ← 修改：初始化代码
│   ├── css/
│   │   └── tutorial-styles.css       ← 新增：样式定制
│   └── locales/
│       ├── zh-CN.json                ← 修改：添加翻译
│       ├── zh-TW.json                ← 修改：添加翻译
│       ├── en.json                   ← 修改：添加翻译
│       └── ja.json                   ← 修改：添加翻译
└── TUTORIAL_SPEC.md                  ← 新增：本文档
```

### 关键代码片段

**初始化教程管理器**:
```javascript
if (typeof initTutorialManager === 'function') {
    initTutorialManager();
}
```

**检查引导状态**:
```javascript
const hasSeen = localStorage.getItem('neko_has_seen_tutorial');
```

**重启引导**:
```javascript
window.tutorialManager.restartTutorial();
```

---

## 联系与支持

如有问题或建议，请：
1. 查看本文档的故障排查部分
2. 检查浏览器控制台的错误信息
3. 查看 `tutorial-manager.js` 中的注释说明
4. 参考 driver.js 官方文档: https://driverjs.com/

---

**文档完成日期**: 2026-02-02
**最后更新**: 2026-02-02
**维护者**: N.E.K.O 开发团队

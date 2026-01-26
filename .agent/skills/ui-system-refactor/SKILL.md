---
name: 🎨 UI 设计规范
description: Project N.E.K.O. 胶囊化 UI、品牌蓝视觉系统规范
---

# 🎨 UI 设计规范

## 核心视觉标准

1. **胶囊化 (Capsule UI)**: 所有交互元素使用大圆角 (`border-radius: 50px`)
2. **品牌蓝 (Neko Blue)**: 主色 `#40C5F1`，描边 `#22b3ff`
3. **圆润描边**: 使用20层 `text-shadow` 矩阵实现标题描边效果

## 关键避坑

- **i18n 图标消失**: 翻译更新时检测 HTML 标签，使用 `innerHTML` 保留图标
- **Select 被裁剪**: 包含下拉框的容器必须设置 `overflow: visible`
- **按钮交互**: Hover `translateY(-1px)` + Active `translateY(1px) scale(0.98)`

## 参考文档

- 📘 [设计系统规范](references/design-system.md) - CSS 变量、颜色、组件
- 💎 [实战经验](references/valuable-experiences.md) - 描边算法、i18n 兼容

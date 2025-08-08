# Xiao8 Project

这是一个包含多个应用的 monorepo 项目，包括 Web 端和移动端应用。

## 项目结构

```
Xiao8/
├── apps/
│   ├── mobile/     # React Native 移动应用
│   └── web/        # Next.js Web 应用
├── static/         # 静态资源
├── templates/      # HTML 模板
└── ...            # 其他配置和资源
```

## 快速开始

### 移动应用 (React Native)

```bash
# 方法1: 使用根目录脚本
npm run mobile

# 方法2: 直接进入目录
cd apps/mobile
npx expo start
```

然后在手机上：
- 扫描二维码使用 Expo Go 应用
- 或按 `i` 启动 iOS 模拟器
- 或按 `a` 启动 Android 模拟器

### Web 应用 (Next.js)

```bash
npm run dev:web
```

## 移动应用功能

移动应用包含以下页面，都是从 `templates/*.html` 转换而来：

- 🏠 **主页面** - 聊天界面和导航
- 🎭 **Live2D 查看器** - 模型显示和控制
- 🧠 **记忆浏览器** - 记忆数据管理
- 📝 **字幕管理** - 字幕设置
- 🎤 **语音克隆** - TTS 和语音管理
- ⚙️ **API 设置** - 服务器和 API 配置
- 👤 **角色管理** - AI 角色管理
- 🎨 **Live2D 管理** - 模型管理
- 😊 **表情管理** - 表情和动作管理

## 技术栈

### 移动端
- React Native
- Expo Router
- TypeScript
- React Native WebView

### Web 端
- Next.js
- React
- TypeScript

## 开发说明

这个项目使用 monorepo 结构，通过 Turbo 进行构建管理。移动应用使用 Expo Router 进行路由管理，提供了流畅的页面切换体验。

所有页面都已经从原始的 HTML 模板成功转换为 React Native 组件，并针对移动设备进行了优化。

- 通过访问`
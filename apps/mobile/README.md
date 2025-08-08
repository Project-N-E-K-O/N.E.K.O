# Xiao8 Mobile App

这是 Xiao8 项目的 React Native 移动端应用，使用 Expo Router 构建。

## 功能特性

### 🏠 主页面 (index.tsx)
- 聊天界面，支持与AI助手对话
- 顶部导航栏，快速访问Live2D和记忆管理
- 底部工具栏，包含字幕、语音、设置等功能

### 🎭 Live2D 查看器 (viewer.tsx)
- Live2D模型显示和控制
- 表情切换功能
- 模型和表情管理入口

### 🧠 记忆浏览器 (memory-browser.tsx)
- 浏览和管理AI记忆数据
- 按类型筛选记忆
- 搜索和删除功能

### 📝 字幕管理 (subtitle.tsx)
- 字幕显示开关
- 字体大小、颜色、透明度设置
- 位置调整功能

### 🎤 语音克隆 (voice-clone.tsx)
- TTS语音合成设置
- 语音模型管理
- 语音克隆功能

### ⚙️ API设置 (api-key-settings.tsx)
- 服务器连接配置
- 多种AI API密钥管理
- 连接测试功能

### 👤 角色管理 (chara-manager.tsx)
- AI角色创建和编辑
- 角色性格设定
- 角色激活/切换

### 🎨 Live2D管理 (l2d-manager.tsx)
- Live2D模型导入/导出
- 模型预览和激活
- 模型信息管理

### 😊 表情管理 (live2d-emotion-manager.tsx)
- 表情和动作管理
- 表情/动作编辑
- 预览和测试功能

## 技术栈

- **React Native** - 跨平台移动应用框架
- **Expo Router** - 文件系统路由
- **TypeScript** - 类型安全
- **React Native WebView** - Live2D显示

## 开发环境

### 安装依赖
```bash
cd apps/mobile
npm install
```

### 启动开发服务器
```bash
npx expo start
```

### 运行在设备上
- 扫描二维码使用 Expo Go 应用
- 或按 `i` 启动 iOS 模拟器
- 或按 `a` 启动 Android 模拟器

## 项目结构

```
apps/mobile/
├── app/                    # Expo Router 页面
│   ├── _layout.tsx        # 根布局
│   ├── index.tsx          # 主页面
│   ├── viewer.tsx         # Live2D查看器
│   ├── memory-browser.tsx # 记忆浏览器
│   ├── subtitle.tsx       # 字幕管理
│   ├── voice-clone.tsx    # 语音克隆
│   ├── api-key-settings.tsx # API设置
│   ├── chara-manager.tsx  # 角色管理
│   ├── l2d-manager.tsx    # Live2D管理
│   └── live2d-emotion-manager.tsx # 表情管理
├── assets/                # 静态资源
├── package.json           # 项目配置
└── index.ts              # 应用入口
```

## 从HTML转换说明

这个React Native应用是从原始的HTML模板转换而来：

- `templates/index.html` → `app/index.tsx` (主聊天界面)
- `templates/viewer.html` → `app/viewer.tsx` (Live2D查看器)
- `templates/memory_browser.html` → `app/memory-browser.tsx` (记忆管理)
- `templates/subtitle.html` → `app/subtitle.tsx` (字幕设置)
- `templates/voice_clone.html` → `app/voice-clone.tsx` (语音克隆)
- `templates/api_key_settings.html` → `app/api-key-settings.tsx` (API设置)
- `templates/chara_manager.html` → `app/chara-manager.tsx` (角色管理)
- `templates/l2d_manager.html` → `app/l2d-manager.tsx` (Live2D管理)
- `templates/live2d_emotion_manager.html` → `app/live2d-emotion-manager.tsx` (表情管理)

## 主要改进

1. **移动端优化** - 适配触摸操作和移动设备屏幕
2. **导航体验** - 使用 Expo Router 提供流畅的页面切换
3. **组件化设计** - 将功能模块化为独立组件
4. **TypeScript支持** - 提供类型安全和更好的开发体验
5. **现代化UI** - 使用React Native原生组件，提供更好的性能

## 下一步开发

- [ ] 集成实际的AI API
- [ ] 实现Live2D模型加载
- [ ] 添加语音合成功能
- [ ] 实现数据持久化
- [ ] 添加用户认证
- [ ] 优化性能和用户体验

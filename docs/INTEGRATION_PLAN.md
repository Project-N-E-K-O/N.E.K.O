# VRM 动画库集成计划

## 📋 分支信息
- **分支名称**: `feature/integrate-vrm-animation-library`
- **创建时间**: 2024年
- **目标**: 集成 `@pixiv/three-vrm-animation` 官方库，替换手动实现的动画播放逻辑

## 🎯 集成目标

### 主要目标
1. 使用官方库处理 VRM 动画播放
2. 自动处理骨骼重定向和四元数问题
3. 保留所有现有自定义功能

### 需要保留的功能
- ✅ 口型同步 (`startLipSync`/`stopLipSync`)
- ✅ 调试模式 (`toggleDebug`)
- ✅ 播放速度控制 (`playbackSpeed`)
- ✅ 立即播放模式 (`immediate`)
- ✅ SpringBone 协调
- ✅ 淡入淡出效果

## 📝 集成步骤

### 阶段 1: 准备工作
- [ ] 下载 `@pixiv/three-vrm-animation` 库文件
- [ ] 添加到 `static/libs/` 目录
- [ ] 在 `index.html` 的 importmap 中配置

### 阶段 2: API 研究
- [ ] 阅读官方文档
- [ ] 测试基础 API（加载、播放、停止）
- [ ] 验证功能兼容性（速度、循环、淡入淡出）

### 阶段 3: 代码重构
- [ ] 重构 `vrm-animation.js` 使用官方库
- [ ] 保留口型同步功能
- [ ] 保留调试模式
- [ ] 适配播放速度控制
- [ ] 适配淡入淡出效果

### 阶段 4: 集成测试
- [ ] 测试基础播放功能
- [ ] 测试循环播放
- [ ] 测试播放速度控制
- [ ] 测试淡入淡出
- [ ] 测试立即播放模式
- [ ] 测试口型同步兼容性
- [ ] 测试 SpringBone 协调

### 阶段 5: 兼容性验证
- [ ] 验证与 `vrm-manager.js` 的兼容性
- [ ] 验证与 `vrm-core.js` 的兼容性
- [ ] 验证与 `app.js` 的兼容性
- [ ] 验证与 `audio-loader.js` 的兼容性

### 阶段 6: 性能优化
- [ ] 性能对比测试
- [ ] 内存使用检查
- [ ] 代码清理和优化

## 🔗 参考资源

- [@pixiv/three-vrm-animation GitHub](https://github.com/pixiv/three-vrm-animation)
- [Three.js AnimationMixer 文档](https://threejs.org/docs/#api/en/animation/AnimationMixer)
- [VRM 规范文档](https://vrm.dev/)

## 📊 当前实现统计

- **代码行数**: ~531 行
- **核心逻辑**: ~400 行
- **自定义功能**: 口型同步、调试模式、速度控制等

## ⚠️ 注意事项

1. 保持 API 接口不变，确保向后兼容
2. 所有自定义功能必须保留
3. 如果官方库不支持某些功能，需要额外封装
4. 充分测试后再合并到主分支

## 📝 变更记录

- 2024年: 创建集成分支和计划文档


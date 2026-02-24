# VRM 模型

## 概述

N.E.K.O. 支持 VRM（Virtual Reality Model）格式，使用 Three.js 和 `@pixiv/three-vrm` 进行 3D 角色渲染。

## 模型管理

- 通过 `/api/model/vrm/upload` 上传 VRM 文件（最大 200MB）
- 通过 `/api/model/vrm/animation/upload` 单独上传动画
- 通过 `/vrm_emotion_manager` 配置情感映射

## 灯光配置

VRM 模型使用可配置的灯光系统：

| 灯光 | 默认值 | 范围 | 描述 |
|------|--------|------|------|
| Ambient | 0.4 | 0 - 1.0 | HemisphereLight 强度 |
| Main | 1.2 | 0 - 2.5 | 主方向光 |
| Fill | 0.5 | 0 - 1.0 | 辅助补光 |
| Rim | 0.8 | 0 - 1.5 | 边缘/轮廓光 |
| Top | 0.3 | 0 - 1.0 | 顶部光 |
| Bottom | 0.15 | 0 - 0.5 | 底部光 |

通过 `PUT /api/characters/catgirl/{name}/lighting` 进行配置。

## UI 组件

| 模块 | 用途 |
|------|------|
| `vrm-ui-buttons.js` | VRM 专用控制按钮 |
| `vrm-ui-popup.js` | VRM 弹出对话框 |

## 已知问题与修复

### SpringBone 物理爆炸

VRM 的 `update(delta)` 期望 delta 以**秒**为单位。传入毫秒或未限制的值会导致头发向上飞起：

```javascript
let delta = clock.getDelta();
delta = Math.min(delta, 0.05); // Prevent physics explosion on tab switch
vrm.update(delta);
```

### 碰撞体过大（影响几乎所有 VRM 模型）

从 VRoid Studio 导出的 VRM 模型存在一个已知的 UniVRM bug（[#673](https://github.com/vrm-c/UniVRM/issues/673)），碰撞体半径约为正常值的 2 倍。这会导致头发呈水平固定状态。**修复方法**：加载后将所有碰撞体半径缩小 50%：

```javascript
springBoneManager.colliders.forEach(collider => {
    if (collider.shape?.radius > 0) {
        collider._originalRadius = collider.shape.radius;
        collider.shape.radius *= 0.5;
    }
});
```

### MToon 轮廓线粗细

当模型被缩放时，MToon 轮廓线会变得不成比例地粗。切换到屏幕空间模式：

```javascript
material.outlineWidthMode = 'screenCoordinates';
material.outlineWidthFactor = 0.005; // 1-2 pixel thin outline
material.needsUpdate = true;
```

| 系数 | 效果 |
|------|------|
| 0.002 - 0.003 | 极细（~1px） |
| 0.005 | 细（1-2px） |
| 0.01 | 中等（2-3px） |
| 0.02+ | 粗 |

### 摄像机拖拽不一致

永远不要使用固定的 `panSpeed` 进行拖拽。应动态计算像素到世界坐标的映射：

```javascript
const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
const pixelToWorld = worldHeight / screenHeight;
```

参见[开发者笔记](/contributing/developer-notes#vrm-model-gotchas)获取完整参考。

## API 端点

请参阅 [VRM API](/api/rest/vrm) 获取完整的 REST 端点参考。

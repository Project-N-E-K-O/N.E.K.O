# VRM 模型

## 运行时与格式

VRM 渲染器使用 Three.js 与 `@pixiv/three-vrm`。模型是 `.vrm` 文件；动画通常是通过 `@pixiv/three-vrm-animation` 加载的 `.vrma` 文件。

当前实现位于 `static/vrm/`：核心、管理器、初始化、动画、表情、交互、光标跟随、朝向与 UI 模块。只有 VRM 角色激活时，`vrm-init.js` 才会创建 `window.vrmManager` 并初始化 `#vrm-canvas`。

## 模型与动画

`GET /api/model/vrm/models` 会合并 `static/vrm/` 顶层的内置文件、通过 `/user_vrm` 提供的用户文件，以及 `/workshop/{item_id}/...` 下的已安装创意工坊文件。API 返回公共 URL，不会暴露绝对文件系统路径。

动画来自 `static/vrm/animation/` 与 `/user_vrm/animation/`。上传接口接受模型 `.vrm` 和动画 `.vrma`，每个文件上限 200 MB。用户模型删除仅允许操作配置 VRM 目录顶层的 `.vrm` 文件。

## 光照

`config/character_defaults.py` 中的后端默认值会在渲染器脚本之前以 `window.VRM_DEFAULT_LIGHTING` 注入模板。当前键值为：

```json
{
  "ambient": 0.83,
  "main": 1.91,
  "fill": 0.0,
  "rim": 0.0,
  "top": 0.0,
  "bottom": 0.0,
  "exposure": 1.1,
  "toneMapping": 7,
  "outlineWidthScale": 1.0
}
```

角色专用光照可以覆盖这些值。后端默认值、模板上下文与 `vrm-core.js` 中的防御性回退必须保持一致。

## 情绪映射

VRM 情绪把语义名称映射到有顺序的候选表情名称：

```json
{
  "neutral": ["neutral"],
  "happy": ["happy", "joy", "fun", "smile"],
  "surprised": ["surprised", "surprise", "shock", "e", "o"]
}
```

服务器把按模型的映射保存在 `static/vrm/configs/`。`vrm-expression.js` 将保存的映射合并到默认值之上，并对表情名称执行不区分大小写的精确匹配。管理页面在保存前可以通过 `/api/model/vrm/expressions/{model_name}` 获取模型实际表情。

VRM 激活时，`window.LanLan1.setEmotion(name)` 委派给 `window.vrmManager.expression.setMood(name)`。非 neutral 情绪会在运行时延时结束后回到 neutral。

## VMC 动作输出

`vrm-vmc-sender.js` 会在动画、视线、弹簧骨和表情更新后采样当前 VRM，并通过独立的 `/api/vmc/ws` 通道发送姿态。后端负责坐标转换，再通过 OSC/UDP 发送给兼容 VMC Protocol 的接收端。

该功能默认关闭。关闭状态只保留轻量代理，不进行状态轮询、VMC 定时任务或逐帧采样。在主页面使用默认本机目标启用：

```js
await window.vrmVmcSender.enable('127.0.0.1', 39539, 60)
```

VMC 根节点独立于 `vrm.scene`，因此桌宠布局变换不会被导出。切换或销毁模型时，旧表情会通过带确认的清零帧退出；禁用或释放数据源最终会发送 `/VMC/Ext/OK 0`。

接收端配置、控制接口、安全边界、关闭码与排障方法见 [VMC 动作输出](/zh-CN/api/rest/vmc)。

## 运行时保护

当前渲染器会在长时间停顿后钳制帧 delta、缩小导入的 spring-bone 碰撞体半径，并通过光照配置缩放 MToon 描边宽度。这些是内部兼容保护，不是模型格式要求；不要为了复现它们而预先修改上传的 VRM 文件。

碰撞体默认统一缩小到 50%（`VrmPhysicsProfile.DEFAULT_REDUCTION`）。这是经验值，用来抵消 UniVRM 导出时碰撞体半径没有随缩放归一的问题。某个模型需要不同的值时，用下面的每模型物理配置覆盖，不要改这个全局系数。

## 每模型物理配置

加载 VRM 时，`vrm-physics-profile.js` 会请求 `GET /api/model/vrm/physics_profile/{model_name}`，读取 `static/vrm/configs/{model_name}_physics.json`。读到可用配置就按配置写碰撞体半径和弹簧关节参数；没有文件、文件损坏、请求失败或超过 4 秒，都回到上面的 50% 缩减。仓库目前不附带任何 `_physics.json`，所以默认所有模型都走 50%。

`model_name` 取模型 URL 的文件名去掉 `.vrm`（先做 URL 解码，解码失败时保留原样），与情绪映射用的是同一个名字。请求带 `cache: 'no-store'`，改完配置重新加载模型即可生效。控制台会输出一行 `[VRM] Physics profile: source=file|default, colliders=N, joints=N, reset=true|false`，用来确认实际命中了多少。

配置可以由外部 Unity 工具 [vrm-physics-tuner](https://github.com/ylh440104/vrm-physics-tuner) 导出，本仓库不依赖它，也可以手写。

### 文件格式

顶层必须是 JSON 对象，`colliders` 和 `chains` 至少要有一个是数组，否则按没有配置处理。文件用 UTF-8 编码，允许 BOM（C# 默认会写）。`NaN` / `Infinity` 会在接口里换成 `null`，对应字段跳过。

```json
{
  "colliders": [
    { "bone": "J_Bip_C_Head", "shape": "sphere", "offset": [0, 0.05, 0], "radius": 0.06 },
    { "bone": "J_Bip_C_Chest", "shape": "capsule", "offset": [0, 0, 0], "tail": [0, 0.1, 0], "radius": 0.08 }
  ],
  "chains": [
    {
      "bones": ["J_Sec_Hair1_01", "J_Sec_Hair2_01"],
      "stiffness": 1.0,
      "drag_force": 0.4,
      "gravity_power": 0.0,
      "gravity_dir": [0, -1, 0],
      "hit_radius": 0.02
    }
  ]
}
```

| 字段 | 说明 |
| --- | --- |
| `colliders[].bone` | 碰撞体所在骨骼的 glTF 节点原名 |
| `colliders[].shape` | `sphere` 或 `capsule`，与 three-vrm 碰撞体的 `shape.type` 一致；省略时不检查形状 |
| `colliders[].offset` | 碰撞体在骨骼局部空间的偏移。省略或 `[0,0,0]` 表示没有位置信息（tuner 导出的恒为 `[0,0,0]`） |
| `colliders[].tail` | 胶囊尾端，只用来在同一骨骼的多个胶囊之间优先区分；tuner 写的是 Unity 坐标，对不上不算错 |
| `colliders[].radius` | 模型缩放为 1 时的半径，运行时再乘当前缩放；必须是有限非负数，否则这个碰撞体仍按 50% 处理 |
| `chains[].bones` | 这组参数作用到的骨骼原名列表 |
| `chains[].stiffness`、`gravity_power` | 非负数 |
| `chains[].drag_force` | 取值 [0, 1]（求解器按 `1 - dragForce` 衰减上一帧位移） |
| `chains[].hit_radius` | 非负数，缩放为 1 时的值，随模型缩放 |
| `chains[].gravity_dir` | 可选，三元数组 |

关节参数超出取值范围时只跳过这一项，同一条链的其他参数照常写入。

### 匹配规则

- **骨骼名**：先按 GLTFLoader 保留在 `userData.name` 里的 glTF 原名匹配；节点没有记录原名时才用清洗后的名字（GLTFLoader 会把空白换成下划线、删掉 `[ ] . : /`）。节点有原名但配置里查不到时不回退到清洗名，避免 `hair.001` 和 `hair001` 这类清洗后同名的骨骼互相借配置。
- **碰撞体**：只改模型已有碰撞体的半径，不增删碰撞体，也不改形状和位置。依次尝试：骨骼 + 形状 + offset + tail；骨骼 + 形状 + offset；同骨骼、同形状且配置没有位置信息时按出现顺序配对。形状不同、或 offset 明确对不上的保持未命中，走 50%。
- **关节**：按骨骼找到链。同一骨骼出现在多条链里时按出现顺序依次分配；运行时关节比配置的链多时，多出来的保留模型自己的参数。
- 写完后调用 `springBoneManager.reset()`，让弹簧骨从新参数重新开始。缩放模型时，碰撞体半径照旧随缩放；关节的 `hitRadius` 只有被配置写过的才随缩放，没有配置的模型与以前一样不变。

### 已知限制

- **按文件名查找。** 配置按模型文件名查找，与情绪映射一致。创意工坊里不同条目的同名 `model.vrm` 会共用一份配置。
- **按骨骼名匹配，不按骨骼路径。** 同一骨骼出现在多条参数不同的链里时，按出现顺序分配。
- **用 tuner 当前的导出，碰撞体基本不会生效。** 以仓库自带的 `sister1.0.vrm` 为例，tuner 导出 6 个胶囊碰撞体，而模型运行时是 28 个球体碰撞体，结果碰撞体 0/28 命中、关节 71/71 命中，实际只有弹簧关节参数生效。要让碰撞体也生效，需要 tuner 改成按模型现有碰撞体逐个导出半径。

### 做成用户可见功能之前要先解决

现在这套机制只面向开发者和调参者，还没有导入界面，也没有随包或通过创意工坊分发的配置。要做成用户可见功能，至少先解决下面三点：

1. **存放位置。** 配置读自 `project_root/static/vrm/configs`，打包版的 `project_root` 是安装目录，不是用户数据目录。用户自己放进去的文件不适合长期保留，要先换到用户可写的目录，并兼容旧位置。情绪映射也存在同一个目录，最好一起迁移。
2. **查找的名字。** 开始分发配置以后，按文件名查找会让工坊里的同名模型串用配置，需要换成能区分来源的名字。情绪映射用的是同一套规则，两者应一起改。
3. **格式版本。** 文件没有版本字段，代码只看 `colliders` / `chains` 在不在。tuner 改格式之前（例如改成逐个导出碰撞体半径、改变字段含义），要先加版本字段，并约定旧版本怎么处理。

## API 摘要

| 方法 | 端点 | 用途 |
| --- | --- | --- |
| `POST` | `/api/model/vrm/upload` | 上传一个 `.vrm` 模型 |
| `POST` | `/api/model/vrm/upload_animation` | 上传一个 `.vrma` 动画 |
| `GET` | `/api/model/vrm/models` | 列出内置、用户与创意工坊模型 |
| `GET` | `/api/model/vrm/animations` | 列出内置与用户动画 |
| `GET` | `/api/model/vrm/config` | 返回公共 VRM URL 前缀 |
| `GET`、`POST` | `/api/model/vrm/emotion_mapping/{model_name}` | 读取或保存表情映射 |
| `GET` | `/api/model/vrm/expressions/{model_name}` | 检查模型中的表情名称 |
| `GET` | `/api/model/vrm/physics_profile/{model_name}` | 读取每模型物理配置 |
| `DELETE` | `/api/model/vrm/model` | 按公共 URL 删除用户模型 |

## 宿主边界

VRM 在 `index.html` 中渲染，也包括 Electron 桌宠窗口。独立聊天与字幕模板有意不创建第二个 VRM 场景；原生窗口通过共享跨窗口桥接与主页面协调。

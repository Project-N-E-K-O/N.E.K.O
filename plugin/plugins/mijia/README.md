# 米家智能家居插件

米家（小米智能家居）插件，支持控制小米/米家智能设备。

## 功能特性

- 🔐 二维码登录，安全便捷
- 🏠 支持多家庭管理
- 📱 自动缓存设备列表和规格信息
- 🎙️ 智能控制：一句话控制设备（如"打开插座"）
- 📊 状态查询：快速查看设备当前状态
- ⚡ 支持属性控制和操作调用

## 安装

将本插件复制到 N.E.K.O 的插件目录：

```bash
cp -r mijia /path/to/N.E.K.O/plugin/plugins/
```

## 使用方法
### 0.所以操作除登录外均可直接对小天说指令，agent大语言会自动选择合适入口执行。
### 1. 登录

首次使用需要登录小米账号：

1. 访问插件配置页面：`http://localhost:48916/plugin/mijia/ui/`
2. 扫描二维码登录
3. 登录成功后即可使用

### 2. 获取设备列表

```python
# 获取并缓存设备列表
result = await list_devices()

# 从缓存获取（更快）
result = await get_cached_devices()
```

### 3. 智能控制

最简单的控制方式，只需一句话：

```python
# 打开插座
result = await smart_control(command="打开插座")
# 返回: ✅ 已打开'插座'

# 关闭灯
result = await smart_control(command="关闭灯")
# 返回: ✅ 已关闭'灯'
```

支持的关键词：打开/开启/开/关闭/关掉/关

### 4. 查询设备状态

```python
# 查询插座状态
result = await query_device_state(name="插座")
# 返回:
# 📱 设备 '插座' 当前状态：
#   • 开关: ✅ 开启
#   • 功率: 1250W
```

### 5. 精确控制

如需精确控制特定属性：

```python
# 1. 先查找设备获取 did、siid、piid
result = await find_device_by_name(name="插座")
device = result.value["devices"][0]
did = device["did"]

# 2. 从 properties 中找到开关属性
prop = device["properties"][0]  # 假设第一个是开关
siid = prop["siid"]
piid = prop["piid"]

# 3. 控制设备
result = await control_device(
    device_id=did,
    siid=siid,
    piid=piid,
    value=True  # 打开
)
```

### 6. 调用设备操作

```python
result = await call_device_action(
    device_id=did,
    siid=2,   # 服务ID
    aiid=1    # 操作ID
)
```

### 7. 获取设备规格

```python
result = await get_device_spec(model="your.device.model")
# 返回设备的所有属性和操作定义
```

### 8. 执行智能场景

```python
result = await execute_scene(scene_id="scene_id_here")
```

### 9. 登出

```python
result = await logout()
# 清除凭据和所有本地缓存数据
```

## 入口列表

| 入口ID | 功能 | 说明 |
|--------|------|------|
| `smart_control` | 智能控制设备 | 一句话控制，如"打开插座" |
| `query_device_state` | 查询设备状态 | 根据名称查询设备当前状态 |
| `find_device_by_name` | 模糊搜索设备 | 根据名称查找设备信息 |
| `get_cached_devices` | 获取缓存设备列表 | 从本地缓存读取 |
| `list_devices` | 获取并缓存设备列表 | 从服务器获取最新列表 |
| `list_homes` | 获取家庭列表 | 获取用户的所有家庭 |
| `control_device` | 精确控制设备属性 | 使用 did/siid/piid 控制 |
| `get_device_status` | 获取单个属性值 | 查询特定属性的值 |
| `call_device_action` | 调用设备操作 | 执行设备操作 |
| `execute_scene` | 执行智能场景 | 执行预设场景 |
| `get_device_spec` | 获取设备规格 | 获取属性和操作定义 |
| `logout` | 登出 | 清除凭据和数据 |

## 数据缓存

插件会自动缓存以下数据到 `data/` 目录：

- `credential.json` - 登录凭据（权限 600）
- `devices_cache.json` - 设备列表和规格信息

缓存的设备信息包含：
- 设备基本信息（did、name、model、is_online）
- 属性列表（siid、piid、name、type、access）
- 操作列表（siid、aiid、name）

## 返回值格式

所有入口都返回包含 `message` 字段的友好格式：

```python
# 成功
{
    "success": True,
    "message": "✅ 操作成功",
    ...
}

# 失败
Err(SdkError("错误信息"))
```

使用 emoji 图标增强可读性：
- ✅ 成功
- ❌ 失败
- 🟢 在线/开启
- 🔴 离线/关闭
- 📱 设备
- 📊 状态
- 📋 规格
- 🏠 家庭
- 🔍 搜索
- ▶ 操作

## 依赖

- Python 3.11+
- 见 `requirements.txt`

## 许可证

MIT License

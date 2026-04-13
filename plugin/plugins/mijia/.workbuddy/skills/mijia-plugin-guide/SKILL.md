# 米家插件使用指南

## 概述

这个 skill 指导 AI 如何正确使用 N.E.K.O 的米家插件（mijia）来控制米家智能设备。

## 核心概念

### 设备信息结构
每个设备包含以下关键字段：
- `did`: 设备ID（控制设备时作为 `device_id` 参数）
- `name`: 设备名称
- `model`: 设备型号
- `is_online`: 是否在线
- `properties`: 属性列表（包含 `siid`, `piid`, `name`, `type`, `access`）
- `actions`: 操作列表（包含 `siid`, `aiid`, `name`）

### 重要参数说明
- **siid** (Service ID): 服务ID，标识设备的功能模块
- **piid** (Property ID): 属性ID，标识具体属性
- **aiid** (Action ID): 操作ID，标识具体动作

## 使用流程

### 场景1：控制设备（如开关灯）

正确的调用链：

```
1. find_device_by_name(name="灯")
   ↓
   返回: {devices: [{did: "xxx", name: "客厅灯", model: "...", properties: [...], actions: [...]}]}
   
2. 从返回的设备信息中提取：
   - device_id = did (如 "xxx")
   - 从 properties 中找到开关属性（name 包含"开关"或"power"）
   - 获取该属性的 siid 和 piid
   
3. control_device(device_id="xxx", siid=2, piid=1, value=true)
```

### 场景2：获取设备状态

```
1. find_device_by_name(name="插座")
   ↓
   返回设备信息（包含 properties）
   
2. 从 properties 中找到需要查询的属性，获取 siid 和 piid
   
3. get_device_status(device_id=did, siid=2, piid=1)
```

### 场景3：调用设备动作

```
1. find_device_by_name(name="扫地机")
   ↓
   返回设备信息（包含 actions）
   
2. 从 actions 中找到需要执行的动作（如"开始清扫"），获取 siid 和 aiid
   
3. call_device_action(device_id=did, siid=2, aiid=1)
```

## 关键要点

### 1. 必须先获取设备信息
**不要**凭空猜测 `device_id`、`siid`、`piid` 的值。必须先调用 `find_device_by_name` 或 `get_cached_devices` 获取准确的设备信息。

### 2. did 就是 device_id
设备的 `did` 字段就是控制设备时需要的 `device_id` 参数。这是同一个值的不同名称。

### 3. 从缓存获取规格信息
现在 `list_devices` 和 `find_device_by_name` 返回的设备信息中已经包含了 `properties` 和 `actions`，不需要再调用 `get_device_spec`。

### 4. 如何找到正确的 siid/piid
在 `properties` 列表中查找：
- 开关类：找 `name` 包含"开关"、"电源"、"power"、"switch" 的属性
- 亮度类：找 `name` 包含"亮度"、"brightness" 的属性
- 温度类：找 `name` 包含"温度"、"temperature" 的属性

### 5. value 的类型
根据 `property` 的 `type` 字段确定：
- `bool`: 布尔值 `true` 或 `false`
- `int`/`uint`: 整数
- `float`: 浮点数
- `string`: 字符串

## 常见错误

### ❌ 错误：直接使用设备名称作为 device_id
```
control_device(device_id="客厅灯", ...)  // 错误！
```

### ✅ 正确：使用 did 作为 device_id
```
control_device(device_id="123456789", ...)  // 正确
```

### ❌ 错误：猜测 siid/piid
```
control_device(..., siid=1, piid=1)  // 可能错误！
```

### ✅ 正确：从设备信息中获取
```
// 先从 find_device_by_name 获取 properties
// 然后使用正确的 siid 和 piid
control_device(..., siid=2, piid=1)  // 正确
```

## 示例对话

用户："帮我打开客厅的灯"

AI 的思考过程：
1. 用户想控制设备（开灯）
2. 需要先找到"客厅灯"这个设备
3. 调用 `find_device_by_name(name="客厅灯")`
4. 从返回结果中获取 `did` 和 `properties`
5. 在 `properties` 中找到开关属性（`name` 包含"开关"）
6. 获取该属性的 `siid` 和 `piid`
7. 调用 `control_device(device_id=did, siid=siid, piid=piid, value=true)`

## 相关 Entries

- `find_device_by_name`: 根据名称查找设备，返回完整设备信息
- `get_cached_devices`: 获取所有缓存的设备
- `control_device`: 控制设备属性（开关、亮度等）
- `call_device_action`: 调用设备动作（开始清扫等）
- `get_device_status`: 获取设备属性值
- `list_devices`: 刷新并获取设备列表

# 米家智能设备插件 - 使用文档

## 📖 概述
米家智能设备插件是一个基于 N.E.K.O 插件系统开发的插件，用于连接和控制米家（Mi Home）智能设备。通过该插件，你可以使用自然语言控制米家设备，实现智能家居的语音控制。

## ✨ 功能特性
*   **🔐 支持多种登录方式**：账号密码 / 二维码扫码
*   **🏠 自动发现米家设备**
*   **📊 获取设备状态和属性**
*   **🎮 控制设备开关、调节参数**
*   **🔄 设备信息缓存，提高响应速度**
*   **💾 认证数据持久化存储**
*   **🚀 异步并发设备发现**

## 📋 前置要求
*   N.E.K.O 框架（版本 >= 0.1.0）
*   米家账号

## 🚀 快速开始

### 1. 安装插件
将插件放置在 N.E.K.O 的插件目录下：

```N.E.K.O/plugin/plugins/mijia/```

### 2. 配置账号
**方式一：通过 AI 对话配置**

请配置米家插件，账号是 ，密码是 

**方式二：通过 HTTP API 配置**
```bash
 curl -X POST http://localhost:48916/plugin/trigger \
   -H "Content-Type: application/json" \
   -d '{
     "plugin_id": "mijia",
     "entry_id": "update_config",
     "args": {
       "username": "你的账号",
       "password": "你的密码",
       "enableQR": false,
       "auto_connect": true
     }
   }'
```
### 3. 连接米家
* 请连接米家
### 4. 发现设备
* 请发现我的米家设备
### 5. 控制设备
* 请打开客厅的灯
* 请设置空调温度为26度
* 请关闭空气净化器
* 请查询卧室灯的状态
## 📚 插件入口点（Entry Points）

### 配置管理
| 入口ID | 名称 | 描述 | 输入参数 |
| :--- | :--- | :--- | :--- |
| `update_config` | 更新配置 | 更新插件配置 | `username`, `password`, `enableQR`, `auto_connect` |
| `get_status` | 获取状态 | 获取插件运行状态 | 无 |

### 连接管理
| 入口ID | 名称 | 描述 | 输入参数 |
| :--- | :--- | :--- | :--- |
| `connect` | 连接米家 | 连接到米家云服务 | 无 |
| `disconnect` | 断开连接 | 断开米家云服务连接 | 无 |

### 设备管理
| 入口ID | 名称 | 描述 | 输入参数 |
| :--- | :--- | :--- | :--- |
| `discover_devices` | 发现设备 | 发现米家设备 | `online_only`, `force_refresh` |
| `get_devices` | 获取设备列表 | 获取已发现的设备列表 | 无 |
| `get_device_by_name` | 按名称查找 | 根据名称查找设备 | `name` |
| `get_device_by_did` | 按ID查找 | 根据设备ID查找设备 | `did` |
| `search_devices` | 搜索设备 | 搜索和过滤设备 | `query`, `online_only` |

### 设备控制
| 入口ID | 名称 | 描述 | 输入参数 |
| :--- | :--- | :--- | :--- |
| `get_device_properties` | 获取设备属性 | 获取设备支持的属性列表 | `device_id` |
| `get_property_value` | 获取属性值 | 获取设备属性的当前值 | `device_id`, `siid`, `piid` |
| `set_property_value` | 设置属性值 | 设置设备属性的值 | `device_id`, `siid`, `piid`, `value` |
| `call_action` | 调用动作 | 调用设备动作 | `device_id`, `siid`, `aiid`, `params` |

## 🔧 配置说明

### 配置文件位置

```N.E.K.O/plugin/plugins/mijia/data/config.json```

### 配置项说明

```json
{
  "username": "米家账号（手机号/邮箱）",
  "password": "米家密码",
  "enableQR": true,      // true: 二维码登录, false: 账号密码登录
  "auto_connect": false, // true: 插件启动时自动连接
  "log_level": "INFO"    // 日志级别
}
```
## 📝 API 使用示例

### 1. 更新配置

```python
通过跨插件调用
result = await self.plugins.call_entry(
    "mijia:update_config",
    {
        "username": "13800138000",
        "password": "your_password"
    }
)
```
### 2. 连接米家

```python
result = await self.plugins.call_entry("mijia:connect")
if isinstance(result, Ok):
    print("连接成功")
else:
    print(f"连接失败: {result.error}")
```
### 3. 发现设备

```python
result = await self.plugins.call_entry(
    "mijia:discover_devices",
    {
        "online_only": True,      # 只返回在线设备
        "force_refresh": True     # 强制刷新，不使用缓存
    }
)

if isinstance(result, Ok):
    devices = result.value["devices"]
    for device in devices:
        print(f"设备: {device['name']} (ID: {device['did']})")
```
### 4. 控制设备

```python
设置设备属性（例如：打开开关）
result = await self.plugins.call_entry(
    "mijia:set_property_value",
    {
        "device_id": "设备ID",
        "siid": 2,      # 服务实例ID
        "piid": 1,      # 属性实例ID
        "value": True   # 设置的值
    }
)

调用设备动作
result = await self.plugins.call_entry(
    "mijia:call_action",
    {
        "device_id": "设备ID",
        "siid": 2,      # 服务实例ID
        "aiid": 1,      # 动作实例ID
        "params": []    # 动作参数
    }
)
```
### 5. 获取设备状态
```python
获取设备状态
result = await self.plugins.call_entry("mijia:get_status")
if isinstance(result, Ok):
    status = result.value
    print(f"连接状态: {status['connected']}")
    print(f"设备数量: {status['device_count']}")
```
## 🔄 工作流程
1.  配置账号
2.  连接米家
3.  发现设备
4.  获取设备详细
5.  控制设备
6.  查询状态

## 🛠️ 开发调试

### 日志查看
插件日志输出到 N.E.K.O 的主日志中，包含以下关键信息：
*   连接状态变化
*   设备发现过程
*   设备控制结果
*   错误和异常信息

## ❓ 常见问题

### 1. 连接失败
*   检查账号密码是否正确
*   检查网络连接
*   尝试使用二维码登录

### 2. 设备发现失败
*   确保已成功连接
*   检查设备是否在线
*   尝试使用 `force_refresh: true`

### 3. 设备控制失败
*   确认设备支持该属性/动作
*   检查参数格式是否正确
*   查看设备是否在线

## 📁 文件结构

>mijia/
>├── __init__.py              # 插件主入口
>├── adapter/
>│   └── mijia_adapter.py     # 米家适配器
>├── config/
>│   └── mijia_config.py      # 配置管理
>├── utils/
>│   ├── auth_manager.py      # 认证数据管理
>│   └── logger.py            # 日志工具
>├── data/                    # 运行时数据目录
>│   ├── config.json          # 插件配置
>│   ├── devices.json         # 设备缓存
>│   └── auth_data.json       # 认证数据
>└── plugin.toml              # 插件描述文件

## 📄 许可证
本插件遵循 N.E.K.O 框架的许可证协议。

## 🤝 贡献
欢迎提交 Issue 和 Pull Request！

## 📮 联系方式
如有问题，请在 N.E.K.O 项目中提交 Issue。
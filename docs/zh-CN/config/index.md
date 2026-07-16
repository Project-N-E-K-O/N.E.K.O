# 配置概览

N.E.K.O. 有三套不同的配置入口，它们不共享一条万能优先级链。

| 配置面 | 推荐方式 | 运行时来源 |
| --- | --- | --- |
| 用户设置、角色、API Key | Web UI | 选定数据根目录下的 JSON |
| Provider 定义与模型角色默认值 | 维护者修改仓库数据 | `config/api_providers.json` 与代码回退 |
| 端口和部分运行时开关 | 环境变量或桌面端端口设置 | `config/network.py`、`config/memory_settings.py`、`port_config.json` |

普通安装请先启动 N.E.K.O.，再用 Web UI 配置。不要为了修改个人 API Key 而改仓库内置文件。

- [配置文件](./config-files)
- [API Provider](./api-providers)
- [模型配置](./model-config)
- [环境变量](./environment-vars)
- [配置优先级](./config-priority)

::: warning 密钥
不要把 API Key 提交到 Git，也不要放进截图、Issue 或日志。
:::

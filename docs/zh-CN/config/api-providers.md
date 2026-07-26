# API Provider

Provider 是数据驱动的。当前发行定义在 `config/api_providers.json`，Python 回退在 `config/api_profiles.py`；Web UI 通过加载器获取显示信息。

不要依赖文档里的 Provider 数量或模型 ID 快照，它们都会随 revision 变化。

- **Core** 驱动主对话/实时路径，字段为 `core_url(s)`、`core_model`、`core_api_key`。
- **Assist** 支撑会话、摘要、纠错、情感、视觉、Agent 等角色，可包含 URL、token-plan URL、角色模型、Key 占位和 `provider_type`。

`utils/api_config_loader.py` 读取并缓存 JSON、转换运行时字段；assist JSON 覆盖同名代码默认值；损坏/缺失时使用代码回退；`assist_api_key_fields` 映射凭据字段。

同一 JSON 还包含 keybook、API-key registry、原生 TTS/免费语音、默认模型元数据、直播和审核设置；这些有各自消费者，不是通用 Provider 字段。

修改时保持 key 稳定、不得写真实密钥；可见文字同步 8 个 locale，并运行测试与文档构建。字段契约见 [API Provider 字段参考](/api_providers_fields)。面向用户的免费路径、自带 Key 与本地组件区别见[费用与 Provider](/zh-CN/guide/cost-and-providers)。

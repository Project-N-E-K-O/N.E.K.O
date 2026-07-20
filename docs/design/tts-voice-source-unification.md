# TTS provider 与声音来源

> **文档性质：current implementation record + proposal boundary。** Provider dispatch registry 已实现；完整的 source-first 选声 UI 和所有 voice-design 来源统一仍是 proposal，不能写成已上线。

## 当前实现

`utils/tts/provider_registry.py` 定义 `DispatchContext`、`TTSProvider`、`register()` 和 `resolve_selected()`。`main_logic/tts_client/__init__.py` 的 `get_tts_worker()` 装载 provider 并生成对应 worker；具体 worker 位于 `main_logic/tts_client/workers/`。

原生/平台声音还有 `utils/tts/native_voice_registry.py` 及 `utils/tts/providers/`。配置的持久化和 legacy 迁移位于 `utils/config_manager/voice_storage.py` 与相关 config manager 模块。

## 当前合同

- provider id、voice id 和“声音来源”是不同维度，不能仅凭 voice id 猜 provider；
- dispatch 使用结构化 `DispatchContext`，并由 provider 自己判断是否匹配；
- provider 注册、解析和 worker 构造保持对偶；新增 provider 不能只改 UI；
- 旧配置读取后应迁移到规范化 key，同时保留安全 fallback；
- API key、region、base URL 和模型字段属于 provider 配置，不写进 voice id；
- 未知/不可用 provider 返回明确错误或回退，不得静默路由到错误声音。

## Proposal：source-first UI

未来选声器可以先选来源类别，再选 provider/voice，并为 cloned、designed、native、preset 等来源展示不同配置。但在 UI schema、迁移和所有 provider 的 capability 测试完成前，这只是产品提案。

Voice design 也必须先定义生成资产的所有权、删除/迁移、隐私和 provider 限制，不能直接塞进现有静态 voice 列表。

## 验证

```bash
uv run pytest tests/unit/test_native_voice_registry.py tests/unit/test_voice_config.py tests/unit/test_voice_lazy_migration.py tests/unit/test_tts_module_compatibility.py -q
```

新增 provider 时还要运行该 provider 的专用声音与 worker 测试。

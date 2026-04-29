# B站弹幕插件（集成背景LLM）部署指南

> ⚠️ **v2.0 重构**：本文档已重写，旧版（四象限/熔断器/summary/ 目录）已于 2026-04-24 移除。

## 系统概述

背景LLM系统位于 B站弹幕 和 主AI 之间，负责：

1. **时间窗口聚合** — 按可配置时间窗口（5~180s）缓冲弹幕，超量自动随机采样
2. **LLM 引导词生成** — 调用 DeepSeek/OpenAI 兼容 API，根据弹幕总结生成 AI 发言引导
3. **降级统计摘要** — LLM 失败/超时自动降级为词频统计摘要
4. **用户画像追踪** — 记录活跃观众发言习惯，注入 LLM Prompt 辅助个性化引导

## 当前文件结构

```
bilibili_danmaku/
├── __init__.py                # 插件主入口（NekoPluginBase 子类）
├── aggregator.py              # 时间窗口聚合器
├── llm_client.py              # LLM API 调用客户端（DeepSeek/OpenAI 兼容）
├── orchestrator.py            # 引导词编排器（LLM → 降级摘要）
├── user_profile.py            # 用户画像追踪器
├── danmaku_core.py            # B站 WebSocket 弹幕监听器
├── bili_auth_service.py       # B站认证服务
├── bili_content_service.py    # B站内容服务
├── plugin.toml                # 插件注册元数据
├── data/
│   ├── config.json            # 运行时配置（自动读写）
│   ├── config_enhanced.json   # 背景LLM 配置文件模板
│   ├── user_profiles/         # 观众画像持久化目录
└── static/
    └── index.html             # 插件控制台前端
```

## 调用链路

```
B站 WebSocket → danmaku_core.py
    ├─→ _process_danmaku_event()
    │     ├─ _danmaku_queue (传统推送队列)
    │     └─ _aggregator.add() → [时间窗口缓冲]
    │           └─→ flush() → _on_batch_ready()
    │                 └─ _orchestrator.generate()
    │                       ├─ LLMClient.generate_guidance() → DeepSeek API
    │                       └─ 失败降级 → _degrade_summary() → 词频统计
    │                 └─ _push_guidance_to_ai() → 主AI
    └─→ push_danmaku_tick() → 定时推送/强制刷新
```

## 部署步骤

### 1. 启用背景LLM

编辑 `data/config_enhanced.json`：

```json
{
  "background_llm": {
    "enabled": true,
    "cloud": {
      "url": "https://api.deepseek.com",
      "api_key": "sk-your-key-here",
      "model": "deepseek-chat",
      "timeout_sec": 10,
      "retry_times": 2
    },
    "window_size": 15,
    "max_samples": 30,
    "knowledge_context": "主播是一只猫娘...",
    "user_profile": ""
  }
}
```

- `cloud.url`：LLM API 基地址（不含 chat/completions 后缀，代码自动拼接 `/v1/chat/completions`）
- `cloud.model`：`deepseek-chat`（普通模式）或 `deepseek-reasoner`（思考模式，需处理 reasoning_content 回传）
- `cloud.timeout_sec` / `cloud.retry_times`：API 超时秒数和重试次数（可选，默认 10s / 2次，注意放在 `cloud` 下级）
- `knowledge_context`：注入 Prompt 的专属知识库（人设、世界观、常见梗）
- `user_profile`：用户画像配置（当前实际由代码直接初始化 db_path，该字段仅占位保留，配置不影响运行）

### 2. 启动插件

NEKO 系统启动后，插件自动加载。`plugin.toml` 中 `auto_start = true`。

检查日志中 `_init_background_llm` 的输出确认 LLM 系统状态。

## API接口

### 背景LLM API（新增）

| 接口 | 方法 | 说明 |
|------|------|------|
| `get_guidance_config` | GET | 获取背景LLM配置与统计（聚合器/编排器/LLM客户端） |
| `update_guidance_config` | POST | 更新背景LLM配置 |
| `test_guidance` | POST | 用测试弹幕列表验证引导词生成效果 |

### 兼容API

`set_room_id`, `set_interval`, `send_danmaku`, `get_danmaku`, `get_status`,
`save_credential`, `clear_credential`, `reload_credential`, `connect`, `disconnect`

## 故障排除

| 症状 | 排查方向 |
|------|----------|
| 背景LLM未启用 | 检查 `config_enhanced.json` 中 `background_llm.enabled` = true；确认 LLM 模块导入无 ImportError |
| LLM 返回空/超时 | 检查 `cloud.api_key`、`cloud.url`；超时调整 `cloud.timeout_sec`；查看日志 `[LLMClient]` 错误 |
| 配置不生效 | 确认 `cloud` 字段在 `config_enhanced.json` 的 `background_llm` 下级，而非平铺 |
| `_background_llm_enabled=False` | 查看 `_init_background_llm` 日志，确认它真的被调用 |

---

**文档版本**: 2.0（重构后）  
**最后更新**: 2026-04-25  
**系统架构**: 四件套（TimeWindowAggregator + GuidanceOrchestrator + LLMClient + UserProfileTracker）

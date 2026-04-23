# 虚拟主播背景LLM系统部署指南

## 系统概述

虚拟主播背景LLM系统是一个智能中间层，位于B站弹幕和主AI（猫娘）之间，负责：

1. **实时过滤与分级**：四象限智能分级（紧急/重要维度）
2. **用户画像积累**：跨会话用户价值数据持久化
3. **智能摘要生成**：云端LLM+本地规则双模摘要
4. **熔断降级**：云端API故障时自动降级本地规则

## 文件结构

```
bilibili_danmaku/
├── __init__.py                    # 原始插件主文件（已备份）
├── __init__enhanced.py           # 增强版插件（集成背景LLM）
├── user_profile.py               # 用户画像存储模块
├── filter_pipeline.py            # 四象限过滤流水线
├── aggregator.py                 # 聚合缓冲器
├── filter.py                     # 原始过滤器（兼容保留）
├── summary/                      # 摘要生成模块
│   ├── __init__.py
│   ├── local_engine.py          # 本地规则引擎
│   ├── cloud_client.py          # 云端API客户端（占位符）
│   ├── circuit_breaker.py       # 熔断器
│   └── orchestrator.py          # 摘要编排器
├── data/
│   ├── config.json              # 原始配置
│   ├── config_enhanced.json     # 增强版配置（含背景LLM）
│   └── user_profiles/           # 用户画像存储目录（自动创建）
├── test_simple.py               # 核心功能测试脚本
└── DEPLOYMENT_GUIDE.md          # 本部署指南
```

## 部署步骤

### 步骤1：备份原始插件

```bash
# 备份原始插件文件
cp __init__.py __init__.py.backup
cp data/config.json data/config.json.backup
```

### 步骤2：启用增强版插件

有两种方式启用背景LLM系统：

#### 方式A：直接替换（推荐用于新部署）
```bash
# 替换主文件
cp __init__enhanced.py __init__.py

# 使用增强版配置
cp data/config_enhanced.json data/config.json
```

#### 方式B：渐进式启用（保持兼容性）
1. 保持 `__init__.py` 不变
2. 在 `data/config.json` 中添加背景LLM配置节
3. 手动导入背景LLM模块到现有插件

### 步骤3：配置背景LLM系统

编辑 `data/config.json`，添加以下配置节：

```json
{
  "background_llm": {
    "enabled": true,
    "cloud": {
      "enabled": false,  // 初始禁用云端API
      "url": "https://api.company.internal/v1/summary",
      "api_key": "${ENV:LLM_API_KEY}"
    },
    "local": {
      "enabled": true   // 启用本地规则引擎
    },
    "aggregation": {
      "max_wait_sec": 30,
      "max_events": 20,
      "min_events": 5
    }
  }
}
```

### 步骤4：环境变量配置（可选）

如果需要云端API，设置环境变量：

```bash
# Windows (PowerShell)
$env:LLM_API_KEY="your-api-key-here"

# Linux/macOS
export LLM_API_KEY="your-api-key-here"
```

### 步骤5：测试系统

运行核心功能测试：

```bash
python test_simple.py
```

预期输出：
```
>>> 所有核心功能测试通过！
```

### 步骤6：启动插件

通过NEKO系统正常启动插件：

```bash
# 在NEKO系统中启用插件
# 插件将自动加载背景LLM系统
```

## 配置详解

### 背景LLM配置选项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `background_llm.enabled` | boolean | `false` | 是否启用背景LLM系统 |
| `background_llm.mode` | string | `"hybrid"` | 模式：`hybrid`(混合), `cloud_only`, `local_only` |
| `background_llm.cloud.enabled` | boolean | `false` | 是否启用云端LLM |
| `background_llm.cloud.url` | string | `""` | 云端API地址 |
| `background_llm.cloud.timeout_sec` | number | `10` | API调用超时时间 |
| `background_llm.local.enabled` | boolean | `true` | 是否启用本地规则引擎 |
| `background_llm.aggregation.max_wait_sec` | number | `30` | 最长等待时间 |
| `background_llm.aggregation.max_events` | number | `20` | 最大事件数 |
| `background_llm.aggregation.min_events` | number | `5` | 最小事件数 |

### 四象限过滤配置

| 象限 | 特征 | 默认动作 | 配置键 |
|------|------|----------|--------|
| I类 | 紧急+重要 | 立即推送 | `filter.quadrant_config.I` |
| II类 | 不紧急+重要 | 进入聚合池 | `filter.quadrant_config.II` |
| III类 | 紧急+不重要 | 丢弃 | `filter.quadrant_config.III` |
| IV类 | 不紧急+不重要 | 丢弃 | `filter.quadrant_config.IV` |

## API接口

### 新增API（背景LLM系统）

| 接口名 | 方法 | 参数 | 返回 | 说明 |
|--------|------|------|------|------|
| `get_summary_config` | GET | 无 | 配置信息 | 获取背景LLM配置 |
| `update_summary_config` | POST | `config: dict` | 更新结果 | 更新背景LLM配置 |
| `get_user_profile` | GET | `uid: int` | 用户画像 | 查询单个用户画像 |
| `clear_user_profiles` | POST | `confirm: bool` | 清理结果 | 清空画像数据 |
| `test_summary_local` | GET | `events_count: int` | 测试结果 | 测试本地摘要效果 |

### 兼容API（原有功能保持不变）

所有原有API保持完全兼容：
- `set_room_id`
- `set_interval`
- `send_danmaku`
- `get_danmaku`
- `get_status`
- `save_credential`
- `clear_credential`
- `connect` / `disconnect`

## 监控与调试

### 日志级别

系统支持以下日志级别：
- `DEBUG`: 详细调试信息
- `INFO`: 正常操作信息（默认）
- `WARNING`: 警告信息
- `ERROR`: 错误信息

### 状态检查

通过 `get_status` API 可以获取系统状态：

```json
{
  "background_llm": {
    "enabled": true,
    "user_profiles": 150,
    "aggregation_buffer": 8,
    "circuit_breaker_state": "CLOSED"
  }
}
```

### 性能指标

系统自动记录以下指标：
- 弹幕处理延迟
- 摘要生成耗时
- 用户画像数量
- 队列大小
- API错误次数

## 故障排除

### 常见问题

#### Q1: 背景LLM系统未启用
**症状**: `get_status` 返回 `"background_llm.enabled": false`
**解决**: 检查 `data/config.json` 中的 `background_llm.enabled` 配置

#### Q2: 导入模块失败
**症状**: 启动时出现 `ImportError`
**解决**: 确保所有模块文件存在，修复相对导入路径

#### Q3: 云端API连接失败
**症状**: 熔断器状态为 `OPEN`
**解决**: 
1. 检查网络连接
2. 验证API密钥
3. 检查 `cloud.url` 配置
4. 等待熔断器自动恢复（默认300秒）

#### Q4: 用户画像文件过大
**症状**: 启动慢，内存占用高
**解决**:
1. 调整 `user_profile.cache_size`（默认10000）
2. 启用分片存储
3. 定期清理不活跃用户

### 调试模式

启用调试日志：

```json
{
  "background_llm": {
    "monitoring": {
      "log_level": "DEBUG"
    }
  }
}
```

## 升级与维护

### 数据迁移

用户画像数据自动从 `data/user_profiles.json` 迁移到 `data/user_profiles/` 目录。

### 版本兼容性

- **v1.0.x**: 原始弹幕插件
- **v1.1.0**: 增强版（集成背景LLM），完全向后兼容

### 备份策略

建议定期备份：
1. 用户画像数据：`data/user_profiles/`
2. 配置文件：`data/config.json`
3. 插件代码：整个 `bilibili_danmaku/` 目录

## 性能优化建议

### 内存优化
1. 调整 `user_profile.cache_size` 控制内存使用
2. 启用LRU缓存自动清理
3. 分片存储大用户数据集

### 性能优化
1. 调整 `aggregation.max_wait_sec` 平衡实时性和摘要质量
2. 使用异步处理避免阻塞
3. 批量操作减少IO次数

### 可靠性优化
1. 启用熔断器防止级联故障
2. 本地规则引擎作为可靠兜底
3. 自动重试和错误恢复

## 扩展开发

### 添加新的摘要模板

1. 在 `data/summary_templates/` 创建模板文件
2. 模板支持变量替换：`{period}`, `{highlights}`, `{topics}`, `{suggestion}`
3. 在配置中指定模板路径

### 集成真实云端API

1. 实现 `cloud_client.py` 中的真实API调用
2. 配置认证和错误处理
3. 更新 `orchestrator.py` 中的调用逻辑

### 添加新的过滤规则

1. 在 `filter_pipeline.py` 中添加新的过滤函数
2. 更新四象限分类逻辑
3. 通过配置控制规则启用/禁用

## 联系支持

如有问题，请：
1. 检查日志文件获取详细错误信息
2. 参考本部署指南
3. 联系开发团队提供技术支持

---

**文档版本**: 1.0  
**最后更新**: 2026-04-23  
**系统版本**: 背景LLM系统 v1.0.0
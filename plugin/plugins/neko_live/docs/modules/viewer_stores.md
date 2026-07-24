# viewer_stores

## Purpose

说明观众档案、审计和凭据的边界。三者用途不同，不能混用。

## Viewer Store

`stores/viewer_store.py` 保存基础档案与安全派生偏好，支持首次出场判断、重置印象、删除档案和惰性清理。

允许保存：平台前缀 UID、受限昵称/头像 metadata、首次/最近互动时间、安全派生偏好和必要计数。

不保存：原始弹幕、完整支持流水、cookie/token、头像 bytes/base64、跨平台无命名空间 UID。

关闭个性化记忆不应破坏本场防复读或首次出场判断。写入采用原子替换和容量/保留期策略；路径失败时安全降级并暴露可写状态。

## Audit Store

`stores/audit_store.py` 记录模块、route、status、reason 和脱敏摘要，用于解释与复盘。audit 不是日志数据库，也不能成为保存 raw payload 的后门。

未知 reason 和异常文本在投影前归一化；用户身份使用不可逆短关联 ID。

## Credential Store

`stores/credential_store.py` 按 provider namespace 加密保存登录凭据。公开状态只返回登录状态和必要的脱敏账号信息。

凭据绝不进入 config、logger、audit、Dashboard、viewer profile 或事件 raw。注销删除本机凭据；失败时不回显秘密内容。

## Testing

覆盖原子写、并发、路径冲突、容量与保留期、重置/删除、跨平台 UID、敏感字段负例、加密保存、注销和 Dashboard 安全投影。

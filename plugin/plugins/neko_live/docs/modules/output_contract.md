# output_contract

## Purpose

定义直播回复在真正派发前的内容形状：短、自然、知道在回应谁、适合 TTS，并避免模板主持、舞台指令、伪礼物致谢和换词复读。

## Ownership

- request metadata / prompt contract：`core/live_reply_contract.py`、`core/live_output_contract_prompt.py`；
- 回复策略与质量整形：`core/live_reply_policy.py`、`core/live_output_policy.py`、`core/live_output_quality.py`、`core/live_output_shape.py`；
- 最终派发：`adapters/neko_dispatcher.py`。

模块只提供安全请求内容，不在各自 handler 里复制最终输出规则。

## Rules

- 普通弹幕接话不重复描述头像；
- 看不到头像时只使用昵称或安全 META；
- 支持事件致谢不索要更多礼物；
- 主持输出不说“有人吗”“大家发弹幕”，不输出导演式舞台指令；
- 回复过长、悬空选项、泛化问句或近期复读应被整形、替换或跳过；
- 图片超出 message-plane 预算时降级纯文字；
- dry-run 与跳过必须返回可解释结果。

## Testing

为每种 route 提供正常例、长度边界、复读、伪礼物、舞台指令、泛化主持、看不到头像和 Dispatcher 降级负例。输出质量告警属于复盘信号，不应绕过 pipeline 自动重试造成重复发言。

## Rollback

质量规则异常时回滚到上一组已验证规则，保留唯一 Dispatcher、短句上限和敏感内容边界。不得用关闭所有安全整形作为临时修复。

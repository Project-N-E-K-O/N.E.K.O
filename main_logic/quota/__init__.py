"""quota — N.E.K.O.Servers 配额掉落本地引擎。

提供两个 hook 给 ``main_logic.agent_event_bus`` 挂载：
- ``on_text_message(lanlan_name, text) -> None``
  挂到 ``register_text_user_message_hook``，按 word_count + keywords 规则触发
  本地 UX 动画事件 + 异步调云端 ``/api/quotas/drop-hint``。**必须返回 None**
  避免抢占 first-hit-wins 链上的 mini-game-invite 等已有消费者。
- ``on_utterance(bucket, event) -> None``
  挂到 ``register_user_utterance_sink``。M2-j v1 暂时仅做日志记录，
  v2 接入情感强度判定时再启用 emotion 规则。

启用：必须同时满足 ``NEKO_QUOTA_DROPPER_ENABLED=1`` 且 ``NEKO_SOCIAL_BASE_URL`` 已配
（否则 hooks 是 noop）。默认禁用，避免推 commit 后悄悄向云端推数据。
"""

from main_logic.quota.dropper import on_text_message, on_utterance

__all__ = ["on_text_message", "on_utterance"]

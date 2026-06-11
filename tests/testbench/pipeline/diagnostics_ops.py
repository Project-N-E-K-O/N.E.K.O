"""Centralized enum of all ``diagnostics_store.record_internal`` op types.

Background
----------
Before this module, ``record_internal(op="integrity_check", ...)`` /
``record_internal(op="judge_extra_context_override", ...)`` used bare
string literals scattered across routers. Typos don't error; F7
Security subpage had to hardcode a duplicate list of "known ops" to
filter against, guaranteed to drift.

This module provides:

* :class:`DiagnosticsOp` — StrEnum of all known op strings. Callers do
  ``record_internal(DiagnosticsOp.INTEGRITY_CHECK.value, ...)`` or
  ``record_internal(DiagnosticsOp.INTEGRITY_CHECK, ...)`` (StrEnum
  auto-coerces to ``str`` for equality and JSON serialization).
* :data:`OP_CATALOG` — metadata dict ``{op_value: {category, severity,
  description}}`` consumed by ``GET /api/diagnostics/ops`` so the
  F7 Security subpage renders without hardcoding.
* :func:`all_ops_payload` — serialized catalog for the router.

Contract
--------
* ``record_internal`` signature UNCHANGED (still accepts plain ``str``).
  Migrating a call site is just swapping ``"integrity_check"`` →
  ``DiagnosticsOp.INTEGRITY_CHECK``; no behavior diff.
* Adding a new op: add one enum member + one OP_CATALOG entry.
  ``.cursor/rules/diagnostics-ops-sync.mdc`` (future) will grep new
  ``record_internal(op="..."`` calls and fail if the op is not in
  the enum.

See ``P24_BLUEPRINT §4.1.5`` for the full rationale.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any


class DiagnosticsOp(StrEnum):
    """All known ``record_internal`` op strings.

    New ops must be added here AND to :data:`OP_CATALOG` in the same PR.
    """

    # P22.1 — archive memory hash mismatch detected on load / restore.
    # Severity: warning (doesn't block load, but user should investigate).
    INTEGRITY_CHECK = "integrity_check"

    # P22.1 — judge_run body included extra_context that overrode one of
    # the built-in keys (persona_system / dimensions_block / etc.). The
    # override took effect, but it's an unusual enough pattern that we
    # log it for forensic replay. Severity: warning.
    JUDGE_EXTRA_CONTEXT_OVERRIDE = "judge_extra_context_override"

    # ── P24 new ops (landing across Day 2-5) ──────────────────────────

    # P24 §12.5 — safe_append_message coerced a message timestamp to
    # preserve monotonicity (user rewound virtual clock into the past).
    # Severity: warning. Downstream code is safe (ts is now monotonic),
    # but the UI should surface the coercion so the tester understands
    # "my new message got tagged at the old time, not what I set."
    TIMESTAMP_COERCED = "timestamp_coerced"

    # P24 §4.3 I — server bound to a non-loopback host (0.0.0.0 etc).
    # The startup banner already WARNs on stderr, but recording it in
    # the ring buffer lets F7 Security show it to anyone who opens
    # Diagnostics. Severity: warning.
    INSECURE_HOST_BINDING = "insecure_host_binding"

    # P24 §15.2 A — boot_self_check found orphan sandbox directories
    # and reported them (did NOT auto-delete, per §3A F3). Severity: info.
    ORPHAN_SANDBOX_DETECTED = "orphan_sandbox_detected"

    # P24 Day 8 §13 F3 scope extension — prompt_injection_detect matched
    # on a user-editable field (Chat send / persona edit / memory field).
    # Per LESSONS "detect-don't-mutate", we never block or rewrite; this
    # op just writes a warning to the Diagnostics ring so F7 Security can
    # aggregate. Severity: warning.
    PROMPT_INJECTION_SUSPECTED = "prompt_injection_suspected"

    # P24 Day 8 验收 — Auto-Dialog pipeline runtime error (SimUser LlmFailed
    # / RateLimitError 429 / API 5xx / network timeout / 其他防御兜底).
    # 之前只走 SSE 到前端 banner 显示, 不入 diagnostics ring buffer; 导致
    # 顶栏 Err 徽章 + Diagnostics → Errors 页都看不见这一类错误 — 跑夜里
    # batch 时完全失察. 改走 record_internal 统一兜住三处 except 分支.
    # Severity: error (触发徽章计数 +1, 区别于 injection 的 advisory warn).
    AUTO_DIALOG_ERROR = "auto_dialog_error"

    # P24 Day 10 §14.4 M4 — diagnostics ring buffer hit its 200-entry cap
    # and older entries are being dropped. Emitted *inside* the store
    # itself (not via record_internal to avoid re-entrance) the first
    # time overflow happens per fill cycle. Resets when the ring is
    # cleared or drops below the cap. Purpose: a testbench running for
    # 24h can silently burn through 200 entries (each 429 retry + each
    # injection hit logs one), and the UI currently has no signal that
    # older entries got evicted — the user just sees "200 events" and
    # assumes that's everything. This op makes the eviction visible.
    # Severity: warning.
    DIAGNOSTICS_RING_FULL = "diagnostics_ring_full"

    # ── P25 new ops (Day 1 外部事件仿真面板落盘) ─────────────────────────

    # P25 §2.1 / §3 Day 1 — tester 从前端 Chat 工作区 "外部事件模拟" 面板
    # 触发的 avatar interaction 仿真请求已被后端处理. 一次调用一条审计,
    # 不管语义层是否真的落盘 (dedupe 命中窗口会把事件静默丢掉, 但本 op
    # 仍然记录, dedupe_hit=True, 方便事后复核去重是否符合预期). detail
    # 字典承载 interaction_id / tool_id / action_id / intensity /
    # reward_drop / easter_egg / dedupe_hit / mirror_to_recent 等字段.
    # 与主程序 avatar interaction 语义契约对齐 (见 §2.1). 真正的缓存
    # "满了" 另走 AVATAR_DEDUPE_CACHE_FULL, 不要混用.
    # Severity: info (事件密度审计, 不触发 Err 徽章).
    AVATAR_INTERACTION_SIMULATED = "avatar_interaction_simulated"

    # P25 §2.1 / §3 Day 1 — tester 从前端 "外部事件模拟" 面板触发的
    # agent callback 仿真. 复现主程序 AGENT_CALLBACK_NOTIFICATION 的
    # 5 语言 instruction 拼接 + LLM 回复抓取 + 回复写入 session.messages
    # 流程. detail 字典承载 callback_count / total_chars /
    # instruction_lang / reply_len / mirror_to_recent 等字段. **与
    # avatar 不同**: agent_callback 不做 dedupe (多条 callback 合并成
    # 一个 instruction 一次性塞给 LLM, 语义层只跑一次), 所以 detail
    # 里没有 dedupe_hit 字段.
    # Severity: info.
    AGENT_CALLBACK_SIMULATED = "agent_callback_simulated"

    # P25 §2.1 / §3 Day 1 — tester 从前端 "外部事件模拟" 面板触发的
    # 主动搭话仿真. 复现主程序 get_proactive_chat_prompt(kind, lang)
    # 的 LLM 调用 + LLM 可能返回 [PASS] 跳过 (合法, pass_signaled=True)
    # + 非 [PASS] 回复走 append_message 写入 session.messages. detail
    # 字典承载 kind (home/screenshot/window/news/video/personal/music)
    # + lang + pass_signaled + reply_len + mirror_to_recent 等字段.
    # 与 agent_callback 一样不做 dedupe.
    # Severity: info.
    PROACTIVE_SIMULATED = "proactive_simulated"

    # P25 §3 Day 1 + §4.5 L27 四问 3 (通知频率) 回应 — avatar 事件去重
    # 缓存软上限 100 条, 达到上限后按 LRU 丢最旧, 并在本 fill cycle 内
    # 发一次本通知 (once-per-fill). 锁会自动重新武装的两条路径:
    # (a) caller 手动 POST /api/session/external-event/dedupe-reset 清空,
    # (b) 8 s 过期清扫让缓存回落到 < 100 条 (高峰静默后自然发生).
    # 与 diagnostics_store ring-full 的 warn-once 语义对齐.
    # 常见触发: tester 在短时间内高频连点 avatar 道具
    # 按钮 (L25 + LESSONS_LEARNED §4.27 #91 "避免 DoS 测试面板" 同类
    # 风险). 自查方式: 查 GET /api/session/external-event/dedupe-info
    # 的 cache 条目数, 配合本通知 detail 字典里的 max_entries 字段.
    # Severity: warning.
    AVATAR_DEDUPE_CACHE_FULL = "avatar_dedupe_cache_full"

    # P25 Day 2 polish r3 (L36 §7.25 第四次同族证据 / 同族更广根因) —
    # tester 在 composer 里选 Role=System 并点 [发送]. 主程序
    # (main_logic/omni_offline_client.py) 的运行期契约是 SystemMessage
    # 只出现在 _conversation_history[0] (初始化阶段), 所有运行期输入路径
    # 统一以 HumanMessage(role=user) 注入; 因此 session.messages 里追加
    # 的 role=system 消息与主程序语义**不等价**. wire 层 chokepoint
    # (prompt_builder.build_prompt_bundle) 会把这条消息重写为 role=user
    # + `[system note] ` 前缀, 避免 Vertex AI Gemini 对 "wire 中间/末尾
    # 有 system" shape 过敏 (400 INVALID_ARGUMENT 或 200 空 reply 导致
    # stale reply 时序错位). 本 op 把契约偏离点审计进 ring buffer, 方便
    # tester 在 Diagnostics → Errors/Logs 回看 "我发的 system 实际被 LLM
    # 当 user 消费了".
    # Severity: info.
    CHAT_SEND_SYSTEM_REWRITTEN = "chat_send_system_rewritten"

    # P25 Day 2 polish r5 — tester 在 composer textarea 里留空或只输了
    # 空白然后点 [发送]. 不走 pipeline 的"只跑 LLM 对末尾 user 重发"
    # 路径 (那条路径只有当 session.messages 末尾已经是 user 时才合法)
    # 而是直接忽略本次点击, 只 toast warn 提示 tester "消息不能为空".
    # 之前 (r4 及以前) 是让后端走 user_content=None → pipeline 抛
    # ValueError → 前端 SSE error frame → UI error 徽章 + Errors 页也
    # 会出现. 用户反馈 "这种情况不应当算 error, 只需要报警提示用户不
    # 得输入空消息即可, 但是依然需要在日志里面记录下来" — 所以降级到
    # info + 仅 ring 审计留痕, 不写 session JSONL 避免成为"假信号".
    # Severity: info.
    CHAT_SEND_EMPTY_IGNORED = "chat_send_empty_ignored"


#: Metadata consumed by ``GET /api/diagnostics/ops``. Must contain one
#: entry per :class:`DiagnosticsOp` member. Categories help F7 Security
#: subpage group-render events.
OP_CATALOG: dict[str, dict[str, str]] = {
    DiagnosticsOp.INTEGRITY_CHECK.value: {
        "category": "data_integrity",
        "severity": "warning",
        "description": (
            "存档载入 / 恢复时 memory 完整性校验未通过: 存档仍然载入, 但 "
            "memory tar.gz 的内容哈希与保存时记录的不一致, 可能是手动编辑 "
            "过或静默损坏. 存档数据未必可靠, 建议先核对 memory 内容再继续."
        ),
    },
    DiagnosticsOp.JUDGE_EXTRA_CONTEXT_OVERRIDE.value: {
        "category": "security",
        "severity": "warning",
        "description": (
            "调用 /judge/run 时 extra_context 覆盖了一个或多个内置键 "
            "(persona_system / dimensions_block / anchors_block 等). "
            "覆盖已生效; 本条仅为审计留痕, 便于事后复盘谁改了评委上下文."
        ),
    },
    DiagnosticsOp.TIMESTAMP_COERCED.value: {
        "category": "data_integrity",
        "severity": "warning",
        "description": (
            "虚拟时钟被设到过去后发送消息, 系统自动把新消息时间戳前移 "
            "到上一条消息时间, 保证消息列表时间单调不倒序 (下游时间分隔条 "
            "/ 导出 dialog_template 等都依赖这个单调性). 消息内容未改, "
            "只是时间字段被调整. 若想让消息时间真正往后, 请先把虚拟时钟 "
            "推到一个更晚的时刻再发送."
        ),
    },
    DiagnosticsOp.INSECURE_HOST_BINDING.value: {
        "category": "security",
        "severity": "warning",
        "description": (
            "服务器绑定到非 loopback 主机 (例如 0.0.0.0). testbench 没有 "
            "任何鉴权层, 同一局域网内任何人都能访问 Diagnostics / 聊天记录 "
            "/ 导出存档. 若只是本机测试用, 请改回 127.0.0.1; 若确实要暴露到 "
            "局域网, 务必确认网络是受信环境."
        ),
    },
    DiagnosticsOp.ORPHAN_SANDBOX_DETECTED.value: {
        "category": "maintenance",
        "severity": "info",
        "description": (
            "启动自检发现一个或多个没有对应活跃会话的沙盒目录 (通常是上次 "
            "进程被强杀 / 断电留下的). 系统**没有自动删除**它们, 请到 "
            "Diagnostics → Paths 子页核对后决定清理还是保留 (里面可能有 "
            "崩溃前的排查素材)."
        ),
    },
    DiagnosticsOp.PROMPT_INJECTION_SUSPECTED.value: {
        "category": "security",
        "severity": "warning",
        "description": (
            "在 Chat 发送 / persona 编辑 / memory 字段里检测到疑似 prompt "
            "injection 模式 (ChatML / Llama tokens / 越狱短语 / 角色冒充串 "
            "等). 系统**没有改写 / 拒绝**原内容 (testbench 允许输入对抗性 "
            "payload 作为测试素材, 参见 §3A G1 '检测不改'原则); 本条只是"
            "审计留痕让 F7 Security 子页能聚合统计. 若真的是无意输入且"
            "希望 LLM 正常响应, 建议修掉敏感 token 再发送."
        ),
    },
    DiagnosticsOp.AUTO_DIALOG_ERROR.value: {
        "category": "runtime",
        "severity": "error",
        "description": (
            "Auto-Dialog 自动对话跑批过程中遇到 runtime error 提前终止: "
            "常见类型有 LlmFailed (SimUser / target LLM 被上游限流 / 拒绝, "
            "例如 RateLimitError 429 / InternalServerError 5xx) / 网络超时 "
            "/ 配置校验未通过的防御兜底 (理论上应被 start 前预检拦住, 若"
            "跑到这里说明预检漏了). 已完成的轮次已经正常落盘, 可从本条信息"
            "的 detail 看 completed_turns / total_turns; 若是临时性上游故障"
            "(429 / 502), 隔一会儿重启 Auto-Dialog 即可续跑."
        ),
    },
    DiagnosticsOp.DIAGNOSTICS_RING_FULL.value: {
        "category": "maintenance",
        "severity": "warning",
        "description": (
            "Diagnostics 错误环形缓冲已达 200 条上限, 正在开始丢弃最老的 "
            "条目. 本事件本身只在 fill cycle 首次溢出时发一次, 被清空或 "
            "条目数量回落到阈值以下会自动重置. 请注意: 之后新增的每条错误 "
            "都会顶掉一条最老的错误, 如果需要保留本次会话的完整错误历史, "
            "建议立即导出 session / 到 Diagnostics → Errors 子页 Clear "
            "一下以重置 fill cycle."
        ),
    },
    DiagnosticsOp.AVATAR_INTERACTION_SIMULATED.value: {
        "category": "external_event",
        "severity": "info",
        "description": (
            "tester 从前端 Chat 工作区 '外部事件模拟' 面板触发了一次 "
            "avatar interaction 仿真 (敲 / 戳 / 摸等道具动作), 后端已经"
            "处理完毕. 不管语义层是否真的把事件塞进 session.messages, 也"
            "不管是否因 dedupe 窗口命中被丢弃, 每次调用都会记录一条本 op "
            "的审计信息, 方便事后在 Diagnostics → Errors 页回看 '我今天 "
            "按了几次 hammer 道具 / 有没有被 dedupe 吃掉' 这种事件密度. "
            "detail 字典包含 interaction_id + tool_id + action_id + "
            "intensity + reward_drop + easter_egg + dedupe_hit(True/False)"
            " + mirror_to_recent(True/False) 等关键字段. 注意: 真正因"
            "dedupe 被丢弃的事件本条 op 仍然会记 (dedupe_hit=True); 缓存"
            "本身达到软上限另走 avatar_dedupe_cache_full."
        ),
    },
    DiagnosticsOp.AGENT_CALLBACK_SIMULATED.value: {
        "category": "external_event",
        "severity": "info",
        "description": (
            "tester 从前端 Chat 工作区 '外部事件模拟' 面板触发了一次 "
            "agent callback 仿真. 本 op 对应的流程复现了主程序 "
            "AGENT_CALLBACK_NOTIFICATION 的完整链路: 5 语言 instruction "
            "拼接 → 发给 LLM → 抓取回复 → 回复通过 append_message 写入 "
            "session.messages. detail 字典承载 callback_count + "
            "total_chars + instruction_lang + reply_len + mirror_to_recent "
            "等字段. 与 avatar interaction 不同: agent callback 不做 "
            "dedupe (多条 callback 会被合并成一个 instruction 一次性塞 "
            "给 LLM, 语义层只跑一次), 因此 detail 里也没有 dedupe_hit "
            "字段."
        ),
    },
    DiagnosticsOp.PROACTIVE_SIMULATED.value: {
        "category": "external_event",
        "severity": "info",
        "description": (
            "tester 从前端 Chat 工作区 '外部事件模拟' 面板触发了一次主动 "
            "搭话仿真. 本 op 对应的流程复现了主程序 "
            "get_proactive_chat_prompt(kind, lang) 的 LLM 调用: LLM 可能 "
            "合法地返回 [PASS] 跳过 (这种情况下 pass_signaled=True, 不 "
            "写入 session.messages), 非 [PASS] 回复则走 append_message "
            "写入. detail 字典承载 kind (home / screenshot / window / "
            "news / video / personal / music) + lang + pass_signaled + "
            "reply_len + mirror_to_recent 等字段. 与 agent callback 一样, "
            "proactive 不做 dedupe."
        ),
    },
    DiagnosticsOp.AVATAR_DEDUPE_CACHE_FULL.value: {
        "category": "external_event",
        "severity": "warning",
        "description": (
            "avatar 事件去重缓存达到了 100 条的软上限, 已按 LRU 策略丢弃 "
            "最旧条目. 本通知在同一个 fill cycle 内只发一次 (once-per-"
            "fill); 当 (a) tester 手动调用 POST "
            "/api/session/external-event/dedupe-reset 清空缓存, **或** "
            "(b) 8 s 过期清扫让缓存回落到 < 100 条 (高峰过去后 8 秒静默 "
            "就会发生) 时, 锁会自动重新武装, 下次再填满又会发一条新的 "
            "notice. 与 diagnostics_store ring-full 的 warn-once 语义对齐. "
            "常见触发: tester 在短时间内高频连点 avatar 道具按钮 (参见 "
            "L25 + LESSONS_LEARNED §4.27 #91 '避免 DoS 测试面板' 同类风险). "
            "自查方式: 查 GET /api/session/external-event/dedupe-info 的 "
            "cache 条目数, 并配合本通知 detail 字典里的 max_entries 字段 "
            "核对上限值."
        ),
    },
    DiagnosticsOp.CHAT_SEND_SYSTEM_REWRITTEN.value: {
        "category": "data_integrity",
        "severity": "info",
        "description": (
            "tester 在 composer 里选 Role=System 并点 [发送]. 主程序语义 "
            "里 SystemMessage 只出现在初始化阶段 (position 0), 运行期所 "
            "有输入路径 (send_text_message / create_response / "
            "prompt_ephemeral) 都以 HumanMessage 注入; 所以 session. "
            "messages 里追加的 role=system 消息与主程序 SystemMessage "
            "**并不等价**, 它只是一条带 system 角色标签的上下文消息. "
            "wire 层 chokepoint (prompt_builder.build_prompt_bundle) 会 "
            "把这条消息重写为 role=user + `[system note] ` 前缀, 避免 "
            "Vertex AI Gemini 对 'wire 中间/末尾有 system' shape 过敏 "
            "(400 INVALID_ARGUMENT '空输入' 或 200 空 reply 导致 stale "
            "reply 时序错位). 本 op 是契约偏离审计点, 不是错误 — 如果 "
            "你期望的是主程序初始化 system prompt 行为, 请改用 Setup → "
            "Persona 编辑; 如果你就是想让 LLM 看到一条带 system 标签的 "
            "提示信息, 这条 op 告诉你它已经被转写成什么了."
        ),
    },
    DiagnosticsOp.CHAT_SEND_EMPTY_IGNORED.value: {
        "category": "runtime",
        "severity": "info",
        "description": (
            "tester 在 composer 空输入框 (或只打了空白字符) 状态下点了 "
            "[发送]. 已被直接忽略, 没有调 LLM, 也没有向 session.messages "
            "追加任何消息. 前端会弹一条 toast warning 提示 '消息不能为空'. "
            "注意: 空 textarea + session.messages 末尾是 user 的场景走 "
            "另一条合法路径 (stream_send(user_content=None) '重发最后一条 "
            "user 的 LLM 回复'), 该场景不会记本 op. 本 op 的 detail 字典 "
            "包含 role + source + session_id + tail_role (末尾消息的 role, "
            "一般是非 user 时才走这里) + tail_empty(True/False 表示 session "
            "是否为空会话)."
        ),
    },
}


def all_ops_payload() -> list[dict[str, Any]]:
    """Flat list serialization for the ``GET /api/diagnostics/ops`` endpoint.

    Returns a list so the UI can render in definition order (which
    roughly mirrors the phase order of when each op was introduced).
    """
    return [
        {"op": op_value, **metadata}
        for op_value, metadata in OP_CATALOG.items()
    ]


# Sanity check: enum and catalog are kept in sync. Raises at import
# time if someone adds an enum member but forgets the catalog entry.
_enum_values = {member.value for member in DiagnosticsOp}
_catalog_keys = set(OP_CATALOG.keys())
if _enum_values != _catalog_keys:
    raise RuntimeError(
        f"DiagnosticsOp / OP_CATALOG mismatch — "
        f"enum_only={_enum_values - _catalog_keys}, "
        f"catalog_only={_catalog_keys - _enum_values}"
    )


__all__ = ["DiagnosticsOp", "OP_CATALOG", "all_ops_payload"]

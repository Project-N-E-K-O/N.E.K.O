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

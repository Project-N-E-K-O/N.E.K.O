"""维护候选 Session 中的有限公开回合历史与时间戳。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Any


# 模型只消费最近四轮对话，公开恢复依赖快照，不保存无限增长的聊天流水。
MAX_RECENT_TURN_MESSAGES = 8


def _compose_graph_progress_dialogue(
    *,
    author_dialogue: str,
    generated_dialogue: str,
    response_focus: dict[str, Any],
) -> str:
    """有独立回应焦点时保留一次补充对白，并确保作者正文逐字且只出现一次。"""  # noqa: DOCSTRING_CJK
    author_text = str(author_dialogue or "").strip()
    if not response_focus:
        return author_text
    generated_text = str(generated_dialogue or "").strip()
    if not generated_text:
        return author_text
    # Actor 可能仍按旧 Prompt 返回完整作者正文；先移除所有逐字副本，再由服务端统一追加一次。
    supplemental_text = generated_text.replace(author_text, "").strip()
    if not supplemental_text:
        return author_text
    if not author_text:
        # Loader 会拒绝缺作者对白的正式节点；这里只保护被坏对象绕过时的可诊断行为。
        return supplemental_text
    return f"{supplemental_text}\n{author_text}"


def _append_turns(
    session: dict[str, Any],
    *,
    message: str,
    performance: dict[str, str],
    trace: dict[str, Any],
) -> None:
    """保存最小公开历史，供恢复和下一轮演绎使用。"""  # noqa: DOCSTRING_CJK
    turns = session.setdefault("turns", [])
    now = _now_ms()
    turns.append({"role": "user", "text": message, "created_at": now})
    turns.append(
        {
            "role": "assistant",
            "text": performance.get("dialogue", ""),
            "narration": performance.get("narration", ""),
            "scenario_trace": dict(trace),
            "created_at": now,
        }
    )
    # 只保存模型真正会读取的最近四轮，公开恢复依赖 snapshot，不依赖完整聊天流水。
    session["turns"] = turns[-MAX_RECENT_TURN_MESSAGES:]

def _now_ms() -> int:
    """使用毫秒时间戳保存 Session 生命周期。"""  # noqa: DOCSTRING_CJK
    import time

    return int(time.time() * 1000)

from __future__ import annotations

from hashlib import sha1
from time import time
from typing import Any


def normalize_instruction(
    *,
    source: str,
    kind: str,
    scope: str,
    content: str,
    priority: int = 0,
    mode: str = "advisory",
    payload: dict[str, Any] | None = None,
    origin: dict[str, Any] | None = None,
    applies_to: dict[str, Any] | None = None,
    lifetime: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    instruction_id: str | None = None,
) -> dict[str, Any]:
    normalized_content = str(content or "").strip()
    normalized_payload = dict(payload) if isinstance(payload, dict) else {}
    normalized_origin = dict(origin) if isinstance(origin, dict) else {}
    normalized_applies_to = dict(applies_to) if isinstance(applies_to, dict) else {}
    normalized_lifetime = dict(lifetime) if isinstance(lifetime, dict) else {}
    normalized_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
    if instruction_id is None:
        payload = f"{source}|{kind}|{scope}|{normalized_content}|{normalized_origin.get('step')}|{normalized_origin.get('timestamp')}"
        instruction_id = sha1(payload.encode("utf-8")).hexdigest()[:12]
    return {
        "schema_version": 1,
        "id": instruction_id,
        "source": str(source or "unknown"),
        "kind": str(kind or "directive"),
        "scope": str(scope or "step"),
        "priority": int(priority),
        "mode": str(mode or "advisory"),
        "content": normalized_content,
        "payload": normalized_payload,
        "origin": normalized_origin,
        "applies_to": normalized_applies_to,
        "lifetime": normalized_lifetime,
        "tags": normalized_tags,
    }


def normalize_guidance_instruction(content: str, *, source: str = "neko", step: int | None = None, guidance_type: str = "soft_guidance") -> dict[str, Any]:
    now = time()
    return normalize_instruction(
        source="neko_guidance",
        kind="directive",
        scope="step",
        content=content,
        priority=1,
        mode="advisory",
        payload={"guidance_type": guidance_type, "source": source},
        origin={"step": step, "timestamp": now, "source": source},
        lifetime={"consume_on_use": True},
        tags=[guidance_type],
    )


def normalize_strategy_instruction(strategy_name: str, strategy_constraints: dict[str, Any]) -> dict[str, Any]:
    must = strategy_constraints.get("must") if isinstance(strategy_constraints, dict) else []
    prefer = strategy_constraints.get("prefer") if isinstance(strategy_constraints, dict) else []
    summary_parts: list[str] = []
    if isinstance(must, list) and must:
        summary_parts.append("must=" + ", ".join(str(item) for item in must[:3]))
    if isinstance(prefer, list) and prefer:
        summary_parts.append("prefer=" + ", ".join(str(item) for item in prefer[:3]))
    content = f"strategy={strategy_name}" if not summary_parts else f"strategy={strategy_name}; " + "; ".join(summary_parts)
    return normalize_instruction(
        source="strategy_constraints",
        kind="constraint",
        scope="strategy",
        content=content,
        priority=0,
        mode="informational",
        payload={"strategy_name": strategy_name, "constraints": dict(strategy_constraints) if isinstance(strategy_constraints, dict) else {}},
        origin={"strategy": strategy_name},
        applies_to={"character_strategy": strategy_name},
        lifetime={"consume_on_use": False},
        tags=[strategy_name],
    )


def instruction_summary(instructions: list[dict[str, Any]]) -> str:
    lines = [str(item.get("content") or "").strip() for item in instructions if isinstance(item, dict) and str(item.get("content") or "").strip()]
    return "\n".join(f"- {line}" for line in lines)


__all__ = [
    "instruction_summary",
    "normalize_guidance_instruction",
    "normalize_instruction",
    "normalize_strategy_instruction",
]

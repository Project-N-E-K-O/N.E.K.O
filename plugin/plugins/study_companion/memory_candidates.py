from __future__ import annotations

import hashlib
import json
from typing import Any


def upsert_memory_candidate(store: Any, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return store.upsert_candidate_item(
        item_type="memory_draft",
        payload=payload,
        source="memory_llm_fallback",
        dedupe_key=f"{kind}:{digest}",
        status="candidate",
    )


__all__ = ["upsert_memory_candidate"]

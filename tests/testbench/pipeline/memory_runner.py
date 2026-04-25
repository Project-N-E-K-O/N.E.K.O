"""Memory op runner — dry-run / commit / discard for 4 memory operations (P10).

Scope
-----
P10 turns the four "long-tail" LLM memory operations into explicit,
tester-driven actions with a **preview → confirm → commit** flow:

============================== ==========================================
op                             what it does
============================== ==========================================
``recent.compress``            Summarize the **head** (oldest slice) of recent.json into one
                               SystemMessage memo (saves context budget).
``facts.extract``              Ask the LLM to pull atomic facts out of a
                               conversation window, dedupe, append to
                               facts.json.
``reflect``                    Ask the LLM to synthesize a higher-level
                               reflection from unabsorbed facts; mark the
                               source facts as absorbed.
``persona.add_fact``           Add a tester-authored (text, entity) fact
                               to persona.json, with contradiction
                               detection against existing entries.
``persona.resolve_corrections`` Batch-resolve queued persona contradictions
                               via correction model (runs an LLM review,
                               then applies replace / keep_new / keep_old
                               / keep_both actions).
============================== ==========================================

Why re-implement vs. call ``FactStore.extract_facts`` etc. directly?
--------------------------------------------------------------------
The upstream memory managers (``memory/*.py``) hardcode
``self._config_manager.get_model_api_config('summary')`` (resp.
``'correction'``). ``ConfigManager`` groups are set by the main app's
config UI; testbench surfaces its own 4 groups (``chat / simuser /
judge / memory``) in ``model_config.py`` and never writes them into
ConfigManager. So we'd be calling the **production** summary/correction
model, not what the tester picked in Settings → Models → memory. That's
a footgun: testers would silently burn their real budget when they
meant to test an offline endpoint.

So this runner owns the LLM call path: it imports the same prompts
(``config.prompts_memory``) and the same ``create_chat_llm`` factory,
but resolves base_url / api_key / model from ``session.model_config``
via :func:`chat_runner.resolve_group_config` — identical to how the
chat turn does it. Disk writes still go through the sandboxed
``ConfigManager.memory_dir`` so testbench isolation holds.

Preview vs. commit
------------------
* **Trigger** (``trigger_op``): runs the LLM (if any), computes a
  candidate result, **does not touch disk**. Stores the result in
  ``session.memory_previews[op]`` keyed by op id. Returns a
  :class:`MemoryPreviewResult` that the router hands to the UI drawer.
* **Commit** (``commit_op``): reads the cached preview (optionally with
  tester edits merged on top), writes to the corresponding JSON file
  (atomic), clears the cache entry.
* **Discard** (``discard_op``): clears the cache entry with no write.

Only one preview per op is kept; re-triggering overwrites. Entries
older than :data:`MEMORY_PREVIEW_TTL_SECONDS` are evicted on the next
access (:func:`prune_expired_previews`). No background tasks.

Limitations (documented on purpose, not TODO)
---------------------------------------------
* ``facts.extract`` preview skips the FTS5 semantic-dedup stage
  (``TimeIndexedMemory.asearch_facts``) because setting up the SQLite
  FTS5 index for a transient preview would require rollback-safe
  writes. Only SHA-256 exact-dedup is applied at preview time. Commit
  does NOT re-run FTS5 indexing either — the testbench editor is raw
  JSON; FTS5 is only consulted during the main app's own
  ``extract_facts`` pass, which happens naturally on the next real chat
  turn anyway.
* ``recent.compress`` summarizes the **current contents of
  recent.json** (what the memory subsystem would actually compress),
  not ``session.messages``. Testers who want to compress the live chat
  thread should either mirror it into recent.json first, or use Save &
  Edit on a manual recent.json tail.
* ``persona.add_fact`` preview reports the contradiction code only;
  it does not simulate what the LLM-driven ``resolve_corrections``
  would later do with a queued conflict. Testers get a separate
  ``persona.resolve_corrections`` trigger for that.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from utils.config_manager import get_config_manager
from utils.file_utils import robust_json_loads
from utils.llm_client import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    create_chat_llm,
    messages_from_dict,
    messages_to_dict,
)

from tests.testbench.chat_messages import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    SOURCE_EXTERNAL_EVENT_BANNER,
)
from tests.testbench.logger import python_logger
from tests.testbench.model_config import ModelGroupConfig
from tests.testbench.pipeline.chat_runner import (
    ChatConfigError,
    resolve_group_config,
)
from tests.testbench.pipeline.wire_tracker import (
    record_last_llm_wire,
    update_last_llm_wire_reply,
)
from tests.testbench.session_store import Session


# ── op identifiers ───────────────────────────────────────────────────

OP_RECENT_COMPRESS = "recent.compress"
OP_FACTS_EXTRACT = "facts.extract"
OP_REFLECT = "reflect"
OP_PERSONA_ADD_FACT = "persona.add_fact"
OP_PERSONA_RESOLVE_CORRECTIONS = "persona.resolve_corrections"

ALL_OPS: tuple[str, ...] = (
    OP_RECENT_COMPRESS,
    OP_FACTS_EXTRACT,
    OP_REFLECT,
    OP_PERSONA_ADD_FACT,
    OP_PERSONA_RESOLVE_CORRECTIONS,
)

# Which editor kind each op "targets" — the UI uses this to decide which
# memory subpage shows the trigger button.
OP_TO_KIND: dict[str, str] = {
    OP_RECENT_COMPRESS: "recent",
    OP_FACTS_EXTRACT: "facts",
    OP_REFLECT: "reflections",
    OP_PERSONA_ADD_FACT: "persona",
    OP_PERSONA_RESOLVE_CORRECTIONS: "persona",
}

# Preview cache TTL. 30 min is long enough for a tester to read the
# preview + tweak the payload + click Accept, but short enough that a
# stale cache entry won't silently overwrite a later edit.
MEMORY_PREVIEW_TTL_SECONDS: int = 30 * 60


# ── exceptions ──────────────────────────────────────────────────────


class MemoryOpError(RuntimeError):
    """Raised for trigger/commit problems the router should translate to HTTP.

    Attributes
    ----------
    code
        Stable machine-readable error code (``NoActiveSession``,
        ``PreviewMissing``, ``LlmFailed``, ``CharacterRequired``, ...).
    message
        Human-readable message (zh-CN) surfaced directly in the UI toast.
    status
        Suggested HTTP status code.
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}")


# ── data class ──────────────────────────────────────────────────────


@dataclass
class MemoryPreviewResult:
    """Return shape for ``trigger_op``.

    The router dumps this via ``.to_dict()`` into the JSON response;
    the UI stores it in a drawer component, allows tester edits, then
    POSTs back the (possibly-edited) payload to ``/commit/{op}``.
    """

    op: str
    payload: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat(timespec="seconds")
        return d


# ── session cache helpers ───────────────────────────────────────────


def prune_expired_previews(session: Session) -> None:
    """Drop cache entries older than :data:`MEMORY_PREVIEW_TTL_SECONDS`.

    Called lazily at the top of every ``trigger_op`` / ``commit_op`` /
    ``list_previews``. No background task — the cache is small (≤ 5
    entries) so a linear scan is fine.
    """
    if not session.memory_previews:
        return
    now = datetime.now()
    stale = [
        op for op, entry in session.memory_previews.items()
        if (now - entry.get("created_at", now)).total_seconds()
        > MEMORY_PREVIEW_TTL_SECONDS
    ]
    for op in stale:
        session.memory_previews.pop(op, None)


def _store_preview(session: Session, result: MemoryPreviewResult) -> None:
    session.memory_previews[result.op] = {
        "created_at": result.created_at,
        "payload": result.payload,
        "params": result.params,
        "warnings": result.warnings,
    }


def _pop_preview(session: Session, op: str) -> dict[str, Any]:
    prune_expired_previews(session)
    entry = session.memory_previews.pop(op, None)
    if entry is None:
        raise MemoryOpError(
            "PreviewMissing",
            f"op {op!r} 没有有效的预览; 请先调用 /api/memory/trigger/{op}",
            status=404,
        )
    return entry


def list_previews(session: Session) -> list[dict[str, Any]]:
    """Return ``[{op, created_at, warnings_count}]`` for UI badges."""
    prune_expired_previews(session)
    out: list[dict[str, Any]] = []
    for op, entry in session.memory_previews.items():
        out.append({
            "op": op,
            "created_at": entry["created_at"].isoformat(timespec="seconds"),
            "warnings_count": len(entry.get("warnings", [])),
        })
    return out


# ── common helpers ──────────────────────────────────────────────────


def _require_character(session: Session) -> str:
    name = (session.persona or {}).get("character_name") or ""
    name = str(name).strip()
    if not name:
        raise MemoryOpError(
            "NoCharacterSelected",
            "session.persona.character_name 为空; 请先在 Setup → Persona "
            "填角色名, 或从 Import 导入一个真实角色.",
            status=409,
        )
    return name


def _memory_dir(character: str) -> Path:
    cm = get_config_manager()
    p = Path(str(cm.memory_dir)) / character
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_json_list(path: Path) -> list[Any]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        raise MemoryOpError(
            "InvalidMemoryJson",
            f"{path.name} 不是合法 JSON: {exc}",
            status=500,
        ) from exc
    if not isinstance(data, list):
        raise MemoryOpError(
            "InvalidRootType",
            f"{path.name} 顶层必须是 list, 实际 {type(data).__name__}",
            status=500,
        )
    return data


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        raise MemoryOpError(
            "InvalidMemoryJson",
            f"{path.name} 不是合法 JSON: {exc}",
            status=500,
        ) from exc
    if not isinstance(data, dict):
        raise MemoryOpError(
            "InvalidRootType",
            f"{path.name} 顶层必须是 dict, 实际 {type(data).__name__}",
            status=500,
        )
    return data


# P24 §4.1.2 (2026-04-21): was a local non-fsync copy, now delegates to
# the unified atomic_io chokepoint so this module shares the fsync guard
# with persistence.py / memory_router / script_runner / scoring_schema /
# snapshot_store. Callers keep using ``_atomic_write_json`` to minimize
# diff; the helper now just wraps ``atomic_io.atomic_write_json``.
from tests.testbench.pipeline.atomic_io import atomic_write_json as _atomic_write_json  # noqa: E402


def _resolve_memory_cfg(session: Session) -> ModelGroupConfig:
    """Resolve the ``memory`` group — delegates to chat_runner's logic.

    We deliberately reuse the *same* three-layer fallback (user-typed →
    preset-bundled → tests/api_keys.json) and Lanlan-free rewrite, so
    memory ops and chat ops have identical key-resolution semantics.
    """
    try:
        return resolve_group_config(session, "memory")
    except ChatConfigError as exc:
        raise MemoryOpError(exc.code, exc.message, status=412) from exc


def _llm_for_memory(session: Session, *, temperature: float):
    """Instantiate a ChatOpenAI bound to the ``memory`` group config."""
    cfg = _resolve_memory_cfg(session)
    return create_chat_llm(
        cfg.model, cfg.base_url, cfg.api_key,
        temperature=temperature,
    )


def _strip_code_fence(raw: str) -> str:
    """Mirror upstream's ``strip ```json``` / ``` ``` fences`` logic."""
    s = (raw or "").strip()
    if s.startswith("```"):
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', s)
        if match:
            return match.group(1).strip()
        return s.replace("```json", "").replace("```", "").strip()
    return s


def _messages_to_wire_lines(
    messages: Iterable[Any],
    name_mapping: dict[str, str],
) -> str:
    """Render a ``[role | content]`` block for LLM prompts.

    Upstream ``compress_history`` / ``extract_facts`` both build the
    same shape. Keeping a single helper avoids subtle drift (e.g. one
    version joining with newlines, the other with empty string).
    """
    lines: list[str] = []
    for msg in messages:
        msg_type = getattr(msg, "type", "") or ""
        role = name_mapping.get(msg_type, msg_type or "?")
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get("text", f"|{item.get('type', '')}|"))
                else:
                    parts.append(str(item))
            text = "\n".join(parts)
        else:
            text = str(content)
        lines.append(f"{role} | {text}")
    return "\n".join(lines)


def _build_name_mapping(session: Session, character: str) -> dict[str, str]:
    """Derive the ``human / ai / system`` → display-name mapping.

    Mirrors :func:`prompt_builder._build_name_mapping` but inlined to
    avoid a circular import (prompt_builder already imports from this
    module for P14). Values are used only for prompt rendering.
    """
    master_name = (session.persona or {}).get("master_name") or "主人"
    return {
        "human": str(master_name),
        "ai": character,
        "system": "system",
    }


# ── op 1: recent.compress ───────────────────────────────────────────


async def _preview_recent_compress(
    session: Session,
    params: dict[str, Any],
) -> MemoryPreviewResult:
    """Summarize the **head** (oldest) slice of recent.json.

    The slicing is ``to_compress = messages[:tail_count]`` /
    ``kept = messages[tail_count:]``: the **first** ``tail_count``
    messages (the oldest, since recent.json is stored chronologically
    asc) are sent to the summarizer; the later messages are preserved
    verbatim. The legacy parameter name ``tail_count`` is misleading
    — it is the count of head messages to compress, not the count of
    tail messages to keep — but renaming it would break the public
    HTTP contract / UI label / smoke fixtures, so we keep the name and
    document the actual semantics here. (GH AI-review issue, 2nd batch
    #3.)

    Parameters (all optional)
    -------------------------
    tail_count : int
        How many messages at the **start (oldest)** of recent.json to
        feed the summarizer. Defaults to
        ``len(recent) - max_history_length + 1`` to mirror the upstream
        ``update_history`` cut point so the post-compress wire size is
        ``1 (memo) + (max_history_length - 1) ≈ max_history_length``.
        Clamped to ``[1, len(recent)]``.
    detailed : bool
        If True, use ``get_detailed_recent_history_manager_prompt``
        (preserves more detail). Default False.

    Payload
    -------
    ``{summary, memo_system_content, tail_messages, kept_tail,
    tail_count, total_before, detailed}``

    The UI shows ``memo_system_content`` (the actual string that will
    replace the summarized **head** slice — the commit path writes
    ``[memo] + kept_tail`` so the memo lands at the *start* of the
    new recent.json) as an editable textarea and ``tail_messages``
    (despite the name, this is the *head* slice — the messages the
    summarizer ate) as a read-only list so the tester can verify the
    cut-point visually.
    """
    from config.prompts_memory import (
        get_detailed_recent_history_manager_prompt,
        get_recent_history_manager_prompt,
    )
    from utils.language_utils import get_global_language

    character = _require_character(session)
    name_mapping = _build_name_mapping(session, character)

    recent_path = _memory_dir(character) / "recent.json"
    recent_dicts = _read_json_list(recent_path)
    if not recent_dicts:
        raise MemoryOpError(
            "RecentEmpty",
            "recent.json 为空, 没有可压缩的消息. 先在 Chat 页发几条消息, "
            "或在 Recent 编辑器手动填充.",
            status=409,
        )

    messages = messages_from_dict(recent_dicts)
    total = len(messages)

    max_history_length = int(params.get("max_history_length") or 10)
    default_tail = max(1, total - max_history_length + 1)
    tail_count = int(params.get("tail_count") or default_tail)
    tail_count = max(1, min(tail_count, total))
    detailed = bool(params.get("detailed") or False)

    to_compress = messages[:tail_count]
    kept = messages[tail_count:]

    mapping_for_prompt = dict(name_mapping)
    mapping_for_prompt["ai"] = character
    messages_text = _messages_to_wire_lines(to_compress, mapping_for_prompt)

    lang = get_global_language()
    if detailed:
        prompt = get_detailed_recent_history_manager_prompt(lang) % messages_text
    else:
        prompt = get_recent_history_manager_prompt(lang).replace("%s", messages_text)

    llm = _llm_for_memory(session, temperature=0.3)
    warnings: list[str] = []
    summary_text = ""
    _memory_wire = [{"role": ROLE_USER, "content": prompt}]
    try:
        record_last_llm_wire(
            session,
            _memory_wire,
            source="memory.llm",
            note=f"memory.recent.compress:tail={tail_count}/{total}",
        )
    except Exception as exc:  # noqa: BLE001 — observability must not block LLM
        python_logger().debug(
            "memory.recent.compress: record_last_llm_wire failed: %s: %s",
            type(exc).__name__, exc,
        )
    try:
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()
        raw = _strip_code_fence(getattr(resp, "content", "") or "")
        try:
            parsed = robust_json_loads(raw)
        except Exception as exc:
            warnings.append(f"摘要模型返回非 JSON, 解析失败: {exc}")
            parsed = {}
        if isinstance(parsed, dict) and "对话摘要" in parsed:
            summary_text = str(parsed["对话摘要"]).strip()
        elif isinstance(parsed, str) and parsed:
            summary_text = parsed.strip()
            warnings.append("摘要模型返回纯字符串 (非标准 {'对话摘要': ...} 格式), 已按单段处理.")
        else:
            warnings.append("摘要模型返回内容缺少 '对话摘要' 字段. 原始返回片段已截断.")
            summary_text = raw[:200] if raw else ""
        try:
            update_last_llm_wire_reply(session, reply_chars=len(raw))
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        try:
            update_last_llm_wire_reply(session, reply_chars=-1)
        except Exception:  # noqa: BLE001
            pass
        raise MemoryOpError(
            "LlmFailed",
            f"摘要模型调用失败: {type(exc).__name__}: {exc}",
            status=502,
        ) from exc

    # Build the "replacement memo" — exactly the same rendering upstream
    # applies (`MEMORY_MEMO_WITH_SUMMARY` template), so commit can just
    # drop this into recent.json as a single system message.
    from config.prompts_sys import MEMORY_MEMO_EMPTY, MEMORY_MEMO_WITH_SUMMARY, _loc
    if summary_text:
        memo_content = _loc(MEMORY_MEMO_WITH_SUMMARY, lang).format(summary=summary_text)
    else:
        memo_content = _loc(MEMORY_MEMO_EMPTY, lang)
        warnings.append("无法从摘要中提取有效内容, 预览生成了空备忘录 (commit 会写入空 memo).")

    payload: dict[str, Any] = {
        "summary": summary_text,
        "memo_system_content": memo_content,
        "tail_messages": messages_to_dict(to_compress),
        "kept_tail": messages_to_dict(kept),
        "tail_count": tail_count,
        "kept_count": len(kept),
        "total_before": total,
        "total_after": 1 + len(kept),
        "detailed": detailed,
    }

    return MemoryPreviewResult(
        op=OP_RECENT_COMPRESS,
        payload=payload,
        params={
            "tail_count": tail_count,
            "detailed": detailed,
            "max_history_length": max_history_length,
        },
        warnings=warnings,
    )


async def _commit_recent_compress(
    session: Session,
    cached: dict[str, Any],
    edits: dict[str, Any],
) -> dict[str, Any]:
    """Replace recent.json with ``[memo] + kept_tail``.

    The ``memo`` summarizes the **head** (oldest) slice that
    :func:`_preview_recent_compress` already cut off; ``kept_tail`` is
    the *later* messages preserved verbatim. Despite the legacy
    parameter name ``tail_count`` (= count of head messages compressed,
    not count of tail messages kept — see preview's docstring for the
    history of this naming), the resulting recent.json layout is
    chronological: ``[1 system memo of older context] + [latest N
    messages]``.

    Accepted edits
    --------------
    ``memo_system_content`` — override the summary memo text. Other
    fields in the cached payload are ignored (commit is always
    "replace recent.json with this exact memo"). Tester can't change
    the cut-point at commit time; re-trigger with a different
    ``tail_count`` to move the cut.
    """
    character = _require_character(session)
    recent_path = _memory_dir(character) / "recent.json"

    payload = cached["payload"]
    memo_content = edits.get("memo_system_content")
    if not isinstance(memo_content, str) or not memo_content.strip():
        memo_content = payload["memo_system_content"]

    memo_msg = SystemMessage(content=memo_content)
    kept_msgs = messages_from_dict(payload["kept_tail"])
    new_messages = [memo_msg] + kept_msgs
    new_dicts = messages_to_dict(new_messages)

    _atomic_write_json(recent_path, new_dicts)
    python_logger().info(
        "memory_runner: recent.compress committed character=%s total=%d → %d",
        character, payload["total_before"], len(new_dicts),
    )
    return {
        "op": OP_RECENT_COMPRESS,
        "path": str(recent_path),
        "written_count": len(new_dicts),
    }


# ── op 2: facts.extract ─────────────────────────────────────────────


async def _preview_facts_extract(
    session: Session,
    params: dict[str, Any],
) -> MemoryPreviewResult:
    """Ask the memory LLM to pull atomic facts from a conversation window.

    Parameters
    ----------
    source : "session.messages" | "recent.json"
        Which message buffer to feed the extractor. ``session.messages``
        uses the live chat thread (including manual injects); the other
        uses the stored recent.json.  Default = ``session.messages``.
    min_importance : int
        Drop facts with importance < this value. Upstream hardcodes 5;
        we expose it because testbench persona editor already shows 0-10
        range. Default 5.

    Payload
    -------
    ``{extracted: [fact_dict], source, min_importance, total_existing}``

    ``extracted`` items are directly editable (text / importance /
    entity / tags). Commit appends them to facts.json after a second
    SHA-256 dedup pass (protects against the tester clicking Commit
    twice or another tab writing a duplicate).
    """
    from config.prompts_memory import get_fact_extraction_prompt
    from utils.language_utils import get_global_language

    character = _require_character(session)
    name_mapping = _build_name_mapping(session, character)

    source = params.get("source") or "session.messages"
    min_importance = int(params.get("min_importance") or 5)

    if source == "recent.json":
        recent_path = _memory_dir(character) / "recent.json"
        messages = messages_from_dict(_read_json_list(recent_path))
    else:
        source = "session.messages"
        messages = _session_messages_to_langchain(session)

    if not messages:
        raise MemoryOpError(
            "NoMessages",
            f"source={source!r} 没有可用的消息. 先发几条消息或切换 source.",
            status=409,
        )

    mapping_for_prompt = dict(name_mapping)
    mapping_for_prompt["ai"] = character
    master_display = name_mapping.get("human", "主人")
    conversation_text = _messages_to_wire_lines(messages, mapping_for_prompt)

    lang = get_global_language()
    prompt = get_fact_extraction_prompt(lang)
    prompt = prompt.replace("{CONVERSATION}", conversation_text)
    prompt = prompt.replace("{LANLAN_NAME}", character)
    prompt = prompt.replace("{MASTER_NAME}", master_display)

    llm = _llm_for_memory(session, temperature=0.3)
    warnings: list[str] = []
    _memory_wire = [{"role": ROLE_USER, "content": prompt}]
    try:
        record_last_llm_wire(
            session,
            _memory_wire,
            source="memory.llm",
            note=f"memory.facts.extract:src={source}:{len(messages)}msgs",
        )
    except Exception as exc:  # noqa: BLE001 — observability must not block LLM
        python_logger().debug(
            "memory.facts.extract: record_last_llm_wire failed: %s: %s",
            type(exc).__name__, exc,
        )
    try:
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()
        raw = _strip_code_fence(getattr(resp, "content", "") or "")
        extracted = robust_json_loads(raw) if raw else []
        try:
            update_last_llm_wire_reply(session, reply_chars=len(raw))
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        try:
            update_last_llm_wire_reply(session, reply_chars=-1)
        except Exception:  # noqa: BLE001
            pass
        raise MemoryOpError(
            "LlmFailed",
            f"事实提取模型调用失败: {type(exc).__name__}: {exc}",
            status=502,
        ) from exc

    if not isinstance(extracted, list):
        warnings.append(f"模型返回非数组 (type={type(extracted).__name__}), 已视为空.")
        extracted = []

    facts_path = _memory_dir(character) / "facts.json"
    existing = _read_json_list(facts_path)
    existing_hashes = {
        f.get("hash") for f in existing if isinstance(f, dict) and f.get("hash")
    }

    candidates: list[dict[str, Any]] = []
    duplicates = 0
    low_importance = 0
    now = datetime.now()

    for raw_fact in extracted:
        if not isinstance(raw_fact, dict):
            continue
        text = str(raw_fact.get("text") or "").strip()
        if not text:
            continue
        try:
            # OverflowError covers an LLM JSON parse turning ``"1e400"``
            # into ``inf`` then ``int(inf)``. 2nd-batch AI review #4
            # family extension.
            importance = int(raw_fact.get("importance", 5))
        except (TypeError, ValueError, OverflowError):
            importance = 5
        if importance < min_importance:
            low_importance += 1
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if content_hash in existing_hashes:
            duplicates += 1
            continue
        candidates.append({
            "id": f"fact_{now.strftime('%Y%m%d%H%M%S')}_{content_hash[:8]}",
            "text": text,
            "importance": importance,
            "entity": str(raw_fact.get("entity") or "master"),
            "tags": list(raw_fact.get("tags") or []),
            "hash": content_hash,
            "created_at": now.isoformat(),
            "absorbed": False,
        })
        existing_hashes.add(content_hash)

    if duplicates:
        warnings.append(f"SHA-256 去重丢弃了 {duplicates} 条已存在的事实.")
    if low_importance:
        warnings.append(
            f"{low_importance} 条事实因 importance < {min_importance} 被丢弃."
        )
    if not candidates and not warnings:
        warnings.append("模型没有返回任何可提取的事实.")

    return MemoryPreviewResult(
        op=OP_FACTS_EXTRACT,
        payload={
            "extracted": candidates,
            "source": source,
            "total_existing": len(existing),
            "message_count": len(messages),
        },
        params={
            "source": source,
            "min_importance": min_importance,
        },
        warnings=warnings,
    )


async def _commit_facts_extract(
    session: Session,
    cached: dict[str, Any],
    edits: dict[str, Any],
) -> dict[str, Any]:
    """Append the (possibly tester-edited) fact list to facts.json.

    Tester edits land under ``edits['extracted']``. We trust the list
    shape (memory_router's PUT does the strict validation) but re-hash
    + re-dedup to protect against commit-commit double-apply.
    """
    character = _require_character(session)
    facts_path = _memory_dir(character) / "facts.json"

    edited = edits.get("extracted")
    if not isinstance(edited, list):
        edited = cached["payload"]["extracted"]

    existing = _read_json_list(facts_path)
    existing_hashes = {
        f.get("hash") for f in existing if isinstance(f, dict) and f.get("hash")
    }

    appended: list[dict[str, Any]] = []
    skipped = 0
    now = datetime.now()
    for item in edited:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            skipped += 1
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if content_hash in existing_hashes:
            skipped += 1
            continue
        fact = {
            "id": item.get("id") or
                f"fact_{now.strftime('%Y%m%d%H%M%S')}_{content_hash[:8]}",
            "text": text,
            "importance": int(item.get("importance", 5) or 5),
            "entity": str(item.get("entity") or "master"),
            "tags": list(item.get("tags") or []),
            "hash": content_hash,
            "created_at": item.get("created_at") or now.isoformat(),
            "absorbed": bool(item.get("absorbed", False)),
        }
        existing.append(fact)
        existing_hashes.add(content_hash)
        appended.append(fact)

    _atomic_write_json(facts_path, existing)
    python_logger().info(
        "memory_runner: facts.extract committed character=%s +%d (skipped %d)",
        character, len(appended), skipped,
    )
    return {
        "op": OP_FACTS_EXTRACT,
        "path": str(facts_path),
        "appended_count": len(appended),
        "skipped_count": skipped,
        "total_after": len(existing),
    }


# ── op 3: reflect ───────────────────────────────────────────────────


async def _preview_reflect(
    session: Session,
    params: dict[str, Any],
) -> MemoryPreviewResult:
    """Synthesize a higher-level reflection from unabsorbed facts.

    Parameters
    ----------
    min_facts : int
        Minimum unabsorbed-fact count required to trigger reflection.
        Upstream constant is 5; overridable for testbench probing.

    Payload
    -------
    ``{reflection: {id,text,entity,status,source_fact_ids,...},
       source_facts: [fact_dict], unabsorbed_count}``

    Commit appends ``reflection`` to reflections.json and marks every
    id in ``source_fact_ids`` as ``absorbed=True`` in facts.json.
    """
    from config.prompts_memory import get_reflection_prompt
    from utils.language_utils import get_global_language

    # Lazy import to avoid pulling MIN_FACTS_FOR_REFLECTION constant via
    # module-level import; main app's reflection.py imports many heavy
    # deps at module load.
    from memory.reflection import MIN_FACTS_FOR_REFLECTION, REFLECTION_COOLDOWN_MINUTES

    character = _require_character(session)
    name_mapping = _build_name_mapping(session, character)
    master_display = name_mapping.get("human", "主人")

    min_facts = int(params.get("min_facts") or MIN_FACTS_FOR_REFLECTION)

    facts_path = _memory_dir(character) / "facts.json"
    facts = _read_json_list(facts_path)
    unabsorbed = [
        f for f in facts
        if isinstance(f, dict) and not bool(f.get("absorbed", False))
    ]
    if len(unabsorbed) < min_facts:
        raise MemoryOpError(
            "NotEnoughFacts",
            (
                f"未吸收事实仅 {len(unabsorbed)} 条, 低于 reflect 阈值 "
                f"{min_facts}. 先跑几次 facts.extract 或手动添加事实."
            ),
            status=409,
        )

    facts_text = "\n".join(
        f"- {f.get('text', '')} (importance: {f.get('importance', 5)})"
        for f in unabsorbed
    )
    lang = get_global_language()
    prompt = get_reflection_prompt(lang)
    prompt = prompt.replace("{FACTS}", facts_text)
    prompt = prompt.replace("{LANLAN_NAME}", character)
    prompt = prompt.replace("{MASTER_NAME}", master_display)

    llm = _llm_for_memory(session, temperature=0.5)
    warnings: list[str] = []
    _memory_wire = [{"role": ROLE_USER, "content": prompt}]
    try:
        record_last_llm_wire(
            session,
            _memory_wire,
            source="memory.llm",
            note=f"memory.reflect:unabsorbed={len(unabsorbed)}",
        )
    except Exception as exc:  # noqa: BLE001 — observability must not block LLM
        python_logger().debug(
            "memory.reflect: record_last_llm_wire failed: %s: %s",
            type(exc).__name__, exc,
        )
    try:
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()
        raw = _strip_code_fence(getattr(resp, "content", "") or "")
        result = robust_json_loads(raw) if raw else {}
        try:
            update_last_llm_wire_reply(session, reply_chars=len(raw))
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        try:
            update_last_llm_wire_reply(session, reply_chars=-1)
        except Exception:  # noqa: BLE001
            pass
        raise MemoryOpError(
            "LlmFailed",
            f"反思模型调用失败: {type(exc).__name__}: {exc}",
            status=502,
        ) from exc

    if not isinstance(result, dict):
        raise MemoryOpError(
            "LlmBadShape",
            f"反思模型返回非 dict (type={type(result).__name__}).",
            status=502,
        )
    reflection_text = str(result.get("reflection", "")).strip()
    reflection_entity = result.get("entity", "relationship")
    if reflection_entity not in ("master", "neko", "relationship"):
        warnings.append(
            f"反思模型返回了非法 entity {reflection_entity!r}, 已归位为 'relationship'."
        )
        reflection_entity = "relationship"
    if not reflection_text:
        raise MemoryOpError(
            "LlmEmptyReflection",
            "反思模型返回了空 reflection. 请稍后重试或换模型.",
            status=502,
        )

    now = datetime.now()
    reflection = {
        "id": f"ref_{now.strftime('%Y%m%d%H%M%S')}",
        "text": reflection_text,
        "entity": reflection_entity,
        "status": "pending",
        "source_fact_ids": [f.get("id") for f in unabsorbed if f.get("id")],
        "created_at": now.isoformat(),
        "feedback": None,
        "next_eligible_at":
            (now + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat(),
    }

    return MemoryPreviewResult(
        op=OP_REFLECT,
        payload={
            "reflection": reflection,
            "source_facts": unabsorbed,
            "unabsorbed_count": len(unabsorbed),
        },
        params={"min_facts": min_facts},
        warnings=warnings,
    )


async def _commit_reflect(
    session: Session,
    cached: dict[str, Any],
    edits: dict[str, Any],
) -> dict[str, Any]:
    """Append reflection + mark source facts absorbed."""
    character = _require_character(session)
    refl_path = _memory_dir(character) / "reflections.json"
    facts_path = _memory_dir(character) / "facts.json"

    edited_refl = edits.get("reflection") if isinstance(edits.get("reflection"), dict) else None
    reflection = dict(cached["payload"]["reflection"])
    if edited_refl:
        # Whitelist editable fields — keep id, timestamps, source_fact_ids intact.
        for k in ("text", "entity", "status", "feedback"):
            if k in edited_refl:
                reflection[k] = edited_refl[k]

    reflections = _read_json_list(refl_path)
    reflections.append(reflection)
    _atomic_write_json(refl_path, reflections)

    absorbed_ids = set(reflection.get("source_fact_ids") or [])
    facts = _read_json_list(facts_path)
    marked = 0
    for f in facts:
        if isinstance(f, dict) and f.get("id") in absorbed_ids:
            f["absorbed"] = True
            marked += 1
    _atomic_write_json(facts_path, facts)

    python_logger().info(
        "memory_runner: reflect committed character=%s id=%s marked=%d",
        character, reflection.get("id"), marked,
    )
    return {
        "op": OP_REFLECT,
        "reflection_id": reflection.get("id"),
        "reflections_total": len(reflections),
        "facts_marked_absorbed": marked,
    }


# ── op 4: persona.add_fact ──────────────────────────────────────────


async def _preview_persona_add_fact(
    session: Session,
    params: dict[str, Any],
) -> MemoryPreviewResult:
    """Classify a tester-provided (text, entity) against existing persona.

    Parameters (all required)
    -------------------------
    text : str — the fact to add.
    entity : str — section key; defaults to ``master``.

    Payload
    -------
    ``{code, conflicting_text, text, entity, existing_count, rejected_texts}``

    ``code`` is one of:

    * ``"added"`` — no contradiction; commit will append.
    * ``"rejected_card"`` — conflicts with a ``character_card``-sourced
      fact. Commit will refuse (no write).
    * ``"queued"`` — conflicts with a non-card fact; commit will enqueue
      a correction for later LLM review.

    The heuristic matches the real app's ``_texts_may_contradict``
    (n-gram overlap ≥ 0.4 with master/neko names stripped out).
    """
    from memory.persona import PersonaManager

    character = _require_character(session)
    text = str(params.get("text") or "").strip()
    entity = str(params.get("entity") or "master").strip() or "master"
    if not text:
        raise MemoryOpError(
            "MissingText",
            "persona.add_fact 需要非空 text 参数.",
            status=422,
        )

    pm = PersonaManager()
    persona = await pm.aensure_persona(character)
    section_facts = pm._get_section_facts(persona, entity)  # noqa: SLF001
    stop_names = await pm._aget_entity_stop_names()         # noqa: SLF001

    code, conflicting_text = pm._evaluate_fact_contradiction(  # noqa: SLF001
        character, text, section_facts, stop_names,
    )
    normalized_code = code or PersonaManager.FACT_ADDED

    warnings: list[str] = []
    if normalized_code == PersonaManager.FACT_REJECTED_CARD:
        warnings.append(
            "与 character_card 条目矛盾; Commit 会拒绝, 不会写入 persona.json."
        )
    elif normalized_code == PersonaManager.FACT_QUEUED_CORRECTION:
        warnings.append(
            "与已有非卡条目矛盾; Commit 会把这条矛盾加入 persona_corrections.json, "
            "请随后触发 persona.resolve_corrections 让 correction 模型裁决."
        )

    return MemoryPreviewResult(
        op=OP_PERSONA_ADD_FACT,
        payload={
            "code": normalized_code,
            "conflicting_text": conflicting_text or "",
            "text": text,
            "entity": entity,
            "existing_count": len(section_facts),
            # Show the section head for tester to eyeball the neighborhood.
            "section_preview": [
                (e.get("text") if isinstance(e, dict) else str(e))
                for e in section_facts[:10]
            ],
        },
        params={"text": text, "entity": entity},
        warnings=warnings,
    )


async def _commit_persona_add_fact(
    session: Session,
    cached: dict[str, Any],
    edits: dict[str, Any],
) -> dict[str, Any]:
    """Write the fact via ``PersonaManager.aadd_fact``.

    We rebuild `(text, entity)` from cached params rather than the
    payload so tester edits on the drawer's ``text`` field flow through
    as a re-preview (the UI re-triggers when ``text`` changes). That
    keeps the contradiction code in the payload accurate.
    """
    from memory.persona import PersonaManager

    character = _require_character(session)
    text = str(edits.get("text") or cached["params"].get("text") or "").strip()
    entity = str(edits.get("entity") or cached["params"].get("entity") or "master").strip() or "master"
    if not text:
        raise MemoryOpError("MissingText", "text 为空, 无法提交.", status=422)

    pm = PersonaManager()
    code = await pm.aadd_fact(character, text, entity=entity, source="manual")
    python_logger().info(
        "memory_runner: persona.add_fact committed character=%s entity=%s code=%s",
        character, entity, code,
    )
    return {
        "op": OP_PERSONA_ADD_FACT,
        "code": code,
        "text": text,
        "entity": entity,
    }


# ── op 5: persona.resolve_corrections ───────────────────────────────


async def _preview_persona_resolve_corrections(
    session: Session,
    params: dict[str, Any],
) -> MemoryPreviewResult:
    """Run correction model on the pending queue; preview LLM suggestions.

    No params. Reads persona_corrections.json; if empty, raises
    ``QueueEmpty`` (409) so the UI can tell tester to first queue a
    conflict via ``persona.add_fact``.

    Payload
    -------
    ``{actions: [{index, action, text, old_text, new_text, entity}],
       queue_size}``

    Where ``action`` is one of ``replace / keep_new / keep_old /
    keep_both`` — direct mirror of the correction prompt schema. Tester
    can tweak ``action`` / ``text`` per row before commit.
    """
    from config.prompts_memory import persona_correction_prompt
    from memory.persona import PersonaManager

    character = _require_character(session)
    pm = PersonaManager()
    corrections = await pm.aload_pending_corrections(character)
    if not corrections:
        raise MemoryOpError(
            "QueueEmpty",
            "persona_corrections.json 没有待裁决的矛盾. 先用 persona.add_fact "
            "加一条会触发冲突的事实.",
            status=409,
        )

    pairs = [
        (i, item) for i, item in enumerate(corrections)
        if item.get("old_text") and item.get("new_text")
    ]
    if not pairs:
        raise MemoryOpError(
            "QueueMalformed",
            "矛盾队列内所有条目都缺少 old_text / new_text, 请清理 "
            "persona_corrections.json 后重试.",
            status=500,
        )

    batch_text = "\n".join(
        f"[{i}] 已有: {item['old_text']} | 新观察: {item['new_text']}"
        for i, item in pairs
    )
    prompt = persona_correction_prompt.format(pairs=batch_text, count=len(pairs))

    llm = _llm_for_memory(session, temperature=0.3)
    _memory_wire = [{"role": ROLE_USER, "content": prompt}]
    try:
        record_last_llm_wire(
            session,
            _memory_wire,
            source="memory.llm",
            note=f"memory.persona.resolve_corrections:pairs={len(pairs)}",
        )
    except Exception as exc:  # noqa: BLE001 — observability must not block LLM
        python_logger().debug(
            "memory.persona.resolve_corrections: record_last_llm_wire "
            "failed: %s: %s", type(exc).__name__, exc,
        )
    try:
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()
        raw = _strip_code_fence(getattr(resp, "content", "") or "")
        results = robust_json_loads(raw) if raw else []
        try:
            update_last_llm_wire_reply(session, reply_chars=len(raw))
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        try:
            update_last_llm_wire_reply(session, reply_chars=-1)
        except Exception:  # noqa: BLE001
            pass
        raise MemoryOpError(
            "LlmFailed",
            f"correction 模型调用失败: {type(exc).__name__}: {exc}",
            status=502,
        ) from exc

    if not isinstance(results, list):
        results = [results]

    actions: list[dict[str, Any]] = []
    warnings: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            warnings.append(f"丢弃非 dict 结果: {result!r}")
            continue
        try:
            idx = int(result.get("index", -1))
        except (TypeError, ValueError, OverflowError):
            warnings.append(f"丢弃无法解析 index 的结果: {result!r}")
            continue
        if idx < 0 or idx >= len(corrections):
            warnings.append(f"丢弃越界 index={idx} 的结果.")
            continue
        item = corrections[idx]
        action = result.get("action", "keep_both")
        if action not in ("replace", "keep_new", "keep_old", "keep_both"):
            warnings.append(f"非法 action={action!r} 于 index {idx}, 归位为 keep_both.")
            action = "keep_both"
        actions.append({
            "index": idx,
            "action": action,
            "text": str(result.get("text") or item.get("new_text", "")),
            "old_text": item.get("old_text", ""),
            "new_text": item.get("new_text", ""),
            "entity": item.get("entity", "master"),
        })

    if not actions:
        warnings.append("correction 模型没有返回任何可执行动作.")

    return MemoryPreviewResult(
        op=OP_PERSONA_RESOLVE_CORRECTIONS,
        payload={
            "actions": actions,
            "queue_size": len(corrections),
        },
        params={},
        warnings=warnings,
    )


async def _commit_persona_resolve_corrections(
    session: Session,
    cached: dict[str, Any],
    edits: dict[str, Any],
) -> dict[str, Any]:
    """Apply the (possibly edited) action list to persona.json.

    Mirrors :meth:`PersonaManager.resolve_corrections` lines 783-843 but
    runs against the cached action list instead of re-calling the LLM.
    """
    from memory.persona import PersonaManager

    character = _require_character(session)
    pm = PersonaManager()
    persona = await pm.aensure_persona(character)
    corrections = await pm.aload_pending_corrections(character)

    edited_actions = edits.get("actions")
    if not isinstance(edited_actions, list):
        edited_actions = cached["payload"]["actions"]

    processed_indices: set[int] = set()
    resolved = 0
    for act in edited_actions:
        if not isinstance(act, dict):
            continue
        try:
            idx = int(act.get("index", -1))
        except (TypeError, ValueError, OverflowError):
            continue
        if idx < 0 or idx >= len(corrections):
            continue
        item = corrections[idx]
        action = act.get("action", "keep_both")
        merged_text = str(act.get("text") or item.get("new_text", ""))
        entity = item.get("entity", "master")
        old_text = item.get("old_text", "")
        new_text = item.get("new_text", "")
        section_facts = pm._get_section_facts(persona, entity)  # noqa: SLF001

        if action == "replace":
            for j, existing in enumerate(section_facts):
                et = (existing.get("text", "") if isinstance(existing, dict)
                      else str(existing))
                if et == old_text:
                    section_facts[j] = pm._normalize_entry(merged_text)  # noqa: SLF001
                    break
        elif action == "keep_new":
            section_facts[:] = [
                e for e in section_facts
                if (e.get("text", "") if isinstance(e, dict) else str(e)) != old_text
            ]
            section_facts.append(pm._normalize_entry(new_text))  # noqa: SLF001
        elif action == "keep_old":
            pass  # no-op
        else:  # keep_both (or fallback)
            existing_texts = {
                (e.get("text", "") if isinstance(e, dict) else str(e))
                for e in section_facts
            }
            if new_text not in existing_texts:
                section_facts.append(pm._normalize_entry(new_text))  # noqa: SLF001
        resolved += 1
        processed_indices.add(idx)

    if resolved:
        # Two-file commit: persona first, then corrections queue. We
        # cannot get cross-file atomicity with the project's single-file
        # ``atomic_write_json`` chokepoint, so we lean on the actions'
        # **idempotency under retry** to bound the worst case (GH AI-
        # review issue #8 — full analysis):
        #   * persona write succeeds + corrections write fails ⇒ next
        #     ``persona.resolve_corrections`` call re-processes the same
        #     entries. ``keep_both`` already de-dupes via
        #     ``existing_texts``; ``keep_new`` removes-then-appends so
        #     the second run is a no-op (old_text gone the first time);
        #     ``keep_old`` is a no-op anyway. Only ``replace`` becomes
        #     an append-on-retry (old_text already replaced ⇒ fall-
        #     through, then *no* re-append because the loop's
        #     ``replace`` branch only writes inside the matching ``if``).
        #     Net effect of retry: at worst a duplicate entry on
        #     ``replace`` if the user manually re-resolves the same
        #     correction, *not* on automatic retry — acceptable.
        #   * persona write fails ⇒ corrections queue is left untouched
        #     (we never reach the second ``await``), so the user sees
        #     the queue intact and can retry from the editor. No data
        #     loss.
        # Reversing the order ("corrections first") would be **strictly
        # worse**: a persona-write failure after the queue is drained
        # would silently drop the user's curated correction forever.
        # If a future change needs stricter cross-file atomicity, the
        # right move is a single combined journal file + a recovery
        # pass at boot, not a re-ordering of these two writes.
        await pm.asave_persona(character, persona)
        remaining = [c for i, c in enumerate(corrections) if i not in processed_indices]
        from utils.file_utils import atomic_write_json_async
        await atomic_write_json_async(
            pm._corrections_path(character),  # noqa: SLF001
            remaining, indent=2, ensure_ascii=False,
        )

    python_logger().info(
        "memory_runner: persona.resolve_corrections committed character=%s resolved=%d",
        character, resolved,
    )
    return {
        "op": OP_PERSONA_RESOLVE_CORRECTIONS,
        "resolved_count": resolved,
        "remaining_queue": len(corrections) - resolved,
    }


# ── helper: session.messages → LangChain BaseMessage list ───────────


def _session_messages_to_langchain(session: Session) -> list[Any]:
    """Convert ``session.messages`` dicts into LangChain ``BaseMessage`` objects.

    The testbench message schema (``chat_messages.py``) uses
    ``role ∈ {user, assistant, system}`` + ``content``; upstream
    ``extract_facts`` / ``compress_history`` both iterate LangChain
    messages (``msg.type`` + ``msg.content``). We do the mapping
    inline rather than round-tripping through ``messages_from_dict``
    because our dicts aren't in the on-disk ``{type, data}`` format.
    """
    out: list[Any] = []
    for m in session.messages:
        # Drop external-event banner pseudo-messages so they never reach
        # ``extract_facts`` / ``compress_history`` — banners are visual-
        # only timeline markers and would otherwise enter the LangChain
        # wire as a SystemMessage, contaminating fact extraction with
        # the literal "[测试事件]" string and biasing recent-history
        # compression. ``prompt_builder`` already filters them on the
        # /chat/send wire path; this is the symmetric read-side filter
        # for the LangChain memory ops (GH AI-review issue #9; same
        # family as L33 single-writer / L36 §7.25 fifth-layer defense).
        if m.get("source") == SOURCE_EXTERNAL_EVENT_BANNER:
            continue
        role = m.get("role")
        content = m.get("content") or ""
        if role == ROLE_USER:
            out.append(HumanMessage(content=content))
        elif role == ROLE_ASSISTANT:
            out.append(AIMessage(content=content))
        elif role == ROLE_SYSTEM:
            out.append(SystemMessage(content=content))
        # Other roles (reserved for P11/P12) skipped silently — they
        # wouldn't fit the LangChain message taxonomy anyway.
    return out


# ── dispatch table ──────────────────────────────────────────────────


# Two parallel dicts instead of a single "OpSpec" tuple so the closure
# types stay readable. ``PREVIEW_HANDLERS`` maps op id → async preview
# function; ``COMMIT_HANDLERS`` maps op id → async commit function.
# Adding an op = add one entry to each.

PreviewHandler = Any  # async fn (Session, dict) -> MemoryPreviewResult
CommitHandler = Any   # async fn (Session, dict, dict) -> dict

_PREVIEW_HANDLERS: dict[str, PreviewHandler] = {
    OP_RECENT_COMPRESS: _preview_recent_compress,
    OP_FACTS_EXTRACT: _preview_facts_extract,
    OP_REFLECT: _preview_reflect,
    OP_PERSONA_ADD_FACT: _preview_persona_add_fact,
    OP_PERSONA_RESOLVE_CORRECTIONS: _preview_persona_resolve_corrections,
}

_COMMIT_HANDLERS: dict[str, CommitHandler] = {
    OP_RECENT_COMPRESS: _commit_recent_compress,
    OP_FACTS_EXTRACT: _commit_facts_extract,
    OP_REFLECT: _commit_reflect,
    OP_PERSONA_ADD_FACT: _commit_persona_add_fact,
    OP_PERSONA_RESOLVE_CORRECTIONS: _commit_persona_resolve_corrections,
}


# ── public API ──────────────────────────────────────────────────────


def is_valid_op(op: str) -> bool:
    return op in _PREVIEW_HANDLERS


async def trigger_op(
    session: Session,
    op: str,
    params: dict[str, Any] | None = None,
) -> MemoryPreviewResult:
    """Run the preview step and cache the result on the session."""
    if op not in _PREVIEW_HANDLERS:
        raise MemoryOpError(
            "UnknownOp",
            f"未知 memory op: {op!r}; 合法值: {', '.join(ALL_OPS)}",
            status=404,
        )
    prune_expired_previews(session)
    result = await _PREVIEW_HANDLERS[op](session, params or {})
    _store_preview(session, result)
    return result


async def commit_op(
    session: Session,
    op: str,
    edits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a cached preview to disk."""
    if op not in _COMMIT_HANDLERS:
        raise MemoryOpError(
            "UnknownOp",
            f"未知 memory op: {op!r}; 合法值: {', '.join(ALL_OPS)}",
            status=404,
        )
    cached = _pop_preview(session, op)
    try:
        return await _COMMIT_HANDLERS[op](session, cached, edits or {})
    except MemoryOpError:
        # Preview already popped; don't restore — tester should re-trigger
        # on retryable errors. The alternative (re-cache on failure) lets
        # a half-applied commit silently re-commit the same payload.
        raise
    except Exception as exc:
        raise MemoryOpError(
            "CommitFailed",
            f"commit {op} 时抛出 {type(exc).__name__}: {exc}",
            status=500,
        ) from exc


def discard_op(session: Session, op: str) -> bool:
    """Drop the cached preview without writing. Returns whether one was dropped."""
    prune_expired_previews(session)
    return session.memory_previews.pop(op, None) is not None


# ── P25 r7: Prompt-only preview (不调 LLM, 不 stamp) ────────────────
#
# 设计背景
# --------
# r7 把 Chat 页 Preview Panel 改成只显示对话 AI 的 wire. 记忆域
# (``memory.llm``) 的 wire 被移到 Memory 各子页 Dry-run 按钮旁的 [预
# 览 prompt] 按钮. 但那个按钮**不应真跑 LLM** — tester 只想看"这个
# op 会发什么 prompt", 不想每次点都耗 2-10 s + token 额度.
#
# 实现选择
# --------
# 复制每个 LLM-using handler 的 "prompt 拼装" 部分到独立函数, 不调
# LLM 也不 stamp (避免污染 ``session.last_llm_wire``). 代码重复但每
# 段都 < 30 行且一目了然, 而在 handler 内部加 ``preview_wire_only``
# 分支反而让主流程变得难读. ``persona.add_fact`` 的 preview 本身不调
# LLM (只做 contradiction 评估), 所以它不参与这个路径.


@dataclass
class MemoryPromptPreview:
    """Return shape for :func:`build_memory_prompt_preview`.

    Layout matches the ``last_llm_wire`` dict so frontend rendering can
    share the "messages list + source label + note" shell:
    ``{wire_messages: list[{role, content}], op: str, note: str | None,
       params_echo: dict, warnings: list[str]}``.
    """

    op: str
    wire_messages: list[dict[str, Any]]
    note: str | None
    params_echo: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "wire_messages": list(self.wire_messages),
            "note": self.note,
            "params_echo": dict(self.params_echo),
            "warnings": list(self.warnings),
        }


async def _build_recent_compress_wire(
    session: Session,
    params: dict[str, Any],
) -> MemoryPromptPreview:
    from config.prompts_memory import (
        get_detailed_recent_history_manager_prompt,
        get_recent_history_manager_prompt,
    )
    from utils.language_utils import get_global_language

    character = _require_character(session)
    name_mapping = _build_name_mapping(session, character)

    recent_path = _memory_dir(character) / "recent.json"
    recent_dicts = _read_json_list(recent_path)
    if not recent_dicts:
        raise MemoryOpError(
            "RecentEmpty",
            "recent.json 为空, 没有可压缩的消息. 先在 Chat 页发几条消息, "
            "或在 Recent 编辑器手动填充.",
            status=409,
        )

    messages = messages_from_dict(recent_dicts)
    total = len(messages)
    max_history_length = int(params.get("max_history_length") or 10)
    default_tail = max(1, total - max_history_length + 1)
    tail_count = int(params.get("tail_count") or default_tail)
    tail_count = max(1, min(tail_count, total))
    detailed = bool(params.get("detailed") or False)
    to_compress = messages[:tail_count]

    mapping_for_prompt = dict(name_mapping)
    mapping_for_prompt["ai"] = character
    messages_text = _messages_to_wire_lines(to_compress, mapping_for_prompt)

    lang = get_global_language()
    if detailed:
        prompt = get_detailed_recent_history_manager_prompt(lang) % messages_text
    else:
        prompt = get_recent_history_manager_prompt(lang).replace("%s", messages_text)

    return MemoryPromptPreview(
        op=OP_RECENT_COMPRESS,
        wire_messages=[{"role": ROLE_USER, "content": prompt}],
        note=f"memory.recent.compress:tail={tail_count}/{total}",
        params_echo={
            "tail_count": tail_count,
            "total_before": total,
            "detailed": detailed,
        },
        warnings=[],
    )


async def _build_facts_extract_wire(
    session: Session,
    params: dict[str, Any],
) -> MemoryPromptPreview:
    from config.prompts_memory import get_fact_extraction_prompt
    from utils.language_utils import get_global_language

    character = _require_character(session)
    name_mapping = _build_name_mapping(session, character)

    source = params.get("source") or "session.messages"
    min_importance = int(params.get("min_importance") or 5)

    if source == "recent.json":
        recent_path = _memory_dir(character) / "recent.json"
        messages = messages_from_dict(_read_json_list(recent_path))
    else:
        source = "session.messages"
        messages = _session_messages_to_langchain(session)

    if not messages:
        raise MemoryOpError(
            "NoMessages",
            f"source={source!r} 没有可用的消息. 先发几条消息或切换 source.",
            status=409,
        )

    mapping_for_prompt = dict(name_mapping)
    mapping_for_prompt["ai"] = character
    master_display = name_mapping.get("human", "主人")
    conversation_text = _messages_to_wire_lines(messages, mapping_for_prompt)

    lang = get_global_language()
    prompt = get_fact_extraction_prompt(lang)
    prompt = prompt.replace("{CONVERSATION}", conversation_text)
    prompt = prompt.replace("{LANLAN_NAME}", character)
    prompt = prompt.replace("{MASTER_NAME}", master_display)

    return MemoryPromptPreview(
        op=OP_FACTS_EXTRACT,
        wire_messages=[{"role": ROLE_USER, "content": prompt}],
        note=f"memory.facts.extract:src={source}:{len(messages)}msgs",
        params_echo={
            "source": source,
            "min_importance": min_importance,
            "message_count": len(messages),
        },
        warnings=[],
    )


async def _build_reflect_wire(
    session: Session,
    params: dict[str, Any],
) -> MemoryPromptPreview:
    from config.prompts_memory import get_reflection_prompt
    from memory.reflection import MIN_FACTS_FOR_REFLECTION
    from utils.language_utils import get_global_language

    character = _require_character(session)
    name_mapping = _build_name_mapping(session, character)
    master_display = name_mapping.get("human", "主人")

    min_facts = int(params.get("min_facts") or MIN_FACTS_FOR_REFLECTION)

    facts_path = _memory_dir(character) / "facts.json"
    facts = _read_json_list(facts_path)
    unabsorbed = [
        f for f in facts
        if isinstance(f, dict) and not bool(f.get("absorbed", False))
    ]
    if len(unabsorbed) < min_facts:
        raise MemoryOpError(
            "NotEnoughFacts",
            (
                f"未吸收事实仅 {len(unabsorbed)} 条, 低于 reflect 阈值 "
                f"{min_facts}. 先跑几次 facts.extract 或手动添加事实."
            ),
            status=409,
        )

    facts_text = "\n".join(
        f"- {f.get('text', '')} (importance: {f.get('importance', 5)})"
        for f in unabsorbed
    )
    lang = get_global_language()
    prompt = get_reflection_prompt(lang)
    prompt = prompt.replace("{FACTS}", facts_text)
    prompt = prompt.replace("{LANLAN_NAME}", character)
    prompt = prompt.replace("{MASTER_NAME}", master_display)

    return MemoryPromptPreview(
        op=OP_REFLECT,
        wire_messages=[{"role": ROLE_USER, "content": prompt}],
        note=f"memory.reflect:unabsorbed={len(unabsorbed)}",
        params_echo={
            "min_facts": min_facts,
            "unabsorbed_count": len(unabsorbed),
        },
        warnings=[],
    )


async def _build_persona_resolve_corrections_wire(
    session: Session,
    params: dict[str, Any],
) -> MemoryPromptPreview:
    from config.prompts_memory import persona_correction_prompt
    from memory.persona import PersonaManager

    character = _require_character(session)
    pm = PersonaManager()
    corrections = await pm.aload_pending_corrections(character)
    if not corrections:
        raise MemoryOpError(
            "QueueEmpty",
            "persona_corrections.json 没有待裁决的矛盾. 先用 persona.add_fact "
            "加一条会触发冲突的事实.",
            status=409,
        )

    pairs = [
        (i, item) for i, item in enumerate(corrections)
        if item.get("old_text") and item.get("new_text")
    ]
    if not pairs:
        raise MemoryOpError(
            "QueueMalformed",
            "矛盾队列内所有条目都缺少 old_text / new_text, 请清理 "
            "persona_corrections.json 后重试.",
            status=500,
        )

    batch_text = "\n".join(
        f"[{i}] 已有: {item['old_text']} | 新观察: {item['new_text']}"
        for i, item in pairs
    )
    prompt = persona_correction_prompt.format(pairs=batch_text, count=len(pairs))

    return MemoryPromptPreview(
        op=OP_PERSONA_RESOLVE_CORRECTIONS,
        wire_messages=[{"role": ROLE_USER, "content": prompt}],
        note=f"memory.persona.resolve_corrections:pairs={len(pairs)}",
        params_echo={
            "queue_size": len(corrections),
            "valid_pair_count": len(pairs),
        },
        warnings=[],
    )


_PROMPT_PREVIEW_BUILDERS: dict[
    str,
    Callable[[Session, dict[str, Any]], Any],
] = {
    OP_RECENT_COMPRESS: _build_recent_compress_wire,
    OP_FACTS_EXTRACT: _build_facts_extract_wire,
    OP_REFLECT: _build_reflect_wire,
    OP_PERSONA_RESOLVE_CORRECTIONS: _build_persona_resolve_corrections_wire,
}


async def build_memory_prompt_preview(
    session: Session,
    op: str,
    params: dict[str, Any] | None = None,
) -> MemoryPromptPreview:
    """Compose the wire a given memory ``op`` **would** send to the LLM.

    Does **not** call the LLM, does **not** stamp ``session.last_llm_wire``,
    does **not** cache anything. Pure function over (session snapshot,
    params). Intended for the Memory sub-page [预览 prompt] buttons that
    appear next to the Dry-run triggers in r7.

    Raises:
        MemoryOpError: Same vocabulary as :func:`trigger_op` — e.g.
            ``RecentEmpty`` (409) if ``recent.json`` is empty,
            ``NoPromptForOp`` (422) if the op has no LLM call
            (``persona.add_fact``), ``UnknownOp`` (404).
    """
    if op not in ALL_OPS:
        raise MemoryOpError(
            "UnknownOp",
            f"未知 memory op: {op!r}; 合法值: {', '.join(ALL_OPS)}",
            status=404,
        )
    if op == OP_PERSONA_ADD_FACT:
        raise MemoryOpError(
            "NoPromptForOp",
            "persona.add_fact 的预览阶段不调用 LLM "
            "(它只做启发式矛盾评估), 没有 prompt 可预览. "
            "真正调 LLM 的 persona 操作是 persona.resolve_corrections.",
            status=422,
        )
    builder = _PROMPT_PREVIEW_BUILDERS[op]
    return await builder(session, params or {})


__all__ = [
    "ALL_OPS",
    "MEMORY_PREVIEW_TTL_SECONDS",
    "MemoryOpError",
    "MemoryPreviewResult",
    "MemoryPromptPreview",
    "OP_FACTS_EXTRACT",
    "OP_PERSONA_ADD_FACT",
    "OP_PERSONA_RESOLVE_CORRECTIONS",
    "OP_RECENT_COMPRESS",
    "OP_REFLECT",
    "OP_TO_KIND",
    "build_memory_prompt_preview",
    "commit_op",
    "discard_op",
    "is_valid_op",
    "list_previews",
    "prune_expired_previews",
    "trigger_op",
]

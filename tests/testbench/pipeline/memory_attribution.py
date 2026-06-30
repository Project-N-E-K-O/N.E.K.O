"""P27.3 — Tier C reverse attribution for the memory lineage graph.

When the tester clicks "分析来源" on a memory node (fact / reflection / persona
entry), find which conversation turns most plausibly produced it. This is
**heuristic reconstruction, never true causality**: the persisted memory JSON
has no fact->conversation link (blueprint §2 / §1.3). Every edge produced here
is therefore ``confidence="heuristic"`` (dashed) + ``relation="attributed_from"``
and is honestly labelled as a reconstruction, never drawn as a solid Tier A
edge.

Two backends
------------
* **text similarity** (default, free, deterministic): CJK-aware character-bigram
  Jaccard overlap between the memory text and each conversation turn. Pure
  Python, no deps — so the smoke can assert real scores without mocking.
* **LLM precision** (opt-in, ``use_llm=True``): asks the memory-group model to
  pick the true source turns from the text-ranked shortlist. MUST stamp
  ``record_last_llm_wire(source="memory.llm")`` *before* the call (R2 /
  ``p25_llm_call_site_stamp_coverage_smoke``); ``memory.llm`` is already in
  ``wire_tracker.KNOWN_SOURCES`` and is a memory-domain wire (does not pollute
  the Chat preview — p25_r7 partition). On any LLM failure it falls back to the
  text ranking with a warning.

Discipline
----------
Read only — never writes memory JSON (blueprint §1.3 "Tier C 归因是分析, 不写回").
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import re
from typing import Any

from utils.file_utils import robust_json_loads

from tests.testbench.chat_messages import ROLE_USER
from tests.testbench.logger import python_logger
from tests.testbench.pipeline.conversation_corpus import load_conversation_corpus
from tests.testbench.pipeline.memory_lineage import (
    LANE_MESSAGE,
    LANE_RECENT_MEMO,
    _is_memo,
    _truncate,
    build_lineage_snapshot,
)
from tests.testbench.pipeline.memory_runner import _llm_for_memory, _strip_code_fence
from tests.testbench.pipeline.wire_tracker import (
    record_last_llm_wire,
    update_last_llm_wire_reply,
)
from tests.testbench.session_store import Session

#: Node types that cannot be attribution *targets* (they ARE the source side or
#: have no conversational provenance to reconstruct).
_NON_TARGET_TYPES = frozenset({"message", "recent_memo", "correction"})

_DEFAULT_TOP_K = 6
# Batch ("推测全部源头") keeps far fewer sources per node than the single-node
# button: it fires over *every* memory at once, so top_k=6 on 200+ memories
# yields 1000+ dashed edges that turn the graph into an unreadable, unscrollable
# hairball (user perf report). 3 strong sources per memory is plenty for a "show
# me roughly where everything came from" overview.
_DEFAULT_ALL_TOP_K = 3
# Hard cap on the number of dashed edges a single batch may emit, so an enormous
# imported character can never return a multi-thousand-edge payload. When hit we
# stop and flag ``capped`` + a warning (the per-node button still drills down).
_MAX_ALL_EDGES = 400
# Overlap-coefficient threshold (see _overlap). A memory is usually a *short*
# distillation of a *long* conversation turn, so plain Jaccard (inter/union)
# under-scores it badly (the long turn inflates the union). We score with the
# containment-biased overlap coefficient instead, which needs a higher floor.
_DEFAULT_MIN_SCORE = 0.2

#: Cap how much conversation we scan + how much we hand the LLM, so a heavy
#: imported db can't blow up latency / token cost.
_MAX_TURNS_SCANNED = 4000
_LLM_PROMPT_PREVIEW = 200


class AttributionError(Exception):
    """Raised for caller-facing attribution failures (mapped to HTTP)."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _normalize(text: str) -> str:
    """Lowercase + drop everything that isn't a word char or CJK ideograph."""
    s = str(text or "").lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s)


def _shingles(text: str) -> set[str]:
    s = _normalize(text)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(sa: set[str], sb: set[str]) -> float:
    """Containment-biased overlap coefficient of two shingle sets; 0..1.

    ``inter / min(|sa|, |sb|)`` instead of Jaccard's ``inter / union``. A memory
    ("主人喜欢深夜写代码") that is fully contained in a longer conversation turn
    scores ~1.0 here, whereas Jaccard would dilute it toward 0 because the long
    turn dominates the union. This is exactly the "short distillation of a long
    source" shape Tier C is trying to surface, so dashed edges actually appear
    for real characters (user report: 一条虚线都看不到). Deterministic.
    """  # noqa: DOCSTRING_CJK
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    if not inter:
        return 0.0
    smaller = min(len(sa), len(sb))
    return inter / smaller if smaller else 0.0


def _similarity(a: str, b: str) -> float:
    """Overlap coefficient of character bigrams; 0..1, deterministic."""
    return _overlap(_shingles(a), _shingles(b))


def _turn_to_node(turn: dict[str, Any]) -> dict[str, Any]:
    """Project a corpus turn into the same node shape the aggregator emits.

    Lets the frontend inject any candidate the node-budget had trimmed so the
    dashed edge always lands on a real node.
    """
    role = turn.get("role", "other")
    content = str(turn.get("content") or "")
    is_memo = _is_memo(role, content)
    return {
        "id": turn["id"],
        "type": "recent_memo" if is_memo else "message",
        "lane": LANE_RECENT_MEMO if is_memo else LANE_MESSAGE,
        "label": _truncate(content),
        "status": role,
        "entity": None,
        "created_at": turn.get("ts"),
        "meta": {
            "content": content,
            "role": role,
            "session_id": turn.get("session_id"),
            "origin": turn.get("origin"),
        },
        "warnings": [],
    }


async def _llm_refine(
    session: Session,
    target_id: str,
    target_text: str,
    shortlist: list[tuple[float, dict[str, Any]]],
) -> list[tuple[float, dict[str, Any]]]:
    """Ask the memory model which shortlisted turns are real sources.

    Returns a list of ``(confidence, turn)``. A *valid empty* list (the model
    replied with a parseable ``[]``) means "none of the shortlist is a source"
    — an honest answer we preserve as ``method="llm"``. But an **empty or
    unparseable** reply (no content, or not a JSON array) is a failure, not an
    honest "no": we raise so the caller degrades to text similarity *visibly*
    via the ``llm_fallback`` signal, instead of silently reporting an LLM
    success with zero edges. Stamps the wire BEFORE invoking (R2).
    """
    lines = []
    for i, (_score, t) in enumerate(shortlist):
        preview = str(t.get("content") or "")[:_LLM_PROMPT_PREVIEW]
        lines.append(f"[{i}] ({t.get('role')}) {preview}")
    prompt = (
        "你是记忆溯源助手. 下面是角色的一条记忆, 以及若干候选历史对话片段.\n"
        "判断哪些对话片段可能是这条记忆的来源 (这条记忆很可能由这些对话归纳/抽取而来).\n\n"
        f"记忆内容: {target_text}\n\n"
        "候选对话:\n" + "\n".join(lines) + "\n\n"
        "只输出一个 JSON 数组, 每项形如 {\"index\": <候选编号整数>, "
        "\"confidence\": <0到1之间小数>}; 仅列出确有来源关系的候选, 没有就输出 []. "
        "不要输出任何解释文字."
    )
    wire = [{"role": ROLE_USER, "content": prompt}]
    try:
        record_last_llm_wire(
            session, wire, source="memory.llm",
            note=f"memory.lineage.attribute:{target_id}",
        )
    except Exception as exc:  # noqa: BLE001 — observability must not block LLM
        python_logger().debug(
            "memory.lineage.attribute: record_last_llm_wire failed: %s: %s",
            type(exc).__name__, exc,
        )

    llm = _llm_for_memory(session, temperature=0.0)
    raw = ""
    try:
        resp = await llm.ainvoke(prompt)
        raw = _strip_code_fence(getattr(resp, "content", "") or "")
    finally:
        try:
            await llm.aclose()
        except Exception:  # noqa: BLE001
            pass
    try:
        update_last_llm_wire_reply(session, reply_chars=len(raw))
    except Exception:  # noqa: BLE001
        pass

    if not raw.strip():
        raise AttributionError(
            "LLMUnparsable", "LLM 精判返回了空回复 (无内容).", status=502)
    parsed = robust_json_loads(raw)
    if not isinstance(parsed, list):
        preview = raw.strip().replace("\n", " ")[:120]
        raise AttributionError(
            "LLMUnparsable",
            f"LLM 精判回复不是预期的 JSON 数组, 无法解析: {preview!r}", status=502)
    selected: list[tuple[float, dict[str, Any]]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(shortlist):
            continue
        conf = item.get("confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = shortlist[idx][0]
        selected.append((max(0.0, min(1.0, conf)), shortlist[idx][1]))
    return selected


async def attribute_node(
    session: Session,
    character: str,
    node_id: str,
    *,
    use_llm: bool = False,
    top_k: int = _DEFAULT_TOP_K,
    min_score: float = _DEFAULT_MIN_SCORE,
) -> dict[str, Any]:
    """Reverse-attribute a memory node to conversation turns (Tier C).

    Returns ``{node_id, method, target_preview, candidates, edges, warnings}``
    where ``candidates`` are full lineage-node dicts (frontend may inject them)
    and ``edges`` are dashed ``attributed_from`` / ``heuristic`` edges.
    """
    node_id = str(node_id or "").strip()
    if not node_id:
        raise AttributionError("MissingNodeId", "node_id 不能为空.", status=422)
    top_k = max(1, min(int(top_k or _DEFAULT_TOP_K), 20))
    warnings: list[str] = []

    # Locate the target node (no budget cap — we must find any node).
    snap = build_lineage_snapshot(character, node_budget=10 ** 9)
    target = next((n for n in snap["nodes"] if n["id"] == node_id), None)
    if target is None:
        raise AttributionError(
            "UnknownNode", f"未找到节点 {node_id!r}.", status=404)
    if target["type"] in _NON_TARGET_TYPES:
        raise AttributionError(
            "NotAttributable",
            "对话 / recent 摘要 / 矛盾节点不支持反向归因; 请选择 事实 / 反思 / 人设 记忆节点.",
            status=409,
        )
    tmeta = target.get("meta") or {}
    target_text = str(
        tmeta.get("text") or tmeta.get("content") or target.get("label") or "")
    if not target_text.strip():
        raise AttributionError(
            "EmptyTarget", "目标记忆没有可用于比对的文本.", status=409)

    # Cap at the DB layer instead of loading the whole table then slicing: the
    # text-similarity scan never looks past _MAX_TURNS_SCANNED turns anyway.
    corpus = load_conversation_corpus(character, limit_rows=_MAX_TURNS_SCANNED)
    warnings.extend(corpus.get("warnings") or [])
    turns = [
        t for t in (corpus.get("turns") or [])
        if str(t.get("content") or "").strip()
    ][:_MAX_TURNS_SCANNED]

    if not turns:
        warnings.append(
            "该角色无对话语料 (无 time_indexed.db / recent.json), 无法反向归因. "
            "从真实角色导入后可获得对话级溯源.")
        return {
            "node_id": node_id,
            "method": "text",
            "llm_fallback": None,
            "target_preview": _truncate(target_text),
            "candidates": [],
            "edges": [],
            "warnings": warnings,
        }

    scored: list[tuple[float, dict[str, Any]]] = []
    for t in turns:
        score = _similarity(target_text, str(t.get("content") or ""))
        if score >= min_score:
            scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    shortlist = scored[:top_k]

    method = "text"
    chosen = shortlist
    # When an LLM refine is requested but degrades, we do NOT stay silent: a
    # structured ``llm_fallback`` signal travels back so the UI can persistently
    # tell the user it fell back to text similarity + a concise reason (not just
    # a transient toast). Mirrors the cluster-label / overview LLM fallbacks.
    llm_fallback: dict[str, Any] | None = None
    if use_llm and shortlist:
        try:
            chosen = await _llm_refine(session, node_id, target_text, shortlist)
            method = "llm"
        except Exception as exc:  # noqa: BLE001 — degrade, never 500 the click
            reason = str(exc).strip() or type(exc).__name__
            warnings.append(f"LLM 精判失败, 回退文本相似度: {reason}")
            llm_fallback = {"requested": "llm", "used": "text", "reason": reason}
            chosen = shortlist
            method = "text"

    candidates: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()
    for score, t in chosen:
        node = _turn_to_node(t)
        if node["id"] not in seen:
            candidates.append(node)
            seen.add(node["id"])
        edges.append({
            "source": node["id"],
            "target": node_id,
            "relation": "attributed_from",
            "confidence": "heuristic",
            "score": round(float(score), 4),
            "note": None,
        })

    return {
        "node_id": node_id,
        "method": method,
        "llm_fallback": llm_fallback,
        "target_preview": _truncate(target_text),
        "candidates": candidates,
        "edges": edges,
        "warnings": warnings,
    }


#: Node types that ARE valid attribution targets (the structural memory side).
_TARGET_TYPES = frozenset({"fact", "reflection", "persona_entry"})


def attribute_all_text(
    character: str,
    *,
    top_k: int = _DEFAULT_ALL_TOP_K,
    min_score: float = _DEFAULT_MIN_SCORE,
    max_edges: int = _MAX_ALL_EDGES,
) -> dict[str, Any]:
    """One-shot text-similarity Tier C for *every* structural memory node.

    The per-node :func:`attribute_node` rebuilds the snapshot and reloads the
    corpus on each call; doing that 20-30 times for a "show me everything"
    button is wasteful. This batch variant loads the snapshot + corpus once,
    pre-computes each turn's shingle set once, then scores every fact /
    reflection / persona entry against them. Text only (deterministic, free) —
    the LLM precision pass stays per-node/opt-in.

    Returns ``{method, candidates, edges, warnings, attributed_nodes,
    target_total}``. ``edges`` are all dashed ``heuristic`` /
    ``attributed_from`` edges; ``candidates`` are the deduped conversation
    nodes they point at (frontend injects any the budget trimmed). Never
    raises for absent/empty data — only soft warnings.
    """
    top_k = max(1, min(int(top_k or _DEFAULT_ALL_TOP_K), 20))
    warnings: list[str] = []

    snap = build_lineage_snapshot(character, node_budget=10 ** 9)
    targets = [n for n in snap["nodes"] if n.get("type") in _TARGET_TYPES]

    # Cap at the DB layer instead of loading the whole table then slicing: the
    # text-similarity scan never looks past _MAX_TURNS_SCANNED turns anyway.
    corpus = load_conversation_corpus(character, limit_rows=_MAX_TURNS_SCANNED)
    warnings.extend(corpus.get("warnings") or [])
    turns = [
        t for t in (corpus.get("turns") or [])
        if str(t.get("content") or "").strip()
    ][:_MAX_TURNS_SCANNED]

    if not turns:
        warnings.append(
            "该角色无对话语料 (无 time_indexed.db / recent.json), 无法反向归因. "
            "从真实角色导入后可获得对话级溯源.")
        return {
            "method": "text",
            "candidates": [],
            "edges": [],
            "warnings": warnings,
            "attributed_nodes": 0,
            "target_total": len(targets),
        }

    # Pre-compute each turn's shingle set once (reused across all targets).
    turn_shingles: list[tuple[set[str], dict[str, Any]]] = [
        (_shingles(str(t.get("content") or "")), t) for t in turns
    ]

    candidates: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    attributed_nodes = 0
    capped = False

    max_edges = max(1, int(max_edges or _MAX_ALL_EDGES))
    for node in targets:
        if len(edges) >= max_edges:
            capped = True
            break
        nmeta = node.get("meta") or {}
        target_text = str(
            nmeta.get("text") or nmeta.get("content") or node.get("label") or "")
        ts = _shingles(target_text)
        if not ts:
            continue
        scored: list[tuple[float, dict[str, Any]]] = []
        for sh, t in turn_shingles:
            if not sh:
                continue
            score = _overlap(ts, sh)
            if score >= min_score:
                scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        shortlist = scored[:top_k]
        if shortlist:
            attributed_nodes += 1
        for score, t in shortlist:
            cnode = _turn_to_node(t)
            if cnode["id"] not in seen_nodes:
                candidates.append(cnode)
                seen_nodes.add(cnode["id"])
            edges.append({
                "source": cnode["id"],
                "target": node["id"],
                "relation": "attributed_from",
                "confidence": "heuristic",
                "score": round(float(score), 4),
                "note": None,
            })

    if capped:
        warnings.append(
            f"启发式来源连线过多, 已截断到 {max_edges} 条 (每条记忆最多取 {top_k} 条"
            f"最相似来源). 想看某条记忆的完整来源, 选中它再点「分析来源」.")

    return {
        "method": "text",
        "candidates": candidates,
        "edges": edges,
        "warnings": warnings,
        "attributed_nodes": attributed_nodes,
        "target_total": len(targets),
        "capped": capped,
    }

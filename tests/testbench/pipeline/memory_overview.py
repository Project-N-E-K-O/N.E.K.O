"""P29.1 — read-only memory **system overview** aggregator (chokepoint of chokepoints).

Builds the dashboard payload behind the "系统概况" sub-page of the 记忆系统分析
workspace. This module performs **no raw reads of its own** beyond what the two
downstream chokepoints already do: it calls :func:`memory_lineage.build_lineage_snapshot`
(P27, structural truth) and :func:`embedding_space._build_space` (P28, vector
space) **once each**, then derives every overview card / finding from those two
results. The frontend renders the returned numbers / finding codes verbatim and
never re-derives them (blueprint §3.1 / LESSONS_LEARNED §7.25).

Design rails (blueprint P29):
  * **Read-only.** No writes, no model load (the rules pass). The optional LLM
    layer (AI report + contradiction NLI) lives in the ``*_llm`` coroutines and
    only runs under ``session_operation`` from the router.
  * **Honest contradiction tiering** (blueprint §6): only L0 (recorded on disk:
    corrections / denied reflections / suppressed persona) is labelled a real
    contradiction. L1 (same-topic high-similarity pairs) is surfaced as
    "待核对候选" — a retrieval, never a judgement. L2 is the LLM endpoint.
  * **Transparency over a black-box score** (blueprint §1.2): no single 0-100
    health number. Instead "N 项需关注" + per-finding severity + an explicit
    *conclusion credibility* card stating what data was / wasn't available.
  * **Functional-stage tagging** (blueprint §4.0): every finding carries a
    ``stage`` (extract/dedup/reflect/promote/correct/embed/structure) so the UI
    can group by pipeline phase, not just by problem type.
  * **i18n discipline**: findings carry a stable ``code`` + numeric ``data``;
    the frontend owns all human text. (Chinese only appears in passthrough
    ``warnings`` that originate from the downstream builders.)

Gating (blueprint §4.3): a character with 0 embedded entries still gets a full,
honest payload — structural findings (D/F/H/L0 contradiction) run regardless;
vector findings (A/B2/C/E.split/G) are marked unavailable with guidance rather
than silently returning empty.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from tests.testbench.pipeline.embedding_space import (
    _build_space,
    build_bridges,
    build_clusters,
    build_duplicates,
)
from tests.testbench.pipeline.memory_lineage import build_lineage_snapshot

# ── Tunables (blueprint §10). Module constants (not config.py) to avoid an
# import cycle with the pipeline package; tune in one place. ──────────────────

#: Cosine ≥ this ⇒ two entries are "redundant" (near-duplicate) for A1/A3.
#: Looser than embedding_space.DUP_THRESHOLD_DEFAULT (0.95) because the overview
#: wants to *surface candidates to review*, not assert identity.
OVERVIEW_DUP_THRESHOLD = 0.92
#: ≥ this many near-dup pairs ⇒ A1 escalates to "bad".
A1_DUP_PAIRS_BAD = 20
#: Redundancy ratio (redundant items / embedded) thresholds for A3.
A3_REDUNDANCY_WARN = 0.10
A3_REDUNDANCY_BAD = 0.25
#: A cluster is a "near-dup cluster" (A2) when its members' mean cosine to the
#: cluster centroid is ≥ this (very tight = likely repeats of one idea).
A2_TIGHT_CENTROID_SIM = 0.93

#: B2 "same-topic, may conflict" candidate band: same entity + cosine in
#: [low, high). Above ``high`` it's a duplicate (A), not a conflict candidate.
B2_CANDIDATE_LOW = 0.55
B2_CANDIDATE_HIGH = 0.92
#: Chinese negation cues — a weak polarity hint used ONLY to sort B2 candidates
#: (a cue present in one side but not the other). Never a contradiction verdict.
NEGATION_CUES = ("不", "没", "无", "非", "别", "拒绝", "讨厌", "不再", "不是", "不会")

#: Promotion fidelity (G1): a promoted persona entry whose cosine to its source
#: reflection is < this has semantically drifted during promotion.
FIDELITY_DRIFT_WARN = 0.80

#: Pipeline (F) thresholds.
PROMOTE_RATE_WARN = 0.10        # promoted/merged ÷ reflections below this ⇒ stall
PROMOTE_RATE_MIN_REFLECTIONS = 5  # don't judge promote rate on a tiny corpus
REJECT_RATE_WARN = 0.50         # denied ÷ reflections at/above this ⇒ quality issue
PENDING_AGE_DAYS = 14           # pending/confirmed older than this ⇒ backlog

#: Retention (H) thresholds.
HIGH_IMPORTANCE = 7             # fact importance ≥ this is "high"
LOW_QUALITY_TEXT_LEN = 6        # fact text shorter than this ⇒ low quality
ZOMBIE_AGE_DAYS = 30           # unabsorbed + unreferenced + older than this

#: How many example items to attach per finding (UI shows a few, links to detail).
MAX_EXAMPLES = 5

#: Severity ordering for sorting the findings list (worst first).
_SEVERITY_RANK = {"bad": 0, "warn": 1, "info": 2}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _age_days(created_at: Any, *, now: datetime | None = None) -> float | None:
    """Best-effort age in days from an ISO8601 string or epoch number.

    Returns ``None`` when unparseable (age-based findings then skip the item
    rather than guess — honest partial coverage, blueprint §3.1.1).
    """
    if created_at is None or created_at == "":
        return None
    now = now or _now_utc()
    dt: datetime | None = None
    if isinstance(created_at, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(created_at), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        s = str(created_at).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    return delta.total_seconds() / 86400.0


def _ratio(numerator: int, denominator: int) -> float | None:
    """Guarded ratio — ``None`` when the denominator is 0 (UI shows '—')."""
    if not denominator:
        return None
    return numerator / denominator


def _finding(
    code: str,
    category: str,
    stage: str,
    severity: str,
    *,
    count: int = 0,
    data: dict[str, Any] | None = None,
    drill: dict[str, Any] | None = None,
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "stage": stage,
        "severity": severity,
        "count": int(count),
        "data": data or {},
        "drill": drill,
        "examples": (examples or [])[:MAX_EXAMPLES],
    }


def _union_components(pairs: list[tuple[str, str]]) -> list[set[str]]:
    """Connected components over an undirected edge list (for redundancy cost)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        union(a, b)
    comps: dict[str, set[str]] = {}
    for node in list(parent):
        comps.setdefault(find(node), set()).add(node)
    return [c for c in comps.values() if len(c) >= 2]


def _has_negation(text: str) -> bool:
    t = text or ""
    return any(cue in t for cue in NEGATION_CUES)


# ── the main rules-pass builder ──────────────────────────────────────────────


def build_overview(character: str) -> dict[str, Any]:
    """``GET /api/memory/overview`` — the read-only system overview dashboard.

    Calls the two downstream chokepoints once each, then derives cards +
    findings. Never raises for absent/malformed memory (soft-degrades).
    """
    character = str(character or "").strip()
    lin = build_lineage_snapshot(character)
    space = _build_space(character)

    nodes = lin.get("nodes", [])
    edges = lin.get("edges", [])
    lmeta = lin.get("meta", {})
    counts = lmeta.get("counts", {})

    facts = [n for n in nodes if n.get("type") == "fact"]
    refls = [n for n in nodes if n.get("type") == "reflection"]
    personas = [n for n in nodes if n.get("type") == "persona_entry"]
    corrections = [n for n in nodes if n.get("type") == "correction"]

    health = space.get("health", {})
    matrix = space.get("_matrix")
    has_emb = matrix is not None
    embedded = int(health.get("embedded", 0))
    total_entries = int(health.get("total", 0))

    warnings: list[str] = []
    warnings += list(lmeta.get("file_warnings") or [])
    warnings += list(lmeta.get("corpus_warnings") or [])
    warnings += list(space.get("warnings") or [])

    findings: list[dict[str, Any]] = []

    # Reuse the vector views ONCE (inject the prebuilt space — blueprint §3.1.1).
    dup = build_duplicates(
        character, threshold=OVERVIEW_DUP_THRESHOLD, space=space,
    ) if has_emb else None
    clusters = build_clusters(character, space=space) if has_emb else None
    bridges = build_bridges(character, space=space) if has_emb else None

    # ── A. Redundancy & duplication (stage=dedup, needs embeddings) ──────────
    if has_emb and dup is not None:
        pairs = dup.get("pairs", [])
        if pairs:
            findings.append(_finding(
                "A1", "redundancy", "dedup",
                "bad" if len(pairs) >= A1_DUP_PAIRS_BAD else "warn",
                count=len(pairs),
                data={"threshold": OVERVIEW_DUP_THRESHOLD, "capped": dup.get("capped", False)},
                drill={"page": "embedding",
                       "opts": {"mode": "duplicates", "threshold": OVERVIEW_DUP_THRESHOLD}},
                examples=[{"a": p["a"], "b": p["b"], "score": p["score"],
                           "a_label": p.get("a_label", ""), "b_label": p.get("b_label", "")}
                          for p in pairs[:MAX_EXAMPLES]],
            ))
            # A3 redundancy cost = items that could be merged away.
            comps = _union_components([(p["a"], p["b"]) for p in pairs])
            redundant = sum(len(c) - 1 for c in comps)
            ratio = _ratio(redundant, embedded)
            if ratio is not None and ratio >= A3_REDUNDANCY_WARN:
                findings.append(_finding(
                    "A3", "redundancy", "dedup",
                    "bad" if ratio >= A3_REDUNDANCY_BAD else "warn",
                    count=redundant,
                    data={"redundant": redundant, "embedded": embedded,
                          "ratio": round(ratio, 4), "groups": len(comps)},
                    drill={"page": "embedding",
                           "opts": {"mode": "duplicates", "threshold": OVERVIEW_DUP_THRESHOLD}},
                ))
    if has_emb and clusters is not None:
        tight = [c for c in clusters.get("clusters", [])
                 if c.get("size", 0) >= 3 and _cluster_is_tight(c, space)]
        if tight:
            findings.append(_finding(
                "A2", "redundancy", "dedup", "warn",
                count=len(tight),
                data={"n_clusters": clusters.get("n_clusters", 0)},
                drill={"page": "embedding",
                       "opts": {"mode": "scatter", "cluster": True}},
                examples=[{"cluster": c["cluster"], "size": c["size"],
                           "label": c.get("label", "")} for c in tight[:MAX_EXAMPLES]],
            ))

    # ── B. Contradiction (stage=correct) ─────────────────────────────────────
    # L0 — recorded on disk = real contradiction signals.
    denied = [r for r in refls if (r.get("status") or "") in ("denied", "promote_blocked")]
    suppressed = [p for p in personas if p.get("status") == "suppressed"]
    l0_total = len(corrections) + len(denied) + len(suppressed)
    if l0_total:
        findings.append(_finding(
            "B1", "contradiction", "correct",
            "warn",
            count=l0_total,
            data={"corrections": len(corrections), "denied": len(denied),
                  "suppressed": len(suppressed)},
            drill=({"page": "lineage", "opts": {"focusNodeId": corrections[0]["id"]}}
                   if corrections else None),
            examples=[{"id": c["id"], "old_text": (c.get("meta") or {}).get("old_text", ""),
                       "new_text": (c.get("meta") or {}).get("new_text", "")}
                      for c in corrections[:MAX_EXAMPLES]],
        ))
    # N1 — unresolved correction: a `corrects` edge whose target persona entry is
    # still active (the disputed old_text still lives in persona).
    persona_status = {p["id"]: p.get("status") for p in personas}
    unresolved = []
    for e in edges:
        if e.get("relation") != "corrects":
            continue
        tgt = e.get("target")
        if persona_status.get(tgt) == "active":
            unresolved.append({"correction": e.get("source"), "persona": tgt})
    if unresolved:
        findings.append(_finding(
            "N1", "contradiction", "correct", "bad",
            count=len(unresolved),
            data={},
            drill={"page": "lineage", "opts": {"focusNodeId": unresolved[0]["persona"]}},
            examples=unresolved[:MAX_EXAMPLES],
        ))
    # B2 — L1 candidate retrieval: same-entity, similar-but-not-identical pairs
    # (a *retrieval*, sorted by a weak negation-polarity hint). NOT a verdict.
    if has_emb and dup is not None:
        cand = _b2_candidates(character, space)
        if cand:
            findings.append(_finding(
                "B2", "contradiction", "correct", "info",
                count=len(cand),
                data={"low": B2_CANDIDATE_LOW, "high": B2_CANDIDATE_HIGH},
                drill={"page": "embedding", "opts": {"mode": "matrix"}},
                examples=cand[:MAX_EXAMPLES],
            ))

    # ── C. Attribution fidelity (stage=reflect, needs embeddings) ────────────
    if has_emb and bridges is not None:
        rows = bridges.get("rows", [])
        miss = [r for r in rows if r.get("missing_in_declared")]
        extra = [r for r in rows if r.get("extra_in_declared")]
        if miss:
            findings.append(_finding(
                "C1", "attribution", "reflect", "warn",
                count=len(miss),
                data={},
                drill={"page": "embedding", "opts": {"mode": "bridges"}},
                examples=[{"reflection_id": r["reflection_id"],
                           "label": r.get("reflection_label", "")} for r in miss[:MAX_EXAMPLES]],
            ))
        if extra:
            findings.append(_finding(
                "C2", "attribution", "reflect", "info",
                count=len(extra),
                data={},
                drill={"page": "embedding", "opts": {"mode": "bridges"}},
                examples=[{"reflection_id": r["reflection_id"],
                           "label": r.get("reflection_label", "")} for r in extra[:MAX_EXAMPLES]],
            ))

    # ── D. Structural orphans (stage=structure, no embeddings needed) ────────
    fact_ids = {f["id"] for f in facts}
    refl_orphans = [r for r in refls
                    if not [fid for fid in ((r.get("meta") or {}).get("source_fact_ids") or [])
                            if fid in fact_ids]]
    if refl_orphans:
        findings.append(_finding(
            "D1", "structure", "structure", "warn",
            count=len(refl_orphans),
            data={},
            drill={"page": "lineage", "opts": {"focusNodeId": refl_orphans[0]["id"]}},
            examples=[{"id": r["id"], "label": r.get("label", "")}
                      for r in refl_orphans[:MAX_EXAMPLES]],
        ))
    # D2 reuses the lineage chokepoint's OWN source edges (promoted_from /
    # merged_from) instead of re-deriving orphan-ness from ``meta.source_id``.
    # A promoted-by-merge persona's single ``source_id`` is consumed during the
    # merge (no longer a standalone reflection node), yet its real sources live
    # in ``merged_from_ids`` and ARE drawn as ``merged_from`` edges. Checking
    # only ``source_id`` falsely flagged such merged entries as "no source"
    # while the溯源图 clearly shows their sources (blueprint §3.1 / LESSONS
    # §7.25: don't recompute a relation the chokepoint already settled).
    linked_persona_ids = {
        e.get("target") for e in edges
        if e.get("relation") in ("promoted_from", "merged_from")
    }
    persona_orphans = [
        p for p in personas
        if (p.get("meta") or {}).get("source") == "reflection"
        and p["id"] not in linked_persona_ids
    ]
    if persona_orphans:
        findings.append(_finding(
            "D2", "structure", "structure", "warn",
            count=len(persona_orphans),
            data={},
            drill={"page": "lineage", "opts": {"focusNodeId": persona_orphans[0]["id"]}},
            examples=[{"id": p["id"], "label": p.get("label", "")}
                      for p in persona_orphans[:MAX_EXAMPLES]],
        ))
    referenced_fact_ids: set[str] = set()
    for r in refls:
        for fid in ((r.get("meta") or {}).get("source_fact_ids") or []):
            referenced_fact_ids.add(fid)
    dangling = [f for f in facts
                if f["id"] not in referenced_fact_ids and not (f.get("meta") or {}).get("absorbed")]
    if dangling:
        findings.append(_finding(
            "D3", "structure", "structure", "info",
            count=len(dangling),
            data={"total_facts": len(facts)},
            drill={"page": "lineage", "opts": {"focusNodeId": dangling[0]["id"]}},
            examples=[{"id": f["id"], "label": f.get("label", "")}
                      for f in dangling[:MAX_EXAMPLES]],
        ))
    # D4 — dangling source references: a reflection's declared ``source_fact_ids``
    # cite facts that no longer exist (hard-deleted, not merely ``absorbed`` —
    # absorbed facts are kept with a flag). A referential-integrity problem:
    # deleting a fact should also clean up reflections that reference it, so a
    # later node never points at a vanished one. Distinct from D1 ("no valid
    # source at all"); D4 also catches the partial case where only SOME declared
    # sources were deleted. Purely structural — runs without embeddings.
    dangling_refl: list[dict[str, Any]] = []
    total_dangling_refs = 0
    for r in refls:
        declared = [str(x) for x in ((r.get("meta") or {}).get("source_fact_ids") or []) if x]
        gone = [fid for fid in declared if fid not in fact_ids]
        if gone:
            total_dangling_refs += len(gone)
            dangling_refl.append({
                "id": r["id"], "label": r.get("label", ""),
                "missing": gone[:MAX_EXAMPLES], "missing_count": len(gone),
                "declared_count": len(declared),
            })
    if dangling_refl:
        findings.append(_finding(
            "D4", "structure", "structure",
            "bad" if total_dangling_refs >= 10 else "warn",
            count=len(dangling_refl),
            data={"reflections": len(dangling_refl),
                  "dangling_refs": total_dangling_refs},
            drill={"page": "embedding", "opts": {"mode": "bridges"}},
            examples=[{"id": d["id"], "label": d["label"],
                       "missing_count": d["missing_count"]}
                      for d in dangling_refl[:MAX_EXAMPLES]],
        ))

    # ── E. Embedding health (stage=embed) ────────────────────────────────────
    missing = int(health.get("missing", 0))
    stale = int(health.get("stale", 0))
    corrupt = int(health.get("corrupt", 0))
    other_space = int(health.get("other_space_count", 0))
    if missing:
        mr = _ratio(missing, total_entries) or 0.0
        findings.append(_finding(
            "E1", "embedding", "embed",
            "warn" if mr >= 0.5 else "info",
            count=missing,
            data={"total": total_entries, "ratio": round(mr, 4)},
        ))
    if stale:
        findings.append(_finding(
            "E2", "embedding", "embed", "warn", count=stale, data={}))
    if corrupt:
        findings.append(_finding(
            "E3", "embedding", "embed", "bad", count=corrupt, data={}))
    if other_space:
        findings.append(_finding(
            "E4", "embedding", "embed", "warn",
            count=other_space,
            data={"primary_dim": health.get("primary_dim"),
                  "dims_present": health.get("dims_present", {})},
        ))

    # ── F. Pipeline throughput & stalls (stage varies) ───────────────────────
    total_refl = len(refls)
    promoted = [r for r in refls if (r.get("status") or "") in ("promoted", "merged")]
    denied_n = len(denied)
    promote_rate = _ratio(len(promoted), total_refl)
    reject_rate = _ratio(denied_n, total_refl)
    if (promote_rate is not None and promote_rate < PROMOTE_RATE_WARN
            and total_refl >= PROMOTE_RATE_MIN_REFLECTIONS):
        findings.append(_finding(
            "F2", "pipeline", "promote", "warn",
            count=len(promoted),
            data={"promoted": len(promoted), "reflections": total_refl,
                  "rate": round(promote_rate, 4)},
            drill={"page": "lineage", "opts": {}},
        ))
    if reject_rate is not None and reject_rate >= REJECT_RATE_WARN:
        findings.append(_finding(
            "F4", "pipeline", "reflect", "warn",
            count=denied_n,
            data={"denied": denied_n, "reflections": total_refl,
                  "rate": round(reject_rate, 4)},
        ))
    pending = [r for r in refls if (r.get("status") or "") in ("pending", "confirmed")]
    pending_old = [r for r in pending
                   if (lambda a: a is not None and a >= PENDING_AGE_DAYS)(
                       _age_days(r.get("created_at")))]
    if pending_old:
        findings.append(_finding(
            "F3", "pipeline", "reflect", "warn",
            count=len(pending_old),
            data={"pending": len(pending), "age_days": PENDING_AGE_DAYS},
            drill={"page": "lineage", "opts": {"focusNodeId": pending_old[0]["id"]}},
            examples=[{"id": r["id"], "label": r.get("label", "")}
                      for r in pending_old[:MAX_EXAMPLES]],
        ))
    # F5 extract yield uses the TRUE conversation turn count (lineage messages
    # are node-budget-truncated — blueprint §3.1.1). Derive it from meta.
    structural = len(facts) + total_refl + len(personas) + len(corrections)
    convo_total = max(0, int((lmeta.get("node_budget", {}) or {}).get("total", 0)) - structural)
    extract_yield = _ratio(len(facts), convo_total)

    # ── G. Promotion fidelity (stage=promote, needs embeddings) ──────────────
    fidelity_unverifiable = 0
    if has_emb:
        drift = _promotion_drift(personas, space)
        fidelity_unverifiable = drift["unverifiable"]
        if drift["drifted"]:
            findings.append(_finding(
                "G1", "fidelity", "promote", "warn",
                count=len(drift["drifted"]),
                data={"threshold": FIDELITY_DRIFT_WARN,
                      "unverifiable": drift["unverifiable"]},
                drill={"page": "lineage",
                       "opts": {"focusNodeId": drift["drifted"][0]["persona"]}},
                examples=drift["drifted"][:MAX_EXAMPLES],
            ))

    # ── H. Retention quality (stage=extract/structure) ───────────────────────
    high_imp_unused = [
        f for f in facts
        if isinstance((f.get("meta") or {}).get("importance"), (int, float))
        and (f.get("meta") or {}).get("importance") >= HIGH_IMPORTANCE
        and f["id"] not in referenced_fact_ids
    ]
    if high_imp_unused:
        findings.append(_finding(
            "H1", "retention", "extract", "info",
            count=len(high_imp_unused),
            data={"importance": HIGH_IMPORTANCE},
            drill={"page": "lineage", "opts": {"focusNodeId": high_imp_unused[0]["id"]}},
            examples=[{"id": f["id"], "label": f.get("label", "")}
                      for f in high_imp_unused[:MAX_EXAMPLES]],
        ))
    low_quality = [
        f for f in facts
        if len((f.get("meta") or {}).get("text", "").strip()) < LOW_QUALITY_TEXT_LEN
        or not f.get("entity")
    ]
    if low_quality:
        findings.append(_finding(
            "H2", "retention", "extract", "info",
            count=len(low_quality),
            data={"min_len": LOW_QUALITY_TEXT_LEN},
            examples=[{"id": f["id"], "label": f.get("label", "")}
                      for f in low_quality[:MAX_EXAMPLES]],
        ))
    zombie = []
    for f in facts:
        if f["id"] in referenced_fact_ids or (f.get("meta") or {}).get("absorbed"):
            continue
        age = _age_days(f.get("created_at"))
        if age is not None and age >= ZOMBIE_AGE_DAYS:
            zombie.append(f)
    if zombie:
        findings.append(_finding(
            "H3", "retention", "structure", "warn",
            count=len(zombie),
            data={"age_days": ZOMBIE_AGE_DAYS},
            drill={"page": "lineage", "opts": {"focusNodeId": zombie[0]["id"]}},
            examples=[{"id": f["id"], "label": f.get("label", "")}
                      for f in zombie[:MAX_EXAMPLES]],
        ))

    findings.sort(key=lambda f: (_SEVERITY_RANK.get(f["severity"], 9), f["code"]))
    attention_count = sum(1 for f in findings if f["severity"] in ("bad", "warn"))

    cards = {
        "composition": {
            "messages": counts.get("messages", 0),
            "recent_memos": counts.get("recent_memos", 0),
            "facts": len(facts),
            "reflections": total_refl,
            "persona": len(personas),
            "corrections": len(corrections),
            "convo_turns": convo_total,
        },
        "coverage": {
            "embedded": embedded, "missing": missing, "stale": stale,
            "corrupt": corrupt, "total": total_entries,
            "embedded_ratio": round(_ratio(embedded, total_entries) or 0.0, 4),
        },
        "space": {
            "primary_dim": health.get("primary_dim"),
            "primary_count": health.get("primary_count", 0),
            "other_space_count": other_space,
            "numpy_ok": bool(health.get("numpy_ok", False)),
        },
        "clusters": {
            "n_clusters": (clusters or {}).get("n_clusters", 0) if has_emb else 0,
            "noise_count": (clusters or {}).get("noise_count", 0) if has_emb else 0,
            "algo": (clusters or {}).get("algo", "none") if has_emb else "none",
        },
        "pipeline": {
            "absorb_rate": round(_ratio(
                sum(1 for f in facts if (f.get("meta") or {}).get("absorbed")),
                len(facts)) or 0.0, 4) if facts else None,
            "promote_rate": round(promote_rate, 4) if promote_rate is not None else None,
            "reject_rate": round(reject_rate, 4) if reject_rate is not None else None,
            "extract_yield": round(extract_yield, 4) if extract_yield is not None else None,
            "pending": len(pending),
            "pending_old": len(pending_old),
        },
    }

    confidence = _confidence(
        embedded=embedded, total=total_entries, has_emb=has_emb,
        sources=lmeta.get("sources_present", {}),
        other_space=other_space, total_refl=total_refl,
        fidelity_unverifiable=fidelity_unverifiable,
    )

    return {
        "character": character,
        "cards": cards,
        "findings": findings,
        "attention_count": attention_count,
        "meta": {
            "sources_present": lmeta.get("sources_present", {}),
            "generated_with_embeddings": has_emb,
            "confidence": confidence,
            "warnings": warnings,
        },
    }


def _cluster_is_tight(cluster: dict[str, Any], space: dict[str, Any]) -> bool:
    """Mean cosine of a cluster's members to its centroid ≥ A2 threshold."""
    import numpy as np  # local — numpy already a hard dep where matrix exists

    matrix = space.get("_matrix")
    by_id = space.get("_by_id", {})
    if matrix is None:
        return False
    idxs = [by_id[i] for i in cluster.get("member_ids", []) if i in by_id]
    if len(idxs) < 2:
        return False
    sub = matrix[idxs]
    centroid = sub.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm <= 0:
        return False
    centroid = centroid / norm
    return float((sub @ centroid).mean()) >= A2_TIGHT_CENTROID_SIM


def _b2_candidates(character: str, space: dict[str, Any]) -> list[dict[str, Any]]:
    """L1 retrieval: same-entity pairs with cosine in the candidate band.

    A *retrieval* of "items on the same topic worth a human/LLM review", sorted
    so pairs with a one-sided negation cue come first. This is **not** a
    contradiction verdict (blueprint §6.1: STS embeddings are negation-blind).
    """
    import numpy as np

    matrix = space.get("_matrix")
    ids = space.get("_ids", [])
    meta_by_id = space.get("_meta_by_id", {})
    if matrix is None or len(ids) < 2:
        return []

    out: list[tuple[float, dict[str, Any]]] = []
    n = len(ids)
    chunk = 512
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        block = matrix[start:end] @ matrix.T
        for bi in range(end - start):
            i = start + bi
            if i + 1 >= n:
                continue
            row = block[bi]
            js = np.nonzero((row[i + 1:] >= B2_CANDIDATE_LOW)
                            & (row[i + 1:] < B2_CANDIDATE_HIGH))[0]
            ma = meta_by_id.get(ids[i], {})
            for jj in js:
                j = i + 1 + int(jj)
                mb = meta_by_id.get(ids[j], {})
                if ma.get("entity") != mb.get("entity") or ma.get("entity") is None:
                    continue
                ta, tb = ma.get("text", ""), mb.get("text", "")
                polarity = 1 if (_has_negation(ta) != _has_negation(tb)) else 0
                out.append((polarity + float(row[j]) * 0.001, {
                    "a": ids[i], "b": ids[j], "entity": ma.get("entity"),
                    "score": round(float(row[j]), 6),
                    "a_label": ta[:80], "b_label": tb[:80],
                    "polarity_hint": bool(polarity),
                }))
    out.sort(key=lambda t: -t[0])
    return [c for _, c in out[:50]]


def _promotion_drift(personas: list[dict[str, Any]], space: dict[str, Any]) -> dict[str, Any]:
    """G1: cosine between a promoted persona entry and its source reflection.

    Returns ``{drifted:[...], unverifiable:int}``. ``unverifiable`` counts
    promoted entries where one side lacks a primary-space vector (honest partial
    coverage — blueprint §3.1.1), so we never mislabel "can't check" as "drift".
    """
    matrix = space.get("_matrix")
    by_id = space.get("_by_id", {})
    drifted: list[dict[str, Any]] = []
    unverifiable = 0
    if matrix is None:
        return {"drifted": [], "unverifiable": 0}
    for p in personas:
        meta = p.get("meta") or {}
        if meta.get("source") != "reflection":
            continue
        src = str(meta.get("source_id") or "")
        if not src:
            continue
        pi = by_id.get(p["id"])
        ri = by_id.get(src)
        if pi is None or ri is None:
            unverifiable += 1
            continue
        sim = float(matrix[pi] @ matrix[ri])  # both unit-norm
        if sim < FIDELITY_DRIFT_WARN:
            drifted.append({"persona": p["id"], "reflection": src,
                            "score": round(sim, 6), "label": p.get("label", "")})
    drifted.sort(key=lambda d: d["score"])
    return {"drifted": drifted, "unverifiable": unverifiable}


def _confidence(
    *, embedded: int, total: int, has_emb: bool, sources: dict[str, Any],
    other_space: int, total_refl: int, fidelity_unverifiable: int,
) -> dict[str, Any]:
    """Conclusion-credibility meta (blueprint §1.2): say what we could/couldn't see.

    ``level`` ∈ high/medium/low; ``notes`` is a list of stable codes the UI maps
    to a sentence ("没有向量 → A/C/E.split/G 不可用", etc.).
    """  # noqa: DOCSTRING_CJK
    notes: list[str] = []
    ratio = (embedded / total) if total else 0.0
    if not has_emb:
        notes.append("NO_EMBEDDINGS")
    elif ratio < 0.5:
        notes.append("LOW_EMBED_COVERAGE")
    if other_space:
        notes.append("SPLIT_SPACES")
    if not sources.get("time_indexed_db"):
        notes.append("NO_TIME_DB")
    if total_refl == 0:
        notes.append("NO_REFLECTIONS")
    if fidelity_unverifiable:
        notes.append("FIDELITY_PARTIAL")

    if not has_emb or ratio < 0.3:
        level = "low"
    elif ratio < 0.8 or notes:
        level = "medium"
    else:
        level = "high"
    return {"level": level, "embedded_ratio": round(ratio, 4), "notes": notes}


# ── P29.2 — optional LLM layer (AI report + contradiction NLI judgement) ──────
#
# Both coroutines mirror embedding_space.build_cluster_labels exactly: they run
# under the caller's ``session_operation`` (the router), stamp the ``memory.llm``
# wire, and **degrade, never 500** — any LLM/config failure returns a structured
# fallback whose ``warnings`` name the actionable reason (e.g. which API to fill).


def _overview_digest_for_llm(overview: dict[str, Any]) -> str:
    """Compact, language-neutral-ish digest of the overview for the AI report."""
    cards = overview.get("cards", {})
    comp = cards.get("composition", {})
    cov = cards.get("coverage", {})
    pipe = cards.get("pipeline", {})
    lines = [
        f"记忆构成: facts={comp.get('facts')}, reflections={comp.get('reflections')}, "
        f"persona={comp.get('persona')}, corrections={comp.get('corrections')}, "
        f"对话回合={comp.get('convo_turns')}.",
        f"嵌入覆盖: embedded={cov.get('embedded')}/{cov.get('total')} "
        f"(missing={cov.get('missing')}, stale={cov.get('stale')}, corrupt={cov.get('corrupt')}).",
        f"流水线: 晋升率={pipe.get('promote_rate')}, 否决率={pipe.get('reject_rate')}, "
        f"抽取产出率={pipe.get('extract_yield')}, 待处理反思={pipe.get('pending')} "
        f"(超期={pipe.get('pending_old')}).",
        f"需关注项: {overview.get('attention_count')}.",
        "发现清单 (code/严重度/数量):",
    ]
    for f in overview.get("findings", []):
        lines.append(f"  - {f['code']} [{f['severity']}] x{f['count']} "
                     f"(category={f['category']}, stage={f['stage']})")
    conf = overview.get("meta", {}).get("confidence", {})
    lines.append(f"结论可信度: level={conf.get('level')}, "
                 f"embedded_ratio={conf.get('embedded_ratio')}, notes={conf.get('notes')}.")
    return "\n".join(lines)


def _ai_report_prompt(digest: str) -> str:
    return (
        "你是记忆系统健康分析助手。下面是某角色记忆系统的自动体检概况 (只读统计, "
        "已按规则得出, 不要质疑数字本身)。\n请基于这些信号, 用简洁中文给出:\n"
        "1) 一句话总体判断;\n2) 最值得优先处理的 2-4 个问题 (按严重度, 说明为什么以及"
        "建议怎么做, 但只提建议, 系统不会自动修改记忆);\n3) 一句话说明本次结论的可信度"
        "局限 (基于结论可信度 notes)。\n请直接输出可读文本, 不要输出 JSON, 不要复述原始清单。\n\n"
        + digest
    )


async def build_ai_report(session, character: str) -> dict[str, Any]:
    """``POST /api/memory/overview/ai_report`` — LLM narrative health report.

    Recomputes the overview server-side (off the event loop), hands a compact
    digest to the memory model, and returns a short prioritized narrative.
    Degrades to ``method='unavailable'`` with an actionable reason on any
    failure — never raises, never 500s.
    """
    overview = await asyncio.to_thread(build_overview, character)
    warnings = list(overview.get("meta", {}).get("warnings", []))

    try:
        from tests.testbench.chat_messages import ROLE_USER
        from tests.testbench.logger import python_logger
        from tests.testbench.pipeline.memory_runner import _llm_for_memory
        from tests.testbench.pipeline.wire_tracker import (
            record_last_llm_wire, update_last_llm_wire_reply,
        )
    except Exception as exc:  # noqa: BLE001 — missing deps → graceful fallback
        warnings.append(f"AI 报告不可用 ({type(exc).__name__}).")
        return {"method": "unavailable", "report": "", "overview": overview,
                "warnings": warnings}

    digest = _overview_digest_for_llm(overview)
    prompt = _ai_report_prompt(digest)
    wire = [{"role": ROLE_USER, "content": prompt}]
    try:
        record_last_llm_wire(session, wire, source="memory.llm",
                             note="memory.overview.ai_report")
    except Exception as exc:  # noqa: BLE001 — observability must not block LLM
        python_logger().debug(
            "memory.overview.ai_report: record_last_llm_wire failed: %s: %s",
            type(exc).__name__, exc)

    try:
        llm = _llm_for_memory(session, temperature=0.2)
        raw = ""
        try:
            resp = await llm.ainvoke(prompt)
            raw = (getattr(resp, "content", "") or "").strip()
        finally:
            try:
                await llm.aclose()
            except Exception:  # noqa: BLE001
                pass
        try:
            update_last_llm_wire_reply(session, reply_chars=len(raw))
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the click
        reason = str(exc).strip() or type(exc).__name__
        warnings.append(f"AI 报告生成失败。原因: {reason}")
        return {"method": "unavailable", "report": "", "overview": overview,
                "warnings": warnings}

    if not raw:
        warnings.append("AI 报告为空, 模型未返回内容。")
        return {"method": "unavailable", "report": "", "overview": overview,
                "warnings": warnings}
    return {"method": "llm", "report": raw, "overview": overview,
            "warnings": warnings}


def _contradiction_prompt(items: list[dict[str, Any]]) -> str:
    lines = []
    for k, it in enumerate(items):
        lines.append(f"[{k}] 实体={it.get('entity')} | A: {it.get('a_label')} || "
                     f"B: {it.get('b_label')}")
    return (
        "你是记忆一致性判定助手。下面每一行是一对关于同一对象的记忆 A 与 B。\n"
        "请对每一对做自然语言推理 (NLI), 判断二者关系, 只能从以下四类里选一个:\n"
        "  contradiction(互相矛盾) / duplicate(重复同义) / complementary(互补不冲突) "
        "/ unrelated(其实无关)。\n"
        "注意: 语义相似不等于矛盾, 也不等于重复; 请实际比较语义与极性。\n\n"
        + "\n".join(lines) + "\n\n"
        "只输出一个 JSON 数组, 每项形如 {\"i\": <行号整数>, \"relation\": \"<四类之一>\", "
        "\"reason\": \"<不超过20字的中文理由>\"}; 为上面每一对都给一项, 不要输出任何解释文字。"
    )


async def judge_contradictions(session, character: str, *, max_items: int = 20) -> dict[str, Any]:
    """``POST /api/memory/overview/contradictions`` — L2 NLI over L1 candidates.

    Retrieves same-topic candidate pairs (L1, blueprint §6) and asks the memory
    model to *judge* each as contradiction/duplicate/complementary/unrelated.
    This is the **only** layer allowed to assert a contradiction. Degrades to
    ``method='unavailable'`` (candidates still returned) on any failure.
    """
    space = await asyncio.to_thread(_build_space, character)
    candidates = await asyncio.to_thread(_b2_candidates, character, space)
    candidates = candidates[:max_items]
    warnings: list[str] = list(space.get("warnings") or [])

    if not candidates:
        return {"method": "none", "verdicts": [], "candidates": [], "warnings": warnings}

    try:
        from tests.testbench.chat_messages import ROLE_USER
        from tests.testbench.logger import python_logger
        from tests.testbench.pipeline.memory_runner import (
            _llm_for_memory, _strip_code_fence,
        )
        from tests.testbench.pipeline.wire_tracker import (
            record_last_llm_wire, update_last_llm_wire_reply,
        )
        from utils.file_utils import robust_json_loads
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"矛盾裁决不可用 ({type(exc).__name__}), 仅返回候选对。")
        return {"method": "unavailable", "verdicts": [], "candidates": candidates,
                "warnings": warnings}

    prompt = _contradiction_prompt(candidates)
    wire = [{"role": ROLE_USER, "content": prompt}]
    try:
        record_last_llm_wire(session, wire, source="memory.llm",
                             note="memory.overview.contradictions")
    except Exception as exc:  # noqa: BLE001
        python_logger().debug(
            "memory.overview.contradictions: record_last_llm_wire failed: %s: %s",
            type(exc).__name__, exc)

    verdicts: list[dict[str, Any]] = []
    try:
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
        parsed = robust_json_loads(raw) if raw else []
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                try:
                    k = int(item.get("i"))
                except (TypeError, ValueError):
                    continue
                if not (0 <= k < len(candidates)):
                    continue
                rel = str(item.get("relation") or "").strip()
                if rel not in ("contradiction", "duplicate", "complementary", "unrelated"):
                    continue
                c = candidates[k]
                verdicts.append({
                    "a": c["a"], "b": c["b"], "entity": c.get("entity"),
                    "relation": rel, "reason": str(item.get("reason") or "")[:60],
                    "score": c.get("score"),
                    "a_label": c.get("a_label", ""), "b_label": c.get("b_label", ""),
                })
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the click
        reason = str(exc).strip() or type(exc).__name__
        warnings.append(f"矛盾裁决失败, 仅返回候选对。原因: {reason}")
        return {"method": "unavailable", "verdicts": [], "candidates": candidates,
                "warnings": warnings}

    if not verdicts:
        warnings.append("模型未返回可用的裁决, 仅返回候选对。")
        return {"method": "unavailable", "verdicts": [], "candidates": candidates,
                "warnings": warnings}
    return {"method": "llm", "verdicts": verdicts, "candidates": candidates,
            "warnings": warnings}

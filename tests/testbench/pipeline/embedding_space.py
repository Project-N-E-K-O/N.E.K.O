"""P28.1 — read-only memory **embedding space** aggregator (single chokepoint).

Builds the analysis payloads behind the "向量空间" sub-page of the 记忆系统分析
workspace. Like :mod:`memory_lineage`, this is the **only** place the embedding
analysis shape is assembled; the frontend renders the returned coordinates /
scores verbatim and never re-derives them from raw vectors (blueprint §3.1).

What it does (all read-only, no model load):
  * Read ``facts.json`` / ``reflections.json`` / ``persona.json`` from the
    active character's sandbox memory dir.
  * Classify each entry's stored embedding triple as embedded / missing /
    stale (text changed since embed) / corrupt, grouped by vector dim.
  * For the **primary** vector space (the dim with the most embedded entries —
    different dims are not comparable, blueprint §2.5), expose:
      - ``space``      : 2D coordinates (PCA by default) + health meta
      - ``neighbors``  : cosine top-k for one entry
      - ``bridges``    : per-reflection semantic-nearest facts vs the declared
                          ``source_fact_ids`` (ties this page to P27 lineage)

Honest gating (blueprint §2.4): a character whose memory has never been
backfilled simply has 0 embedded entries — every endpoint returns a valid,
empty-but-described payload (health counts) rather than an error. The UI turns
that into a "import an already-embedded character" prompt.

Discipline: read only, soft errors (a bad file → warning + partial result),
lazy-imports ``memory.embeddings`` pure helpers only (never the runtime
``EmbeddingService`` — blueprint §1.3).
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import asyncio
import hashlib
import threading
from typing import Any

from tests.testbench.pipeline.memory_lineage import (
    _memory_dir,
    _read_json,
    _truncate,
)

# numpy is a hard project dependency (memory.embeddings uses it), but we keep
# the import soft so the whole router doesn't 500 on an exotic minimal env —
# instead the page reports "analysis unavailable".
try:
    import numpy as _np
    _NUMPY_OK = True
except Exception:  # noqa: BLE001
    _np = None  # type: ignore
    _NUMPY_OK = False

#: Memory types that carry a persisted ``embedding`` triple (blueprint §2.2).
#: recent / messages are intentionally excluded — they have no embedding.
ENTRY_TYPES = ("fact", "reflection", "persona")

#: Defaults for ④duplicates / ⑤matrix (blueprint §10). Kept as module
#: constants (not config.py) to avoid an import cycle; tune in one place.
DUP_THRESHOLD_DEFAULT = 0.95   # cosine ≥ this ⇒ flagged as a near-duplicate pair
DUP_MAX_PAIRS = 500            # cap the returned pair list (sorted by score desc)
MATRIX_MAX_N = 80              # ⑤ heatmap only renders a subset this large

#: P28.5 自动聚类 + 簇标签 (blueprint §11). Clustering runs on the **original
#: high-dim** primary vector space (cosine), never the 2D projection.
CLUSTER_CC_THRESHOLD = 0.55    # numpy-fallback: cosine ≥ this links two points
CLUSTER_MIN_SIZE_FLOOR = 2     # HDBSCAN min_cluster_size lower bound
CLUSTER_MIN_SIZE_CAP = 8       # HDBSCAN min_cluster_size upper bound (auto by √N)
CLUSTER_LABEL_SAMPLES = 12     # how many per-cluster texts we hand the LLM namer
CLUSTER_LABEL_PREVIEW = 80     # per-sample char cap fed to the LLM


def _text_sha256(text: str) -> str:
    """Mirror ``memory.embeddings._embedding_text_sha256`` exactly so our
    staleness check matches what the backfill worker stamped."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _collect_entries(character: str, warnings: list[str]) -> list[dict[str, Any]]:
    """Flatten facts + reflections + persona into a uniform entry list.

    Each entry: ``{id, type, entity, text, embedding, embedding_model_id,
    embedding_text_sha256, source_fact_ids}``. Non-dict / id-less rows are
    skipped (defensive — testbench lets testers craft malformed JSON).
    """
    mem = _memory_dir(character)
    facts = _read_json(mem / "facts.json", expect=list, warnings=warnings)
    reflections = _read_json(mem / "reflections.json", expect=list, warnings=warnings)
    persona = _read_json(mem / "persona.json", expect=dict, warnings=warnings)

    out: list[dict[str, Any]] = []

    for f in facts:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        out.append({
            "id": str(f["id"]),
            "type": "fact",
            "entity": f.get("entity"),
            "text": f.get("text", "") or "",
            "embedding": f.get("embedding"),
            "embedding_model_id": f.get("embedding_model_id"),
            "embedding_text_sha256": f.get("embedding_text_sha256"),
            "source_fact_ids": [],
        })

    for r in reflections:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        out.append({
            "id": str(r["id"]),
            "type": "reflection",
            "entity": r.get("entity"),
            "text": r.get("text", "") or "",
            "embedding": r.get("embedding"),
            "embedding_model_id": r.get("embedding_model_id"),
            "embedding_text_sha256": r.get("embedding_text_sha256"),
            "source_fact_ids": [str(x) for x in (r.get("source_fact_ids") or []) if x],
        })

    for entity_key, section in persona.items():
        if not isinstance(section, dict):
            continue
        section_facts = section.get("facts")
        if not isinstance(section_facts, list):
            continue
        for pf in section_facts:
            if not isinstance(pf, dict) or not pf.get("id"):
                continue
            out.append({
                "id": str(pf["id"]),
                "type": "persona",
                "entity": entity_key,
                "text": pf.get("text", "") or "",
                "embedding": pf.get("embedding"),
                "embedding_model_id": pf.get("embedding_model_id"),
                "embedding_text_sha256": pf.get("embedding_text_sha256"),
                "source_fact_ids": [],
            })

    return out


def _classify(entry: dict[str, Any]) -> tuple[str, Any, int | None]:
    """Return ``(status, decoded_vector_or_None, dim_or_None)``.

    status ∈ {embedded, missing, stale, corrupt}:
      * missing — no embedding string stored
      * corrupt — stored but won't decode (bad base64 / NaN / odd length)
      * stale   — decodes, but sha256(text) != stored embedding_text_sha256
                  (the text was edited after the vector was computed)
      * embedded — decodes and the text fingerprint still matches
    """
    from memory.embeddings import decode_embedding

    emb = entry.get("embedding")
    if not isinstance(emb, str) or not emb:
        return "missing", None, None
    vec = decode_embedding(emb)
    if vec is None or getattr(vec, "size", 0) == 0:
        return "corrupt", None, None
    dim = int(vec.size)
    if entry.get("embedding_text_sha256") != _text_sha256(entry.get("text", "")):
        return "stale", vec, dim
    return "embedded", vec, dim


def _build_space(character: str) -> dict[str, Any]:
    """Core builder shared by every endpoint.

    Classifies all entries, picks the primary vector space (most-populated
    dim), and stacks its embedded vectors into a normalized matrix. Returns a
    structure carrying both JSON-able health meta and in-memory numpy state
    (``_matrix`` / ``_ids`` / ``_by_id``) for the neighbor / bridge maths.
    """
    warnings: list[str] = []
    entries = _collect_entries(character, warnings)

    classified: list[dict[str, Any]] = []
    dims_present: dict[int, int] = {}
    counts = {"total": len(entries), "embedded": 0, "missing": 0,
              "stale": 0, "corrupt": 0}

    if not _NUMPY_OK:
        return {
            "entries": [], "warnings": warnings + ["numpy 不可用, 向量分析关闭"],
            "health": {**counts, "dims_present": {}, "primary_dim": None,
                       "other_space_count": 0, "numpy_ok": False},
            "_matrix": None, "_ids": [], "_by_id": {},
        }

    for e in entries:
        status, vec, dim = _classify(e)
        counts[status] += 1
        rec = {
            "id": e["id"], "type": e["type"], "entity": e["entity"],
            "text": e["text"], "status": status, "dim": dim,
            "model_id": e.get("embedding_model_id"),
            "source_fact_ids": e.get("source_fact_ids", []),
            "_vec": vec if status == "embedded" else None,
        }
        classified.append(rec)
        if status == "embedded" and dim is not None:
            dims_present[dim] = dims_present.get(dim, 0) + 1

    primary_dim = max(dims_present, key=lambda d: dims_present[d]) if dims_present else None
    other_space_count = sum(c for d, c in dims_present.items() if d != primary_dim)

    ids: list[str] = []
    rows: list[Any] = []
    by_id: dict[str, int] = {}
    meta_by_id: dict[str, dict[str, Any]] = {}
    if primary_dim is not None:
        for rec in classified:
            if rec["status"] == "embedded" and rec["dim"] == primary_dim:
                v = _np.asarray(rec["_vec"], dtype=_np.float32)
                n = float(_np.linalg.norm(v))
                if n <= 0:
                    continue
                by_id[rec["id"]] = len(ids)
                ids.append(rec["id"])
                rows.append(v / n)  # defensive re-normalize for clean cosine
                meta_by_id[rec["id"]] = {
                    "id": rec["id"], "type": rec["type"], "entity": rec["entity"],
                    "text": rec["text"], "model_id": rec["model_id"],
                    "source_fact_ids": rec["source_fact_ids"],
                }
    matrix = _np.vstack(rows) if rows else None

    health = {
        **counts,
        "dims_present": {str(d): c for d, c in sorted(dims_present.items())},
        "primary_dim": primary_dim,
        "primary_count": len(ids),
        "other_space_count": other_space_count,
        "numpy_ok": True,
    }
    # Strip the heavy in-memory vectors from the serializable entry list.
    public_entries = [
        {k: v for k, v in rec.items() if k != "_vec"} for rec in classified
    ]
    return {
        "entries": public_entries, "warnings": warnings, "health": health,
        "_matrix": matrix, "_ids": ids, "_by_id": by_id, "_meta_by_id": meta_by_id,
    }


def _pca_2d(matrix) -> list[list[float]]:
    """Project rows to 2D via PCA (SVD on the centered matrix). Deterministic.

    Handles degenerate shapes: 0 rows → []; 1 row → [[0,0]]; a single
    informative axis → second coord 0.
    """
    n = matrix.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [[0.0, 0.0]]
    centered = matrix - matrix.mean(axis=0, keepdims=True)
    try:
        _u, _s, vt = _np.linalg.svd(centered, full_matrices=False)
    except Exception:  # noqa: BLE001 — SVD non-convergence → flat fallback
        return [[0.0, 0.0] for _ in range(n)]
    comps = vt[:2]
    if comps.shape[0] < 2:
        pad = _np.zeros((2 - comps.shape[0], comps.shape[1]), dtype=comps.dtype)
        comps = _np.vstack([comps, pad])
    coords = centered @ comps.T
    return [[float(x), float(y)] for x, y in coords[:, :2]]


# ── UMAP (optional, on-demand installed — blueprint §5.2) ────────────────
#
# umap-learn pulls heavy binary deps (numba/llvmlite/scikit-learn) so it is
# NOT a default dependency. The page works on PCA alone; UMAP is an opt-in
# upgrade the tester installs via POST /embedding/enable_umap. We memoize the
# positive result; a negative result is re-checked (cheap import attempt) so a
# fresh install becomes visible without a server restart.
_UMAP_OK: bool | None = None


def umap_available() -> bool:
    """True if ``umap-learn`` is importable in the current interpreter."""
    global _UMAP_OK
    if _UMAP_OK:
        return True
    try:
        import umap  # noqa: F401
        _UMAP_OK = True
    except Exception:  # noqa: BLE001 — not installed / broken binary deps
        _UMAP_OK = False
    return _UMAP_OK


def _umap_2d(matrix) -> list[list[float]]:
    """Project rows to 2D via UMAP (cosine metric, fixed seed → deterministic).

    UMAP needs a few samples (n_neighbors < n_samples); for tiny corpora we
    fall back to PCA rather than let UMAP raise.
    """
    import umap  # local import — only when actually requested + available

    n = matrix.shape[0]
    if n < 4:
        return _pca_2d(matrix)
    n_neighbors = int(min(15, n - 1))
    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        metric="cosine",
        random_state=42,
        init="random",
    )
    coords = reducer.fit_transform(matrix)
    return [[float(x), float(y)] for x, y in coords[:, :2]]


def _reduce_2d(matrix, reducer: str) -> tuple[list[list[float]], str]:
    """Dispatch 2D reduction. Returns ``(coords, reducer_used)``.

    ``reducer='umap'`` uses UMAP only when available; any unavailability or
    UMAP runtime error falls back to PCA (reported via ``reducer_used``).
    """
    if matrix is None:
        return [], "pca"
    if reducer == "umap" and umap_available():
        try:
            return _umap_2d(matrix), "umap"
        except Exception:  # noqa: BLE001 — UMAP runtime fault → PCA fallback
            return _pca_2d(matrix), "pca"
    return _pca_2d(matrix), "pca"


# Coordinate cache: reduction (esp. UMAP on thousands of points) costs seconds.
# Key by (character, primary_dim, corpus content hash, reducer) so it survives
# repeated page visits but invalidates the moment the memory corpus changes.
_COORDS_CACHE: "dict[str, tuple[list[list[float]], str]]" = {}
_COORDS_CACHE_MAX = 32
# build_space_view runs under asyncio.to_thread, so concurrent requests share
# this dict from multiple threads. Guard the check-then-set + FIFO eviction so
# a race can't corrupt the dict or skip the wrong key during eviction.
_COORDS_CACHE_LOCK = threading.Lock()


def _corpus_hash(ids: list[str], matrix) -> str:
    h = hashlib.sha256()
    h.update(("|".join(ids)).encode("utf-8"))
    if matrix is not None:
        h.update(matrix.tobytes())
    return h.hexdigest()


def _refresh_umap_flag() -> bool:
    """Drop the import cache + memoized flag so a just-installed umap is seen."""
    import importlib
    global _UMAP_OK
    importlib.invalidate_caches()
    _UMAP_OK = None
    return umap_available()


def _run_install(cmd: list[str]) -> tuple[int | None, str]:
    """Run an install command. Returns ``(returncode_or_None, log)``; never
    raises. ``returncode is None`` ⇒ the command couldn't even be spawned."""
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd, capture_output=True, text=True, timeout=900,
        )
    except FileNotFoundError as exc:
        return None, f"无法调用 {cmd[0]} ({exc})."
    except subprocess.TimeoutExpired:
        return 124, "安装超时 (>900s). 可能网络缓慢或在编译 numba/llvmlite."
    except Exception as exc:  # noqa: BLE001 — never let install crash the route
        return None, f"异常: {type(exc).__name__}: {exc}"
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return proc.returncode, out


def install_umap() -> dict[str, Any]:
    """Best-effort online install of ``umap-learn`` into the running interpreter.

    Blueprint §5.2 (online-only; offline wheelhouse deferred). Returns
    ``{ok, installed, reducer_available, log}``; never raises.

    The testbench typically runs from a **uv-managed venv that has no ``pip``**
    (uv venvs aren't pip-seeded), so a plain ``python -m pip`` fails with
    "No module named pip". We therefore try several installers in order and
    stop as soon as ``umap`` imports:

      1. ``uv pip install`` (uv is this project's package manager) — most
         reliable here; targets the current interpreter.
      2. ``python -m pip install`` — works on pip-seeded envs.
      3. ``python -m ensurepip`` then ``python -m pip install`` — bootstraps
         pip into the interpreter, then installs.
    """
    import shutil
    import sys

    if umap_available():
        return {"ok": True, "installed": False, "reducer_available": True,
                "log": "umap-learn 已可用, 无需安装."}
    if not _NUMPY_OK:
        return {"ok": False, "installed": False, "reducer_available": False,
                "log": "numpy 不可用, 向量分析关闭, 无法启用 UMAP."}

    attempts: list[tuple[str, list[str]]] = []
    uv = shutil.which("uv")
    if uv:
        attempts.append(("uv pip install",
                         [uv, "pip", "install", "--python", sys.executable, "umap-learn"]))
    attempts.append(("python -m pip install",
                     [sys.executable, "-m", "pip", "install", "umap-learn"]))

    logs: list[str] = []
    for name, cmd in attempts:
        rc, out = _run_install(cmd)
        logs.append(f"$ {name} (rc={rc})\n{out}")
        if rc == 0 and _refresh_umap_flag():
            return {"ok": True, "installed": True, "reducer_available": True,
                    "log": _tail("\n\n".join(logs))}

    # Last resort: bootstrap pip via ensurepip, then pip install.
    rc, out = _run_install([sys.executable, "-m", "ensurepip", "--upgrade"])
    logs.append(f"$ python -m ensurepip --upgrade (rc={rc})\n{out}")
    if rc == 0:
        rc2, out2 = _run_install([sys.executable, "-m", "pip", "install", "umap-learn"])
        logs.append(f"$ python -m pip install (after ensurepip) (rc={rc2})\n{out2}")
        if rc2 == 0 and _refresh_umap_flag():
            return {"ok": True, "installed": True, "reducer_available": True,
                    "log": _tail("\n\n".join(logs))}

    # Everything failed — surface the combined log (tail) so the UI can show why.
    avail = _refresh_umap_flag()
    return {"ok": bool(avail), "installed": False, "reducer_available": avail,
            "log": _tail("\n\n".join(logs)) or "所有安装方式均失败 (无 uv / 无 pip / 网络?)."}


def _tail(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "…(已截断)\n" + text[-limit:]


def build_space_view(character: str, *, reducer: str = "pca") -> dict[str, Any]:
    """``GET /api/memory/embedding/space`` — ②散点 + ①体检 合一.

    Returns 2D points for the primary vector space plus health meta. ``reducer``
    is ``pca`` (default, always available) or ``umap`` (P28.4, only when
    ``umap-learn`` is installed); an unavailable/failed UMAP quietly falls back
    to PCA, reported via ``meta.reducer_used`` / ``meta.umap_available``.
    """  # noqa: DOCSTRING_CJK
    space = _build_space(character)
    matrix = space["_matrix"]
    ids = space["_ids"]
    meta_by_id = space.get("_meta_by_id", {})

    want = "umap" if str(reducer).lower() == "umap" else "pca"
    if matrix is not None:
        cache_key = f"{character}|{space['health']['primary_dim']}|{_corpus_hash(ids, matrix)}|{want}"
        with _COORDS_CACHE_LOCK:
            cached = _COORDS_CACHE.get(cache_key)
        if cached is not None:
            coords, reducer_used = cached
        else:
            # Compute OUTSIDE the lock so concurrent reductions don't serialize
            # (a redundant double-compute on a simultaneous first-miss is rare
            # and harmless); only the dict mutation needs to be atomic.
            coords, reducer_used = _reduce_2d(matrix, want)
            with _COORDS_CACHE_LOCK:
                if (len(_COORDS_CACHE) >= _COORDS_CACHE_MAX
                        and cache_key not in _COORDS_CACHE):
                    # crude FIFO eviction to bound memory
                    _COORDS_CACHE.pop(next(iter(_COORDS_CACHE)), None)
                _COORDS_CACHE[cache_key] = (coords, reducer_used)
    else:
        coords, reducer_used = [], "pca"

    points = []
    for idx, mid in enumerate(ids):
        m = meta_by_id.get(mid, {})
        x, y = (coords[idx] if idx < len(coords) else [0.0, 0.0])
        points.append({
            "id": mid, "type": m.get("type"), "entity": m.get("entity"),
            "x": x, "y": y, "label": _truncate(m.get("text", "")),
        })

    return {
        "points": points,
        "meta": {
            **space["health"],
            "reducer_requested": want,
            "reducer_used": reducer_used,
            "umap_available": umap_available(),
            "warnings": space["warnings"],
        },
    }


def build_neighbors(character: str, node_id: str, *, k: int = 10) -> dict[str, Any]:
    """``GET /api/memory/embedding/neighbors`` — cosine top-k for one entry.

    ``node_id`` must be an embedded entry in the primary space; otherwise
    returns ``found=False`` so the UI can explain (not embedded / different
    vector space).
    """
    space = _build_space(character)
    matrix = space["_matrix"]
    by_id = space["_by_id"]
    ids = space["_ids"]
    meta_by_id = space.get("_meta_by_id", {})
    node_id = str(node_id or "")

    if matrix is None or node_id not in by_id:
        return {"query_id": node_id, "found": False, "neighbors": []}

    qi = by_id[node_id]
    sims = matrix @ matrix[qi]  # rows are unit-norm → dot == cosine
    k = max(1, min(int(k or 10), len(ids) - 1)) if len(ids) > 1 else 0
    order = _np.argsort(-sims)
    neighbors = []
    for j in order:
        j = int(j)
        if j == qi:
            continue
        mid = ids[j]
        m = meta_by_id.get(mid, {})
        neighbors.append({
            "id": mid, "type": m.get("type"), "entity": m.get("entity"),
            "score": round(float(sims[j]), 6), "label": _truncate(m.get("text", "")),
        })
        if len(neighbors) >= k:
            break
    return {"query_id": node_id, "found": True, "neighbors": neighbors}


def build_bridges(
    character: str, *, top_k: int = 3, space: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``GET /api/memory/embedding/bridges`` — ⑥语义源 vs 结构源 (P27 联动).

    For each embedded reflection, compute its top-k semantically nearest
    **facts** (cosine) and compare against the declared ``source_fact_ids``.
    Surfaces:
      * ``missing_in_declared`` — semantically close facts NOT listed as a source
        (possible un-credited source)
      * ``extra_in_declared``   — declared source facts that are NOT semantically
        close / not embedded (weak or text-drifted attribution)

    Only facts present in the primary embedded space participate.

    ``space`` may be a pre-built :func:`_build_space` result (P29 overview
    aggregates several views — injecting it avoids re-reading + re-decoding the
    whole corpus once per view). Defaults to building it fresh.
    """  # noqa: DOCSTRING_CJK
    space = space if space is not None else _build_space(character)
    matrix = space["_matrix"]
    by_id = space["_by_id"]
    ids = space["_ids"]
    meta_by_id = space.get("_meta_by_id", {})

    if matrix is None:
        return {"rows": [], "fact_count": 0, "reflection_count": 0}

    # Full text lookup over ALL collected entries (embedded or not) so a declared
    # source fact that lacks a vector still shows its real content instead of a
    # bare id. ``_meta_by_id`` only carries embedded entries; ``entries`` carries
    # every classified row (id/type/text/status/...).
    text_by_id = {
        e["id"]: (e.get("text") or "")
        for e in space.get("entries", [])
        if isinstance(e, dict) and e.get("id")
    }

    fact_rows = [i for i, mid in enumerate(ids)
                 if meta_by_id.get(mid, {}).get("type") == "fact"]
    refl_ids = [mid for mid in ids
                if meta_by_id.get(mid, {}).get("type") == "reflection"]
    if not fact_rows or not refl_ids:
        return {"rows": [], "fact_count": len(fact_rows),
                "reflection_count": len(refl_ids)}

    fact_mat = matrix[fact_rows]  # (F, dim)
    top_k = max(1, int(top_k or 3))
    rows = []
    for rid in refl_ids:
        m = meta_by_id.get(rid, {})
        qi = by_id[rid]
        sims = fact_mat @ matrix[qi]  # (F,) cosine vs each embedded fact
        order = _np.argsort(-sims)[:top_k]
        semantic_top = [
            {"fact_id": ids[fact_rows[int(j)]],
             "score": round(float(sims[int(j)]), 6),
             "label": _truncate(meta_by_id.get(ids[fact_rows[int(j)]], {}).get("text", ""))}
            for j in order
        ]
        semantic_ids = {s["fact_id"] for s in semantic_top}
        declared = [fid for fid in m.get("source_fact_ids", [])]
        declared_set = set(declared)
        embedded_fact_ids = {ids[i] for i in fact_rows}
        missing_in_declared = [s for s in semantic_top
                               if s["fact_id"] not in declared_set]
        extra_in_declared = [
            {"fact_id": fid,
             "embedded": fid in embedded_fact_ids,
             "exists": fid in text_by_id,
             "label": _truncate(text_by_id.get(fid, ""))}
            for fid in declared if fid not in semantic_ids
        ]
        rows.append({
            "reflection_id": rid,
            "reflection_label": _truncate(m.get("text", "")),
            "entity": m.get("entity"),
            "declared": declared,
            "semantic_top": semantic_top,
            "missing_in_declared": missing_in_declared,
            "extra_in_declared": extra_in_declared,
            "agreement": len(semantic_ids & declared_set),
        })
    return {"rows": rows, "fact_count": len(fact_rows),
            "reflection_count": len(refl_ids)}


def build_duplicates(
    character: str, *, threshold: float = DUP_THRESHOLD_DEFAULT,
    max_pairs: int = DUP_MAX_PAIRS, space: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``GET /api/memory/embedding/duplicates`` — ④近重复对 (cosine ≥ 阈值).

    Upper-triangle cosine over the primary vector space; returns every pair at
    or above ``threshold`` (sorted by score desc, capped at ``max_pairs``).
    Cross-type pairs are included (a fact and a reflection can be near-dupes);
    ``same_type`` flags whether both endpoints share a type.

    Computed in row chunks so memory stays bounded at thousands of vectors
    (``N²`` similarity is never fully materialized).

    ``space`` may be a pre-built :func:`_build_space` result (P29 reuse).
    """  # noqa: DOCSTRING_CJK
    space = space if space is not None else _build_space(character)
    matrix = space["_matrix"]
    ids = space["_ids"]
    meta_by_id = space.get("_meta_by_id", {})
    thr = float(threshold)

    if matrix is None or len(ids) < 2:
        return {"pairs": [], "threshold": thr, "count": 0,
                "capped": False, "candidates": len(ids)}

    n = len(ids)
    found: list[tuple[float, int, int]] = []
    chunk = 512
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        block = matrix[start:end] @ matrix.T  # (rows, n) cosine
        for bi in range(end - start):
            i = start + bi
            if i + 1 >= n:
                continue
            row = block[bi]
            js = _np.nonzero(row[i + 1:] >= thr)[0]
            for jj in js:
                j = i + 1 + int(jj)
                found.append((float(row[j]), i, j))

    found.sort(key=lambda t: -t[0])
    capped = len(found) > max_pairs
    found = found[:max_pairs]

    pairs = []
    for score, i, j in found:
        a = meta_by_id.get(ids[i], {})
        b = meta_by_id.get(ids[j], {})
        pairs.append({
            "a": ids[i], "b": ids[j],
            "score": round(score, 6),
            "a_type": a.get("type"), "b_type": b.get("type"),
            "a_entity": a.get("entity"), "b_entity": b.get("entity"),
            "a_label": _truncate(a.get("text", "")),
            "b_label": _truncate(b.get("text", "")),
            "same_type": a.get("type") == b.get("type"),
        })
    return {"pairs": pairs, "threshold": thr, "count": len(pairs),
            "capped": capped, "candidates": n}


def _seriate(sub) -> list[int]:
    """Greedy nearest-neighbor seriation → local order that clusters similar
    rows adjacently (makes the ⑤heatmap show block structure). Deterministic:
    starts at index 0 and repeatedly appends the most-similar unused row.
    """
    n = sub.shape[0]
    if n <= 2:
        return list(range(n))
    sims = sub @ sub.T
    used = [False] * n
    order = [0]
    used[0] = True
    for _ in range(n - 1):
        last = order[-1]
        row = sims[last]
        best = -1
        best_s = -2.0
        for j in range(n):
            if used[j]:
                continue
            if row[j] > best_s:
                best_s = float(row[j])
                best = j
        order.append(best)
        used[best] = True
    return order


def build_matrix(
    character: str, *, ids: list[str] | None = None, max_n: int = MATRIX_MAX_N,
) -> dict[str, Any]:
    """``GET /api/memory/embedding/matrix`` — ⑤相似度矩阵 (子集下钻).

    Takes a subset of entry ids (defaults to the whole primary space), keeps the
    ones present & embedded in the primary vector space, caps at ``max_n``, and
    returns an NxN cosine matrix **already reordered** by greedy seriation so
    similar items sit adjacently (visible blocks = clusters).

    Returns ``order`` (display-order ids) + ``cells`` (NxN, same order) +
    per-id ``labels``/``types``/``entities`` + ``truncated`` (subset clipped).
    """  # noqa: DOCSTRING_CJK
    space = _build_space(character)
    matrix = space["_matrix"]
    all_ids = space["_ids"]
    by_id = space["_by_id"]
    meta_by_id = space.get("_meta_by_id", {})

    empty = {"order": [], "cells": [], "n": 0, "truncated": False,
             "requested": 0, "labels": {}, "types": {}, "entities": {}}
    if matrix is None:
        return empty

    if ids:
        req = [str(x) for x in ids if str(x) in by_id]
    else:
        req = list(all_ids)
    requested = len(req)
    truncated = requested > max_n
    req = req[:max_n]
    if not req:
        return {**empty, "requested": requested}

    idxs = [by_id[i] for i in req]
    sub = matrix[idxs]                       # (N, dim), already unit-norm
    order_local = _seriate(sub)
    ordered_ids = [req[k] for k in order_local]
    ordered_idx = [idxs[k] for k in order_local]
    om = matrix[ordered_idx]
    cells_arr = om @ om.T                     # (N, N) cosine
    cells = [[round(float(x), 4) for x in row] for row in cells_arr]

    labels = {i: _truncate(meta_by_id.get(i, {}).get("text", "")) for i in ordered_ids}
    types = {i: meta_by_id.get(i, {}).get("type") for i in ordered_ids}
    entities = {i: meta_by_id.get(i, {}).get("entity") for i in ordered_ids}
    return {
        "order": ordered_ids, "cells": cells, "n": len(ordered_ids),
        "truncated": truncated, "requested": requested,
        "labels": labels, "types": types, "entities": entities,
    }


# ── P28.5 自动聚类 + 簇标签 (blueprint §11) ──────────────────────────────
#
# Cluster the **original high-dim** primary vector space (NOT the 2D projection,
# which distorts distance). Vectors are L2-normalized so euclidean distance is
# monotonic with cosine (‖a-b‖²=2-2cos) — HDBSCAN's default euclidean metric is
# therefore equivalent to cosine without materializing an O(N²) distance matrix.


def _hdbscan_labels(matrix, min_cluster_size: int):
    """Cluster via ``sklearn.cluster.HDBSCAN`` (auto cluster count + noise=-1).

    Returns an int label array, or ``None`` if sklearn is unavailable / the fit
    fails (caller then falls back to the numpy connected-components clusterer).
    sklearn ships with ``umap-learn`` (P28.4 enable-UMAP), so installing UMAP
    also unlocks this better clusterer.
    """
    try:
        from sklearn.cluster import HDBSCAN
    except Exception:  # noqa: BLE001 — sklearn not installed
        return None
    try:
        model = HDBSCAN(min_cluster_size=int(max(2, min_cluster_size)))
        return _np.asarray(
            model.fit_predict(_np.asarray(matrix, dtype=_np.float64)), dtype=int)
    except Exception:  # noqa: BLE001 — degenerate input / version skew
        return None


def _cosine_cc(matrix, threshold: float):
    """Zero-dependency fallback clusterer: connected components of the graph
    ``cosine(i,j) ≥ threshold`` via union-find. Components of size ≥2 are
    clusters (contiguous ids from 0); singletons are noise (-1). Deterministic.

    Computed in row chunks so the full N×N similarity is never materialized.
    """
    n = matrix.shape[0]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    chunk = 512
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        block = matrix[start:end] @ matrix.T
        for bi in range(end - start):
            i = start + bi
            if i + 1 >= n:
                continue
            js = _np.nonzero(block[bi][i + 1:] >= threshold)[0]
            for jj in js:
                union(i, i + 1 + int(jj))

    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    labels = [-1] * n
    cid = 0
    for members in comps.values():
        if len(members) >= 2:
            for m in members:
                labels[m] = cid
            cid += 1
    return _np.asarray(labels, dtype=int)


def build_clusters(
    character: str, *, min_cluster_size: int | None = None,
    cc_threshold: float = CLUSTER_CC_THRESHOLD,
    space: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``GET /api/memory/embedding/clusters`` — auto-cluster the scatter (no LLM).

    Clusters the primary vector space in its original high-dim cosine geometry
    (HDBSCAN preferred, numpy connected-components fallback) and returns, per
    point, its cluster id (``assignments``) plus a per-cluster summary with the
    **medoid** (most central member) as the default label. The LLM naming pass
    is a separate opt-in endpoint (:func:`build_cluster_labels`).

    ``space`` may be a pre-built :func:`_build_space` result (P29 reuse).
    """
    import math

    space = space if space is not None else _build_space(character)
    matrix = space["_matrix"]
    ids = space["_ids"]
    meta_by_id = space.get("_meta_by_id", {})
    warnings = list(space["warnings"])

    if matrix is None or len(ids) < 2:
        return {"algo": "none", "n_clusters": 0, "noise_count": 0,
                "assignments": {}, "clusters": [],
                "meta": {"total": len(ids)}, "warnings": warnings}

    n = len(ids)
    if min_cluster_size is None:
        min_cluster_size = max(
            CLUSTER_MIN_SIZE_FLOOR,
            min(CLUSTER_MIN_SIZE_CAP, round(math.sqrt(n) / 2)))

    labels = _hdbscan_labels(matrix, min_cluster_size)
    algo = "hdbscan"
    if labels is None:
        labels = _cosine_cc(matrix, cc_threshold)
        algo = "cosine_cc"
        warnings.append(
            "sklearn 不可用, 改用 numpy cosine 连通分量聚类 (启用 UMAP 会带入 "
            "HDBSCAN, 聚类更准).")

    members_by_cluster: dict[int, list[int]] = {}
    for i in range(n):
        lab = int(labels[i])
        if lab >= 0:
            members_by_cluster.setdefault(lab, []).append(i)

    assignments = {ids[i]: int(labels[i]) for i in range(n)}
    noise_count = sum(1 for i in range(n) if int(labels[i]) < 0)

    clusters = []
    for lab in sorted(members_by_cluster):
        members = members_by_cluster[lab]
        sub = matrix[members]
        centroid = sub.mean(axis=0)
        cn = float(_np.linalg.norm(centroid))
        if cn > 0:
            centroid = centroid / cn
        sims = sub @ centroid
        order = list(_np.argsort(-sims))
        ordered = [members[int(k)] for k in order]  # most-central first = medoid
        medoid_id = ids[ordered[0]]
        samples = [
            (meta_by_id.get(ids[m], {}).get("text", "") or "")[:CLUSTER_LABEL_PREVIEW]
            for m in ordered[:CLUSTER_LABEL_SAMPLES]
        ]
        clusters.append({
            "cluster": lab,
            "size": len(members),
            "medoid_id": medoid_id,
            "label": _truncate(meta_by_id.get(medoid_id, {}).get("text", "")),
            "samples": [s for s in samples if s],
            "member_ids": [ids[m] for m in ordered],
        })

    return {
        "algo": algo,
        "n_clusters": len(clusters),
        "noise_count": noise_count,
        "assignments": assignments,
        "clusters": clusters,
        "meta": {
            "total": n,
            "min_cluster_size": int(min_cluster_size),
            "cc_threshold": float(cc_threshold) if algo == "cosine_cc" else None,
        },
        "warnings": warnings,
    }


def _cluster_label_prompt(clusters: list[dict[str, Any]]) -> str:
    lines = []
    for c in clusters:
        sample_text = " | ".join(c.get("samples") or []) or (c.get("label") or "")
        lines.append(f"[{c['cluster']}] (共{c['size']}条) {sample_text}")
    return (
        "你是记忆聚类命名助手. 下面每一行是一个记忆簇及其代表性内容样本.\n"
        "为每个簇起一个 2 到 6 个字的中文概括词条, 高度概括该簇的共同主题.\n\n"
        + "\n".join(lines) + "\n\n"
        "只输出一个 JSON 数组, 每项形如 {\"cluster\": <簇编号整数>, "
        "\"label\": \"<概括词条>\"}; 为上面每个簇都给一项. 不要输出任何解释文字."
    )


async def build_cluster_labels(session, character: str) -> dict[str, Any]:
    """``POST /api/memory/embedding/cluster_labels`` — LLM-name each cluster.

    Recomputes :func:`build_clusters` server-side (trust the backend, not the
    client's view), hands every cluster's sample texts to the memory model in a
    **single** call, and asks for a short 概括 per cluster. Reuses the P27
    ``memory.llm`` wire-stamp discipline. On any failure (LLM error / no model /
    unparsable reply) it degrades to ``method="medoid"`` with the medoid labels —
    never raises, never 500s the click.
    """  # noqa: DOCSTRING_CJK
    # build_clusters() is sync HDBSCAN/numpy work; offload it so this async
    # endpoint doesn't block the event loop before its first await (same
    # to_thread discipline as GET /embedding/clusters).
    data = await asyncio.to_thread(build_clusters, character)
    clusters = data["clusters"]
    warnings = list(data["warnings"])

    def _medoid_result(method: str) -> dict[str, Any]:
        return {
            "method": method,
            "labels": {str(c["cluster"]): c["label"] for c in clusters},
            "clusters": clusters,
            "algo": data["algo"],
            "n_clusters": data["n_clusters"],
            "noise_count": data["noise_count"],
            "warnings": warnings,
        }

    if not clusters:
        return _medoid_result("medoid")

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
    except Exception as exc:  # noqa: BLE001 — missing deps → medoid fallback
        warnings.append(f"LLM 概括不可用 ({type(exc).__name__}), 用 medoid 代表词.")
        return _medoid_result("medoid")

    prompt = _cluster_label_prompt(clusters)
    wire = [{"role": ROLE_USER, "content": prompt}]
    try:
        record_last_llm_wire(
            session, wire, source="memory.llm",
            note="memory.embedding.cluster_label",
        )
    except Exception as exc:  # noqa: BLE001 — observability must not block LLM
        python_logger().debug(
            "memory.embedding.cluster_label: record_last_llm_wire failed: %s: %s",
            type(exc).__name__, exc,
        )

    llm_labels: dict[int, str] = {}
    method = "llm"
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
                    cid = int(item.get("cluster"))
                except (TypeError, ValueError):
                    continue
                lab = str(item.get("label") or "").strip()
                if lab:
                    llm_labels[cid] = lab[:40]
    except Exception as exc:  # noqa: BLE001 — degrade, never 500 the click
        # Surface the *actionable* reason. A missing/blank memory-model config
        # raises MemoryOpError whose message already names what to fill (e.g.
        # "请先在 Settings → Models → memory 填好 base_url 与 model。"); pass it
        # through verbatim so the UI can tell the tester exactly which API to set.
        reason = str(exc).strip() or type(exc).__name__
        warnings.append(f"LLM 概括失败, 已回退到每簇代表记忆作标签。原因: {reason}")
        method = "medoid"

    if not llm_labels:
        if method == "llm":
            warnings.append("LLM 未返回可用的概括, 已回退到每簇代表记忆作标签。")
        method = "medoid"

    labels = {
        str(c["cluster"]): (llm_labels.get(c["cluster"]) or c["label"])
        for c in clusters
    }
    return {
        "method": method,
        "labels": labels,
        "clusters": clusters,
        "algo": data["algo"],
        "n_clusters": data["n_clusters"],
        "noise_count": data["noise_count"],
        "warnings": warnings,
    }

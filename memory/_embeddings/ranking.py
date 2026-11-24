"""Exact, vectorized ranking helpers over cosine score arrays.

Every helper here reproduces, element for element, the result of a plain
Python ``sorted(..., key=-score)`` over the same scores: strongest first, and
equal scores keep ascending index order (Python's sort is stable). They exist
so hot paths can drop their per-element Python loops without changing a single
recall, cluster or merge decision.

numpy is imported lazily so importing this module never requires it (the
``embeddings_fallback`` stub path runs without numpy).
"""
from __future__ import annotations

import asyncio
from typing import Callable


async def run_clusters_off_loop(
    compute: Callable[[list], list[list]],
    entries: list,
) -> list[list]:
    """Run a sync ``compute(entries) -> clusters`` in a worker thread.

    Decoding a pool and the N×N similarity pass take tens to hundreds of ms
    on a large pool, so they must not run on the event loop. The worker only
    ever sees shallow copies of the entries, all taken here in one
    synchronous step: a coroutine re-stamping an entry's embedding fields
    while the worker runs can never hand it a half-written
    ``embedding`` / ``embedding_text_sha256`` / ``embedding_model_id``
    triple, and the worker writes nothing back. Cluster members are mapped
    back to the caller's own entry objects, so callers see exactly what the
    in-loop call used to return.
    """
    snapshot = [dict(e) if isinstance(e, dict) else e for e in entries]
    clusters = await asyncio.to_thread(compute, snapshot)
    original = {id(copy): entry for copy, entry in zip(snapshot, entries)}
    return [
        [original.get(id(member), member) for member in cluster]
        for cluster in clusters
    ]


def _at_least_mask(values, threshold: float):
    """``values >= threshold`` with ``threshold`` compared as a Python float.

    A bare ``float32_array >= 0.85`` casts the threshold down to float32
    first. When that rounding goes down, a float32 value equal to the rounded
    threshold passes even though it is below the real threshold — a
    difference from the per-element ``float(row[j]) >= threshold`` this
    replaces. Comparing against the smallest representable value that is
    still ``>= threshold`` gives exactly the same verdict for every element.
    """
    import numpy as np

    dtype = values.dtype
    if dtype.kind != 'f' or dtype == np.float64:
        return values >= float(threshold)
    bound = dtype.type(threshold)
    if float(bound) < float(threshold):
        bound = np.nextafter(bound, dtype.type(np.inf))
    return values >= bound


def stable_top_indices(values, k: int | None = None):
    """Indices of ``values`` strongest first, ties by ascending index.

    Same order as ``sorted(range(n), key=lambda i: -values[i])[:k]``. With
    ``k`` smaller than ``n`` only the top ``k`` are fully sorted; the cut
    point uses ``partition``, and ties straddling the cut are filled in
    ascending index order, so the result never depends on how
    ``argpartition`` happened to break them. Values must not be NaN.
    """
    import numpy as np

    values = np.asarray(values)
    n = int(values.size)
    if k is None or k >= n:
        return np.argsort(-values, kind='stable')
    if k <= 0:
        return np.empty(0, dtype=np.intp)
    cut = n - k
    kth = np.partition(values, cut)[cut]
    keep = values > kth
    need = k - int(np.count_nonzero(keep))
    if need > 0:
        keep[np.flatnonzero(values == kth)[:need]] = True
    picked = np.flatnonzero(keep)
    return picked[np.argsort(-values[picked], kind='stable')]


def threshold_top_k(row, threshold: float, k: int) -> list[int]:
    """Indices with ``row[j] >= threshold``, strongest first, at most ``k``.

    Equivalent to::

        cand = [(j, float(row[j])) for j in range(len(row))
                if float(row[j]) >= threshold]
        cand.sort(key=lambda x: -x[1])
        [j for j, _ in cand[:k]]
    """
    import numpy as np

    idx = np.flatnonzero(_at_least_mask(row, threshold))
    if idx.size == 0:
        return []
    if k < 0:
        # Mirrors ``cand[:k]`` for a negative k: drop the weakest |k|.
        order = stable_top_indices(row[idx])
        return idx[order].tolist()[:k]
    return idx[stable_top_indices(row[idx], k)].tolist()


def cosine_clusters(
    matrix,
    *,
    threshold: float,
    topk: int,
    size_max: int,
) -> list[list[int]]:
    """Connected components of the per-row top-K cosine graph, as row indices.

    ``matrix`` holds one L2-normalized vector per row (dot product ==
    cosine). An edge ``i -> j`` exists when ``j`` is among row ``i``'s
    ``topk`` strongest neighbours with cosine ``>= threshold`` (self excluded).
    Components of size 1 are dropped; components above ``size_max`` keep the
    ``size_max`` members with the strongest in-component neighbour, ties by
    ascending row index. Components come out in the order their union-find
    root is first met scanning rows ascending, members ascending (before any
    truncation) — the order the refine engines have always produced.
    """
    import numpy as np

    n = int(matrix.shape[0])
    if n < 2:
        return []
    sim_matrix = matrix @ matrix.T
    np.fill_diagonal(sim_matrix, -1.0)

    adj = [threshold_top_k(sim_matrix[i], threshold, topk) for i in range(n)]

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in adj[i]:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters: list[list[int]] = []
    for indices in groups.values():
        if len(indices) < 2:
            continue
        if len(indices) > size_max:
            members = np.asarray(indices, dtype=np.intp)
            internal = sim_matrix[np.ix_(members, members)]
            np.fill_diagonal(internal, -np.inf)
            strongest = internal.max(axis=1)
            if size_max >= 0:
                order = stable_top_indices(strongest, size_max)
            else:
                # Mirrors ``strengths[:size_max]`` for a negative cap.
                order = stable_top_indices(strongest)[:size_max]
            indices = members[order].tolist()
        clusters.append(indices)
    return clusters

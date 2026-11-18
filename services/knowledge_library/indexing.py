"""Derived per-revision full-text indexes and bounded hybrid ranking."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

from utils.knowledge_text import fts_match_expression, search_tokens
from utils.text_chunking import OVERLAP_CHARS, source_windows

from .models import LibraryError, LibraryLimits
from .embedding_compat import semantic_cosine
from .package_validate import CHUNKING_VERSION
from .persistence import ensure_directory, sync_directory, sync_file


def make_chunks(document_id: str, filename: str, sections: list[dict]) -> list[dict]:
    """Preserve each source section and hash the exact, untruncated model input."""
    chunks = []
    source_start = 0
    for section in sections:
        # Sections already represent real source paragraphs. Keep exact source
        # bytes instead of reflowing sentence whitespace or capping at the old
        # public-entry chunk count; the domain enforces its own total limits.
        window_start = 0
        for part, body in enumerate(source_windows(section["text"])):
            location = {**section["location"], "part": part + 1,
                        "source_start": source_start + window_start,
                        "source_end": source_start + window_start + len(body)}
            text = f"{filename}\n{body}"
            if len(text) > 2000:
                # A title must not silently push its body out of encoder input.
                raise LibraryError("chunk_too_large")
            identity = json.dumps([document_id, location, body], ensure_ascii=False, sort_keys=True)
            chunks.append({
                "chunk_id": hashlib.sha256(identity.encode()).hexdigest(),
                "document_id": document_id, "location": location, "text": body,
                "embedding_text": text,
                "input_sha256": hashlib.sha256(text.encode()).hexdigest(), "vector": None,
            })
            window_start += len(body) - OVERLAP_CHARS
        source_start += len(section["text"]) + 2  # parse_source joins sections with two newlines.
    return chunks


async def plan_chunks(chunks: list[dict], embedder, *, specification: dict,
                      max_chunks: int, check_cancelled=None) -> list[dict]:
    """Apply the owning tokenizer's exact budget before any document encoding.

    Character windows retain source locations. Token subdivisions are a second
    deterministic step, and every returned range must cover its entire window.
    Missing planners fail closed instead of admitting truncated vectors.
    """
    planner = getattr(embedder, "plan_inputs", None)
    if not callable(planner):
        raise LibraryError("embedding_unavailable")
    inputs = []
    for chunk in chunks:
        body, text = chunk["text"], chunk["embedding_text"]
        if not body or not text.endswith(body):
            raise LibraryError("embedding_plan_invalid")
        prefix = text[:-len(body)]
        window_start = 0
        for index, window in enumerate(source_windows(body)):
            location = dict(chunk["location"])
            if len(body) > 1200:
                location["window_part"] = index + 1
            if type(location.get("source_start")) is int:
                location["source_start"] += window_start
                location["source_end"] = location["source_start"] + len(window)
            inputs.append(({**chunk, "text": window, "location": location}, {"prefix": prefix, "body": window}))
            window_start += len(window) - OVERLAP_CHARS
    planned = []
    for offset in range(0, len(inputs), 16):
        if check_cancelled:
            check_cancelled()
        batch = inputs[offset:offset + 16]
        ranges = await planner([value for _chunk, value in batch], expected_spec=specification)
        if not isinstance(ranges, list) or len(ranges) != len(batch):
            raise LibraryError("embedding_plan_invalid")
        for (chunk, value), parts in zip(batch, ranges):
            if not isinstance(parts, list) or not parts:
                raise LibraryError("embedding_plan_invalid")
            end = 0
            for index, part in enumerate(parts):
                if (not isinstance(part, dict) or set(part) != {"start", "end"}
                        or type(part["start"]) is not int or type(part["end"]) is not int
                        or part["start"] != end or not end < part["end"] <= len(value["body"])):
                    raise LibraryError("embedding_plan_invalid")
                body = value["body"][end:part["end"]]
                location = dict(chunk["location"])
                if len(parts) > 1:
                    # Replanning an already token-split source must retain its
                    # parent path, rather than making siblings reuse positions.
                    parent = location.get("token_path", [location["token_part"]] if "token_part" in location else [])
                    if not isinstance(parent, list) or any(type(value) is not int or value < 1 for value in parent):
                        raise LibraryError("package_invalid_location")
                    location["token_path"] = [*parent, index + 1]
                    location["token_part"] = index + 1
                if type(location.get("source_start")) is int:
                    location["source_end"] = location["source_start"] + part["end"]
                    location["source_start"] += end
                text = value["prefix"] + body
                identity = json.dumps([chunk["document_id"], location, body], ensure_ascii=False, sort_keys=True)
                planned.append({**chunk, "chunk_id": hashlib.sha256(identity.encode()).hexdigest(),
                                "text": body, "location": location, "embedding_text": text,
                                "input_sha256": hashlib.sha256(text.encode()).hexdigest(), "vector": None})
                end = part["end"]
                if len(planned) > max_chunks:
                    raise LibraryError("capacity_chunks")
            if end != len(value["body"]):
                raise LibraryError("embedding_plan_invalid")
    return planned


def checked_vectors(rows: list, count: int, spec: dict) -> list[list[float]]:
    """Validate all rows before admitting any vector into durable domain data."""
    dimension = spec.get("dimension")
    if type(dimension) is not int or not 1 <= dimension <= 768 or len(rows) != count:
        raise LibraryError("invalid_vectors")
    result = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != dimension:
            raise LibraryError("invalid_vectors")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in row):
            raise LibraryError("invalid_vectors")
        values = [float(value) for value in row]
        norm = math.sqrt(sum(value * value for value in values))
        if not all(math.isfinite(value) for value in values) or not math.isfinite(norm) or norm <= 0:
            raise LibraryError("invalid_vectors")
        result.append([value / norm for value in values])
    return result


def build_index(path: Path, documents: list[dict], limits: LibraryLimits) -> None:
    """Build a disposable candidate; its filename is selected only by a committed manifest."""
    rows = [
        (chunk["chunk_id"], " ".join(search_tokens(document["filename"] + " " + chunk["text"])))
        for document in documents for chunk in document["chunks"]
    ]
    if len(rows) > limits.max_chunks:
        raise LibraryError("capacity_chunks")
    ensure_directory(path.parent)
    temporary = path.with_suffix(".building")
    temporary.unlink(missing_ok=True)
    try:
        with sqlite3.connect(temporary) as connection:
            connection.execute("CREATE VIRTUAL TABLE chunks USING fts5(chunk_id UNINDEXED, tokens)")
            connection.executemany("INSERT INTO chunks(chunk_id,tokens) VALUES (?,?)", rows)
        # The active manifest can point here only after the completed database
        # and its new directory entry have reached the platform durability fence.
        sync_file(temporary)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def rank(path: Path, documents: list[dict], query: str, query_vector: list | None, spec: dict | None, limit: int) -> list[dict]:
    """Fuse bounded keyword and positive-affinity candidates for source browsing."""
    expression = fts_match_expression(query)
    lexical = []
    if expression:
        with sqlite3.connect(path) as connection:
            lexical = [row[0] for row in connection.execute(
                "SELECT chunk_id FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT 40",
                (expression,),
            )]
    candidates = {}
    semantic = []
    for document in documents:
        for chunk in document["chunks"]:
            candidates[chunk["chunk_id"]] = (document, chunk)
            vector = chunk.get("vector")
            if query_vector and vector:
                cosine = semantic_cosine(vector, document.get("embedding_spec"), query_vector, spec)
                # Cosine scale has not been calibrated as a relevance gate for
                # this model/corpus. Keep positive finite nearest candidates;
                # this rank does not certify a match or authorize chat context.
                if cosine is not None and math.isfinite(cosine) and cosine > 0:
                    semantic.append((chunk["chunk_id"], cosine))
    semantic.sort(key=lambda item: item[1], reverse=True)
    semantic = semantic[:40]
    scores = {}
    for sequence in (lexical, [key for key, _score in semantic]):
        for position, key in enumerate(sequence):
            scores[key] = scores.get(key, 0.0) + 1 / (60 + position + 1)
    vector_keys = {key for key, _score in semantic}
    semantic_scores = dict(semantic)
    hits = []
    # Swapped lexical/semantic ranks can have exactly equal RRF scores. Use
    # actual semantic affinity before opaque IDs, which carry no relevance.
    for key in sorted(scores, key=lambda item: (-scores[item], -semantic_scores.get(item, 0.0), item))[:limit]:
        document, chunk = candidates[key]
        hits.append({
            "chunk_id": key, "document_id": document["document_id"], "filename": document["filename"],
            "text": chunk["text"], "location": chunk["location"], "score": scores[key],
            "match_type": "hybrid" if key in lexical and key in vector_keys else "semantic" if key in vector_keys else "keyword",
        })
    return hits

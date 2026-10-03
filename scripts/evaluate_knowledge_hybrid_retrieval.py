"""Calibrate semantic knowledge retrieval with the optional local ONNX model.

The evaluator opens the unified knowledge SQLite database in read-only
mode. It never constructs ``KnowledgeStore`` and therefore
cannot create tables, migrate metadata, or rebuild vectors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge.chunking import CHUNKER_VERSION, EMBEDDING_INPUT_VERSION
from knowledge.catalog_overrides import (
    CatalogOverrideError,
    get_catalog_override_path,
    load_disabled_entries,
)
from knowledge.models import normalize_knowledge_title
from knowledge.packs import list_installed_packs, pack_registry_state
from knowledge.store import KnowledgeStoreError, assert_supported_schema

DEFAULT_CASES = (
    PROJECT_ROOT / "tests" / "fixtures" / "knowledge_hybrid_real_model_cases.json"
)
KNOWLEDGE_DATABASE = Path("knowledge.db")
REQUIRED_INPUT_VERSION = str(EMBEDDING_INPUT_VERSION)
REQUIRED_CHUNKER_VERSION = str(CHUNKER_VERSION)
QUERY_BATCH_SIZE = 4


@dataclass(frozen=True, slots=True)
class VectorCorpus:
    matrix: np.ndarray
    rows: tuple[dict[str, object], ...]
    status: dict[str, object]


class EvaluationUnavailable(RuntimeError):
    """The optional model or a compatible prebuilt vector index is absent."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate and calibrate real-model knowledge retrieval.",
    )
    parser.add_argument(
        "--knowledge-root",
        type=Path,
        required=True,
        help="knowledge directory containing knowledge.db",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    return parser


def _load_cases(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("evaluation fixture must use schema_version=1")
    if payload.get("embedding_input_version") != EMBEDDING_INPUT_VERSION:
        raise ValueError(
            "evaluation fixture must use the current embedding input contract"
        )
    positives = payload.get("positives")
    negatives = payload.get("negatives")
    if not isinstance(positives, list) or not positives:
        raise ValueError("evaluation fixture requires positive cases")
    if not isinstance(negatives, list) or not negatives:
        raise ValueError("evaluation fixture requires negative cases")
    identifiers: set[str] = set()
    for case in positives:
        if not isinstance(case, dict) or set(case) != {
            "id",
            "query",
            "expected_source_tag",
            "expected_title",
        }:
            raise ValueError("positive cases use an invalid schema")
        case_id = _required_case_text(case, "id")
        _required_case_text(case, "query")
        expected_source_tag = _required_case_text(case, "expected_source_tag")
        expected_title = _required_case_text(case, "expected_title")
        if not expected_source_tag.startswith("source:") or not expected_source_tag[7:]:
            raise ValueError("positive case source identity is invalid")
        if not normalize_knowledge_title(expected_title):
            raise ValueError("positive case title identity is invalid")
        if case_id in identifiers:
            raise ValueError("case ids must be non-empty and unique")
        identifiers.add(case_id)
    for case in negatives:
        if not isinstance(case, dict) or set(case) != {"id", "query"}:
            raise ValueError("negative cases use an invalid schema")
        case_id = _required_case_text(case, "id")
        _required_case_text(case, "query")
        if case_id in identifiers:
            raise ValueError("case ids must be non-empty and unique")
        identifiers.add(case_id)
    return payload


def _required_case_text(case: Mapping[str, object], field: str) -> str:
    value = case.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"evaluation case {field} must be non-empty text")
    return value.strip()


@contextmanager
def _open_read_only(database_path: Path):
    if not database_path.is_file():
        raise EvaluationUnavailable(f"database_missing:{database_path}")
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    try:
        yield connection
    finally:
        connection.close()


def _load_vectors(
    database_path: Path,
    *,
    model_id: str,
    dimensions: int,
) -> tuple[list[np.ndarray], list[dict[str, object]], dict[str, object]]:
    try:
        disabled = load_disabled_entries(get_catalog_override_path(database_path))
    except CatalogOverrideError as exc:
        raise EvaluationUnavailable("catalog_override_unavailable") from exc
    try:
        with _open_read_only(database_path) as connection:
            try:
                assert_supported_schema(connection)
            except KnowledgeStoreError as exc:
                raise EvaluationUnavailable("index_schema_unavailable") from exc
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            required = {"metadata", "entries", "knowledge_chunks"}
            if not required <= tables:
                raise EvaluationUnavailable("index_schema_unavailable")
            metadata = {
                str(row["key"]): str(row["value"])
                for row in connection.execute(
                    "SELECT key, value FROM metadata WHERE key IN "
                    "('schema_version', 'embedding_input_version', 'chunker_version')"
                ).fetchall()
            }
            if metadata.get("chunker_version") != REQUIRED_CHUNKER_VERSION:
                raise EvaluationUnavailable("chunker_version_mismatch")
            if metadata.get("embedding_input_version") != REQUIRED_INPUT_VERSION:
                raise EvaluationUnavailable(
                    "embedding_input_version_mismatch"
                )
            rows = connection.execute(
                "SELECT knowledge_chunks.entry_rowid, knowledge_chunks.chunk_index, "
                "knowledge_chunks.embedding_dimensions, knowledge_chunks.embedding, "
                "entries.title, entries.tags FROM knowledge_chunks JOIN entries "
                "ON entries.rowid=knowledge_chunks.entry_rowid "
                "WHERE knowledge_chunks.embedding_status='ready' "
                "AND knowledge_chunks.embedding_model_id=? "
                "ORDER BY knowledge_chunks.entry_rowid, knowledge_chunks.chunk_index",
                (model_id,),
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise EvaluationUnavailable(
            f"database_unavailable:{type(exc).__name__}"
        ) from exc

    # Production derives its allowed source set from the installed-pack registry
    # (KnowledgeService._source_material_types), so a community source with no
    # registry entry is never served. Ready vectors for such a source would
    # otherwise inflate recall here and calibrate a threshold against entries the
    # runtime will not return. list_installed_packs() collapses a corrupt registry
    # into an empty tuple, which is fail-closed for production but would silently
    # invalidate a calibration run, so check the registry state explicitly.
    registry_state = pack_registry_state(database_path)
    if registry_state == "invalid":
        raise EvaluationUnavailable("pack_registry_invalid")
    installed_source_tags = {
        str(pack.get("source_tag") or "")
        for pack in list_installed_packs(database_path)
    }

    vectors: list[np.ndarray] = []
    result_rows: list[dict[str, object]] = []
    invalid_vectors = 0
    disabled_vectors = 0
    for row in rows:
        try:
            tags = json.loads(str(row["tags"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise EvaluationUnavailable("entry_identity_unavailable") from exc
        source_tags = (
            [tag for tag in tags if isinstance(tag, str) and tag.startswith("source:")]
            if isinstance(tags, list)
            else []
        )
        title = str(row["title"])
        title_key = normalize_knowledge_title(title)
        if len(source_tags) != 1 or not title_key:
            raise EvaluationUnavailable("entry_identity_unavailable")
        if (source_tags[0], title_key) in disabled:
            disabled_vectors += 1
            continue
        if (
            source_tags[0].startswith("source:community.")
            and source_tags[0] not in installed_source_tags
        ):
            raise EvaluationUnavailable("unresolved_community_source")
        raw = row["embedding"]
        if (
            int(row["embedding_dimensions"] or 0) != dimensions
            or not isinstance(raw, bytes)
            or len(raw) != dimensions * 2
        ):
            invalid_vectors += 1
            continue
        vector = np.frombuffer(raw, dtype="<f2").astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm <= 0 or not np.isfinite(vector).all():
            invalid_vectors += 1
            continue
        vectors.append(vector / norm)
        result_rows.append(
            {
                "entry_rowid": int(row["entry_rowid"]),
                "chunk_index": int(row["chunk_index"]),
                "source_tag": source_tags[0],
                "title": title,
            }
        )
    if not vectors:
        raise EvaluationUnavailable("ready_vectors_missing")
    return (
        vectors,
        result_rows,
        {
            "database": str(database_path),
            "schema_version": int(metadata.get("schema_version", "0")),
            "chunker_version": int(metadata["chunker_version"]),
            "embedding_input_version": int(metadata["embedding_input_version"]),
            "ready_vectors": len(vectors),
            "invalid_vectors": invalid_vectors,
            "disabled_vectors": disabled_vectors,
            "pack_registry_state": registry_state,
        },
    )


def _load_vector_corpus(
    knowledge_root: Path,
    *,
    model_id: str,
    dimensions: int,
) -> VectorCorpus:
    vectors, rows, status = _load_vectors(
        knowledge_root / KNOWLEDGE_DATABASE,
        model_id=model_id,
        dimensions=dimensions,
    )
    return VectorCorpus(
        matrix=np.stack(vectors).astype(np.float32, copy=False),
        rows=tuple(rows),
        status=status,
    )


def _rank_entries(
    corpus: VectorCorpus,
    query_vector: Sequence[float],
) -> list[dict[str, object]]:
    query = np.asarray(query_vector, dtype=np.float32).ravel()
    if query.size != corpus.matrix.shape[1] or not np.isfinite(query).all():
        raise EvaluationUnavailable("query_embedding_invalid")
    norm = float(np.linalg.norm(query))
    if norm <= 0:
        raise EvaluationUnavailable("query_embedding_empty")
    scores = corpus.matrix @ (query / norm)
    best: dict[int, dict[str, object]] = {}
    for index, score_value in enumerate(scores):
        row = corpus.rows[index]
        key = int(row["entry_rowid"])
        score = float(score_value)
        previous = best.get(key)
        if previous is None or score > float(previous["score"]):
            best[key] = {
                "source_tag": str(row["source_tag"]),
                "title": str(row["title"]),
                "score": round(score, 6),
                "chunk_index": int(row["chunk_index"]),
            }
    return sorted(
        best.values(),
        key=lambda row: (
            -float(row["score"]),
            str(row["source_tag"]),
            str(row["title"]),
        ),
    )


def _expected_rank_and_score(
    ranking: Sequence[Mapping[str, object]],
    case: Mapping[str, object],
) -> tuple[int | None, object | None]:
    expected_source_tag = case.get("expected_source_tag")
    expected_title = case.get("expected_title")
    if (
        not isinstance(expected_source_tag, str)
        or not expected_source_tag.startswith("source:")
        or not isinstance(expected_title, str)
        or not normalize_knowledge_title(expected_title)
    ):
        raise EvaluationUnavailable("positive_identity_unavailable")
    expected_title_key = normalize_knowledge_title(expected_title)
    expected_rank = next(
        (
            index
            for index, row in enumerate(ranking, start=1)
            if row.get("source_tag") == expected_source_tag
            and normalize_knowledge_title(str(row.get("title") or ""))
            == expected_title_key
        ),
        None,
    )
    return (
        expected_rank,
        ranking[expected_rank - 1]["score"]
        if expected_rank is not None
        else None,
    )


def select_threshold(
    positive_results: Sequence[Mapping[str, object]],
    negative_results: Sequence[Mapping[str, object]],
    *,
    minimum_recall: float = 0.80,
    minimum_negative_rejection: float = 0.90,
) -> dict[str, object] | None:
    """Return the lowest 0.01 threshold satisfying both quality targets."""
    if not positive_results or not negative_results:
        raise ValueError("threshold selection requires positive and negative results")
    for step in range(101):
        threshold = step / 100
        positive_passes = sum(
            int(
                int(row.get("expected_rank") or 0) in {1, 2, 3}
                and row.get("expected_score") is not None
                and float(row["expected_score"]) >= threshold
            )
            for row in positive_results
        )
        negative_rejections = sum(
            int(row.get("top1_score") is None or float(row["top1_score"]) < threshold)
            for row in negative_results
        )
        recall = positive_passes / len(positive_results)
        rejection = negative_rejections / len(negative_results)
        if recall >= minimum_recall and rejection >= minimum_negative_rejection:
            return {
                "threshold": round(threshold, 2),
                "recall_at_3": round(recall, 4),
                "negative_rejection": round(rejection, 4),
                "positive_passes": positive_passes,
                "negative_rejections": negative_rejections,
            }
    return None


async def _encode_queries(queries: Sequence[str]) -> list[list[float]]:
    from knowledge.chunking import knowledge_query_embedding_text
    from utils.local_embedding_runtime import (
        get_local_embedding_service,
        get_local_embedding_status,
    )

    service = get_local_embedding_service()
    if not service.is_available() and not service.is_disabled():
        try:
            await service.request_load()
        except Exception as exc:
            raise EvaluationUnavailable(
                f"embedding_load_failed:{type(exc).__name__}"
            ) from exc
    status = get_local_embedding_status()
    if not status.ready:
        reason = status.disable_reason or status.state
        raise EvaluationUnavailable(f"embedding_unavailable:{reason}")

    vectors: list[list[float]] = []
    encoded = [knowledge_query_embedding_text(query) for query in queries]
    for offset in range(0, len(encoded), QUERY_BATCH_SIZE):
        try:
            batch = await service.embed_batch(
                encoded[offset : offset + QUERY_BATCH_SIZE]
            )
        except Exception as exc:
            raise EvaluationUnavailable(
                f"embedding_inference_failed:{type(exc).__name__}"
            ) from exc
        if not isinstance(batch, (list, tuple)):
            raise EvaluationUnavailable("embedding_response_invalid")
        for vector in batch:
            if vector is None:
                raise EvaluationUnavailable("query_embedding_unavailable")
            vectors.append(vector)
    if len(vectors) != len(queries):
        raise EvaluationUnavailable("embedding_response_count_mismatch")
    return vectors


async def evaluate(knowledge_root: Path, cases: Mapping[str, object]) -> dict[str, Any]:
    from utils.local_embedding_runtime import (
        get_local_embedding_service,
        get_local_embedding_status,
    )

    service = get_local_embedding_service()
    if not service.is_available() and not service.is_disabled():
        try:
            await service.request_load()
        except Exception as exc:
            raise EvaluationUnavailable(
                f"embedding_load_failed:{type(exc).__name__}"
            ) from exc
    status = get_local_embedding_status()
    if not status.ready:
        reason = status.disable_reason or status.state
        raise EvaluationUnavailable(f"embedding_unavailable:{reason}")
    corpus = _load_vector_corpus(
        knowledge_root,
        model_id=status.model_id,
        dimensions=status.dimensions,
    )
    positives = list(cases["positives"])
    negatives = list(cases["negatives"])
    all_cases = [*positives, *negatives]
    vectors = await _encode_queries([str(case["query"]) for case in all_cases])

    positive_results: list[dict[str, object]] = []
    negative_results: list[dict[str, object]] = []
    for case, vector in zip(all_cases, vectors, strict=True):
        ranking = _rank_entries(corpus, vector)
        top3 = ranking[:3]
        if "expected_title" in case:
            expected_rank, expected_score = _expected_rank_and_score(ranking, case)
            positive_results.append(
                {
                    **case,
                    "expected_rank": expected_rank,
                    "expected_score": expected_score,
                    "top3": top3,
                }
            )
        else:
            negative_results.append(
                {
                    **case,
                    "top1_score": top3[0]["score"] if top3 else None,
                    "top3": top3,
                }
            )

    targets = cases.get("quality_targets", {})
    minimum_recall = float(targets.get("recall_at_3", 0.80))
    minimum_rejection = float(targets.get("negative_rejection", 0.90))
    recommendation = select_threshold(
        positive_results,
        negative_results,
        minimum_recall=minimum_recall,
        minimum_negative_rejection=minimum_rejection,
    )
    return {
        "state": "complete",
        "model_id": status.model_id,
        "dimensions": status.dimensions,
        "embedding_input_version": EMBEDDING_INPUT_VERSION,
        "index": corpus.status,
        "positive_results": positive_results,
        "negative_results": negative_results,
        "quality_targets": {
            "recall_at_3": minimum_recall,
            "negative_rejection": minimum_rejection,
        },
        "recommendation": recommendation,
        "targets_met": recommendation is not None,
    }


def _emit(payload: Mapping[str, object], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{text}\n", encoding="utf-8")


async def _run(args: argparse.Namespace) -> int:
    from memory.local_embedding_provider import bind_process_local_embedding_provider
    from utils.local_embedding_runtime import release_local_embedding_service

    bind_process_local_embedding_provider()
    try:
        cases = _load_cases(args.cases)
        payload = await evaluate(args.knowledge_root.expanduser().resolve(), cases)
    except (EvaluationUnavailable, OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {
            "state": "unavailable",
            "reason": str(exc),
            "knowledge_root": str(args.knowledge_root),
        }
        _emit(payload, args.output)
        return 2
    finally:
        await release_local_embedding_service()
    _emit(payload, args.output)
    return 0 if payload["targets_met"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

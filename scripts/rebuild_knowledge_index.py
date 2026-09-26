"""Inspect or rebuild the local knowledge vector index.

Status and dry-run modes open SQLite databases read-only and never migrate
them.  Rebuild modes use the knowledge-owned local embedding runtime; they do
not call or modify Memory Server APIs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


KNOWLEDGE_DATABASE = Path("knowledge.db")
DEFAULT_BATCH_SIZE = 4
EMBEDDING_MICROBATCH_SIZE = 4


@dataclass(frozen=True, slots=True)
class KnowledgeTarget:
    database_path: Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or rebuild the local hybrid knowledge index.",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--status",
        action="store_true",
        help="show read-only index status (the default action)",
    )
    action.add_argument(
        "--rebuild",
        action="store_true",
        help="process missing, pending, stale, and retryable failed chunks",
    )
    action.add_argument(
        "--full",
        action="store_true",
        help="discard all derived chunks and rebuild them from source entries",
    )
    action.add_argument(
        "--preflight-pack",
        type=Path,
        metavar="PATH",
        help="validate one pack and report bounded work without installing it",
    )
    action.add_argument(
        "--cancel-job",
        metavar="JOB_ID",
        help="cancel one staged import job and remove its staged payload",
    )
    action.add_argument(
        "--discard-job",
        metavar="JOB_ID",
        help="explicitly remove one quarantined staged import job",
    )
    parser.add_argument(
        "--batch-size",
        type=_batch_size,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=(
            "total embedding work per round from 1 to 128; ONNX inference is "
            "split into batches of at most 4 (default: 4)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="calculate the affected entries and chunks without writing or embedding",
    )
    parser.add_argument(
        "--knowledge-root",
        type=Path,
        help="override the application's knowledge directory",
    )
    parser.add_argument(
        "--local-model",
        action="store_true",
        help=(
            "explicitly select the local shared embedding runtime; retained for "
            "clarity because it is the only v1 rebuild backend"
        ),
    )
    parser.add_argument(
        "--enable-local-pack",
        metavar="PACK_ID",
        help=(
            "explicitly allow local vector maintenance for one installed community "
            "pack before --rebuild or --full"
        ),
    )
    return parser


def _batch_size(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("batch size must be an integer") from exc
    if not 1 <= parsed <= 128:
        raise argparse.ArgumentTypeError("batch size must be between 1 and 128")
    return parsed


def _default_knowledge_root() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager(migrate=False).knowledge_dir)


def _target(root: Path) -> KnowledgeTarget:
    return KnowledgeTarget(root / KNOWLEDGE_DATABASE)


def _installed_pack_targets(
    target: KnowledgeTarget,
    pack_id: str,
) -> tuple[KnowledgeTarget, ...]:
    """Locate a pack from registries without opening or migrating databases."""
    registry_path = target.database_path.with_name("packs.json")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    packs = registry.get("packs") if isinstance(registry, dict) else None
    return (target,) if isinstance(packs, dict) and isinstance(packs.get(pack_id), dict) else ()


@contextmanager
def _open_read_only(database_path: Path):
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    try:
        yield connection
    finally:
        connection.close()


def _table_names(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    )


def inspect_database(database_path: Path) -> dict[str, Any]:
    """Return index counts without creating, migrating, or writing the database."""
    result: dict[str, Any] = {
        "database": str(database_path),
        "database_exists": database_path.is_file(),
        "schema_version": 0,
        "entries_total": 0,
        "entries_missing_chunks": 0,
        "chunks_total": 0,
        "chunks_pending": 0,
        "chunks_ready": 0,
        "chunks_stale": 0,
        "chunks_failed": 0,
        "chunks_failed_retryable_now": 0,
        "chunks_failed_waiting": 0,
        "chunks_failed_exhausted": 0,
        "chunks_local": 0,
        "chunks_prebuilt_only": 0,
        "entries_prebuilt_only": 0,
        "chunks_local_pending": 0,
        "chunks_local_ready": 0,
        "chunks_local_stale": 0,
        "chunks_local_failed": 0,
        "chunks_local_failed_retryable_now": 0,
        "chunks_local_failed_waiting": 0,
        "chunks_local_failed_exhausted": 0,
        "indexed_percent": 0.0,
        "embedding_model_id": "",
    }
    if not database_path.is_file():
        return result
    try:
        with _open_read_only(database_path) as connection:
            tables = _table_names(connection)
            if "metadata" in tables:
                metadata = {
                    str(row["key"]): str(row["value"])
                    for row in connection.execute("SELECT key, value FROM metadata")
                }
                try:
                    result["schema_version"] = int(metadata.get("schema_version", 0))
                except ValueError:
                    result["schema_version"] = 0
                result["embedding_model_id"] = metadata.get("embedding_model_id", "")
            if "entries" in tables:
                result["entries_total"] = int(
                    connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                )
            if "knowledge_chunks" not in tables:
                result["entries_missing_chunks"] = result["entries_total"]
                return result

            chunk_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(knowledge_chunks)"
                ).fetchall()
            }
            has_policy = "embedding_policy" in chunk_columns
            policy_expression = "embedding_policy" if has_policy else "'local'"

            counts = {
                str(row["embedding_status"]): int(row["entry_count"])
                for row in connection.execute(
                    "SELECT embedding_status, COUNT(*) entry_count "
                    "FROM knowledge_chunks GROUP BY embedding_status"
                )
            }
            chunks_total = sum(counts.values())
            result.update(
                {
                    "chunks_total": chunks_total,
                    "chunks_pending": counts.get("pending", 0),
                    "chunks_ready": counts.get("ready", 0),
                    "chunks_stale": counts.get("stale", 0),
                    "chunks_failed": counts.get("failed", 0),
                    "indexed_percent": (
                        round(100.0 * counts.get("ready", 0) / chunks_total, 1)
                        if chunks_total
                        else 0.0
                    ),
                }
            )
            policy_counts = {
                (str(row["policy"]), str(row["embedding_status"])): int(
                    row["entry_count"]
                )
                for row in connection.execute(
                    f"SELECT {policy_expression} policy, embedding_status, "
                    "COUNT(*) entry_count FROM knowledge_chunks "
                    f"GROUP BY {policy_expression}, embedding_status"
                )
            }
            local_total = sum(
                count
                for (policy, _), count in policy_counts.items()
                if policy == "local"
            )
            prebuilt_total = sum(
                count
                for (policy, _), count in policy_counts.items()
                if policy == "prebuilt_only"
            )
            result.update(
                {
                    "chunks_local": local_total,
                    "chunks_prebuilt_only": prebuilt_total,
                    "chunks_local_pending": policy_counts.get(("local", "pending"), 0),
                    "chunks_local_ready": policy_counts.get(("local", "ready"), 0),
                    "chunks_local_stale": policy_counts.get(("local", "stale"), 0),
                    "chunks_local_failed": policy_counts.get(("local", "failed"), 0),
                }
            )
            if has_policy:
                result["entries_prebuilt_only"] = int(
                    connection.execute(
                        "SELECT COUNT(DISTINCT entry_rowid) FROM knowledge_chunks "
                        "WHERE embedding_policy='prebuilt_only'"
                    ).fetchone()[0]
                )
            now = int(time.time())
            failed_counts = connection.execute(
                "SELECT "
                "SUM(CASE WHEN embedding_attempts<8 AND next_retry_at<=? "
                "THEN 1 ELSE 0 END) retryable_now, "
                "SUM(CASE WHEN embedding_attempts<8 AND next_retry_at>? "
                "THEN 1 ELSE 0 END) waiting, "
                "SUM(CASE WHEN embedding_attempts>=8 THEN 1 ELSE 0 END) exhausted "
                "FROM knowledge_chunks WHERE embedding_status='failed'",
                (now, now),
            ).fetchone()
            result.update(
                {
                    "chunks_failed_retryable_now": int(failed_counts[0] or 0),
                    "chunks_failed_waiting": int(failed_counts[1] or 0),
                    "chunks_failed_exhausted": int(failed_counts[2] or 0),
                }
            )
            local_policy_clause = " AND embedding_policy='local'" if has_policy else ""
            result["chunks_local_failed_retryable_now"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks "
                    "WHERE embedding_status='failed' AND embedding_attempts<8 "
                    "AND next_retry_at<=?" + local_policy_clause,
                    (now,),
                ).fetchone()[0]
            )
            result["chunks_local_failed_waiting"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks "
                    "WHERE embedding_status='failed' AND embedding_attempts<8 "
                    "AND next_retry_at>?" + local_policy_clause,
                    (now,),
                ).fetchone()[0]
            )
            result["chunks_local_failed_exhausted"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks "
                    "WHERE embedding_status='failed' AND embedding_attempts>=8"
                    + local_policy_clause
                ).fetchone()[0]
            )
            if "entries" in tables:
                result["entries_missing_chunks"] = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM entries WHERE NOT EXISTS ("
                        "SELECT 1 FROM knowledge_chunks "
                        "WHERE knowledge_chunks.entry_rowid=entries.rowid)"
                    ).fetchone()[0]
                )
    except sqlite3.DatabaseError as exc:
        result["error_type"] = type(exc).__name__
    return result


def inspect_pack_jobs(root: Path) -> list[dict[str, Any]]:
    """Read persistent job metadata without opening or migrating staging databases."""
    from knowledge.pack_jobs import _read_job

    jobs_root = root / ".staging"
    if not jobs_root.is_dir():
        return []
    items = [
        _read_job(job_dir)
        for job_dir in jobs_root.iterdir()
        if job_dir.is_dir() and not job_dir.is_symlink()
    ]
    return sorted(
        items,
        key=lambda item: (
            -_safe_nonnegative_int(item.get("created_at")),
            str(item.get("job_id") or ""),
        ),
    )


def _safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value >= 0 else 0
    if (
        isinstance(value, str)
        and 0 < len(value) <= 20
        and value.isascii()
        and value.isdecimal()
    ):
        return int(value)
    return 0


def _count_derived_chunks(database_path: Path) -> tuple[int, int]:
    """Count valid entries and deterministic v1 chunks using read-only data."""
    from knowledge.chunking import derive_knowledge_chunks
    from knowledge.models import KnowledgeEntry

    if not database_path.is_file():
        return 0, 0
    entries = 0
    chunks = 0
    try:
        with _open_read_only(database_path) as connection:
            if "entries" not in _table_names(connection):
                return 0, 0
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(entries)").fetchall()
            }
            if not {"title", "terms", "tags", "summary", "content"}.issubset(columns):
                return 0, 0
            for row in connection.execute(
                "SELECT rowid, title, terms, tags, summary, content FROM entries "
                "ORDER BY rowid"
            ):
                try:
                    entry = KnowledgeEntry(
                        title=str(row["title"]),
                        terms=json.loads(str(row["terms"])),
                        tags=tuple(json.loads(str(row["tags"]))),
                        summary=str(row["summary"]),
                        content=str(row["content"]),
                    )
                    derived = derive_knowledge_chunks(
                        entry,
                        entry_key=f"{entry.source_tag}\0{entry.title}",
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                entries += 1
                chunks += len(derived)
    except sqlite3.DatabaseError:
        return 0, 0
    return entries, chunks


def dry_run_plan(target: KnowledgeTarget, *, full: bool) -> dict[str, Any]:
    status = inspect_database(target.database_path)
    valid_entries, derived_chunks = _count_derived_chunks(target.database_path)
    if full:
        affected_chunks = max(
            derived_chunks - int(status["chunks_prebuilt_only"]),
            0,
        )
        affected_entries = max(
            valid_entries - int(status["entries_prebuilt_only"]),
            0,
        )
    else:
        affected_chunks = (
            int(status.get("chunks_local_pending", status["chunks_pending"]))
            + int(status.get("chunks_local_stale", status["chunks_stale"]))
            + int(
                status.get(
                    "chunks_local_failed_retryable_now",
                    status["chunks_failed_retryable_now"],
                )
            )
        )
        affected_entries = int(status["entries_missing_chunks"])
        if affected_entries:
            # Missing rows are part of the deterministic derived total, but the
            # exact per-row chunk count would require duplicating Store queries.
            affected_chunks += max(derived_chunks - int(status["chunks_total"]), 0)
    return {
        **status,
        "valid_entries": valid_entries,
        "derived_chunks_after_rebuild": derived_chunks,
        "affected_entries": affected_entries,
        "affected_chunks": affected_chunks,
        "action": "full" if full else "rebuild",
        "dry_run": True,
    }


def _backfill_all(store: Any, *, batch_size: int) -> int:
    from knowledge.packs import installed_source_embedding_policies

    policies = installed_source_embedding_policies(store.database_path)
    processed = 0
    while True:
        count = store.backfill_missing_chunks(
            limit=max(batch_size, 64),
            embedding_policy_by_source=policies,
        )
        processed += count
        if count == 0:
            return processed


def _eligible_chunk_count(status: dict[str, Any]) -> int:
    return (
        int(status.get("chunks_local_pending", status["chunks_pending"]))
        + int(status.get("chunks_local_stale", status["chunks_stale"]))
        + int(
            status.get(
                "chunks_local_failed_retryable_now",
                status["chunks_failed_retryable_now"],
            )
        )
    )


def _completion_state(status: dict[str, Any], *, last_batch_state: str = "") -> str:
    local_failed = int(status.get("chunks_local_failed", status["chunks_failed"]))
    if (
        int(
            status.get(
                "chunks_local_failed_exhausted", status["chunks_failed_exhausted"]
            )
        )
        > 0
    ):
        return "failed_exhausted"
    if int(status.get("entries_missing_chunks", 0)) > 0:
        return "backfill_incomplete"
    if local_failed > 0:
        return "retry_scheduled"
    if (
        int(status.get("chunks_local_pending", status["chunks_pending"])) == 0
        and int(status.get("chunks_local_stale", status["chunks_stale"])) == 0
    ):
        return "complete"
    if last_batch_state in {
        "disabled",
        "embedding_unavailable",
        "not_ready",
    }:
        return "embedding_unavailable"
    return "processing_incomplete"


async def _run_embedding_work_round(
    store: Any,
    *,
    work_budget: int,
) -> tuple[int, int, int, int, str]:
    """Process one bounded round while keeping each ONNX call at four texts."""
    from knowledge.vector_index import index_embedding_batch

    selected = 0
    stored = 0
    failed = 0
    stale_writebacks = 0
    last_state = "no_work"
    while selected < work_budget:
        result = await index_embedding_batch(
            store,
            batch_size=min(EMBEDDING_MICROBATCH_SIZE, work_budget - selected),
            load_model=True,
        )
        last_state = result.state
        selected += result.selected
        stored += result.stored
        failed += result.failed
        stale_writebacks += result.stale_writebacks
        if result.selected == 0:
            break
        await asyncio.sleep(0)
    return selected, stored, failed, stale_writebacks, last_state


def _ready_vector_work_budget(
    status: dict[str, Any],
    *,
    batch_size: int,
    max_ready_vectors: int,
) -> int:
    return min(
        batch_size,
        max(max_ready_vectors - int(status.get("chunks_ready", 0)), 0),
    )


async def rebuild_target(
    target: KnowledgeTarget,
    *,
    full: bool,
    batch_size: int,
) -> tuple[dict[str, Any], bool]:
    """Reconcile chunks and generate all currently eligible embeddings."""
    from knowledge.pack_jobs import MAX_READY_VECTOR_CHUNKS
    from knowledge.store import KnowledgeStore
    from utils.local_embedding_runtime import get_local_embedding_status

    action = "full" if full else "rebuild"
    reset_chunks = 0
    backfilled_entries = 0
    embedded_chunks = 0
    failed_chunks = 0
    stale_writebacks = 0
    last_batch_state = "no_work"

    def inspection_unavailable(status: dict[str, Any]):
        return {
            **status,
            "action": action,
            "reset_chunks": reset_chunks,
            "backfilled_entries": backfilled_entries,
            "embedded_chunks": embedded_chunks,
            "failed_chunks_this_run": failed_chunks,
            "stale_writebacks": stale_writebacks,
            "last_batch_state": last_batch_state,
            "result_state": "inspection_unavailable",
            "complete": False,
        }, False

    before = inspect_database(target.database_path)
    if not target.database_path.is_file():
        return {**before, "action": "skipped", "reason": "database_missing"}, True
    if before.get("error_type"):
        return inspection_unavailable(before)

    store = KnowledgeStore(target.database_path)
    reset_chunks = store.reset_chunk_index(full=True) if full else 0
    backfilled_entries = await asyncio.to_thread(
        _backfill_all,
        store,
        batch_size=batch_size,
    )

    capacity_limited = False
    while True:
        status_before = inspect_database(target.database_path)
        if status_before.get("error_type"):
            return inspection_unavailable(status_before)
        eligible_before = _eligible_chunk_count(status_before)
        if eligible_before == 0:
            break
        work_budget = _ready_vector_work_budget(
            status_before,
            batch_size=batch_size,
            max_ready_vectors=MAX_READY_VECTOR_CHUNKS,
        )
        if work_budget == 0:
            capacity_limited = True
            last_batch_state = "capacity_limited"
            break
        (
            selected,
            stored,
            failed,
            stale,
            last_batch_state,
        ) = await _run_embedding_work_round(
            store,
            work_budget=work_budget,
        )
        embedded_chunks += stored
        failed_chunks += failed
        stale_writebacks += stale
        status_after = inspect_database(target.database_path)
        if status_after.get("error_type"):
            return inspection_unavailable(status_after)
        eligible_after = _eligible_chunk_count(status_after)
        if selected == 0 or (
            stored == 0 and failed == 0 and eligible_after >= eligible_before
        ):
            break
        await asyncio.sleep(0)

    after = inspect_database(target.database_path)
    if after.get("error_type"):
        return inspection_unavailable(after)
    embedding_status = get_local_embedding_status()
    eligible_remaining = _eligible_chunk_count(after)
    result_state = (
        "capacity_limited"
        if capacity_limited and eligible_remaining > 0
        else _completion_state(after, last_batch_state=last_batch_state)
    )
    complete = result_state == "complete"
    return {
        **after,
        "action": action,
        "reset_chunks": reset_chunks,
        "backfilled_entries": backfilled_entries,
        "embedded_chunks": embedded_chunks,
        "failed_chunks_this_run": failed_chunks,
        "stale_writebacks": stale_writebacks,
        "eligible_chunks_remaining": eligible_remaining,
        "vector_budget_remaining": max(
            MAX_READY_VECTOR_CHUNKS - int(after.get("chunks_ready", 0)),
            0,
        ),
        "embedding_service_state": embedding_status.state,
        "runtime_model_id": embedding_status.model_id,
        "runtime_dimensions": embedding_status.dimensions,
        "last_batch_state": last_batch_state,
        "result_state": result_state,
        "complete": complete,
    }, complete


async def _run(args: argparse.Namespace) -> int:
    root = (args.knowledge_root or _default_knowledge_root()).expanduser().resolve()
    target = _target(root)
    requested_action = (
        "preflight"
        if args.preflight_pack
        else "cancel_job"
        if args.cancel_job
        else "discard_job"
        if args.discard_job
        else "full"
        if args.full
        else "rebuild"
        if args.rebuild
        else "status"
    )
    payload: dict[str, Any] = {
        "action": requested_action,
        "knowledge_root": str(root),
        "embedding_backend": "local_shared_runtime",
        "database": str(target.database_path),
    }
    if args.enable_local_pack:
        if requested_action not in {"rebuild", "full"}:
            payload.update(
                {
                    "ok": False,
                    "error_type": "invalid_action",
                    "reason": "enable_local_pack_requires_rebuild",
                }
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2
        pack_targets = _installed_pack_targets(target, args.enable_local_pack)
        if not pack_targets:
            payload.update(
                {
                    "ok": False,
                    "error_type": "pack_not_found",
                    "pack_id": args.enable_local_pack,
                }
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2
        payload["local_embedding_opt_in"] = {
            "pack_id": args.enable_local_pack,
            "dry_run": bool(args.dry_run),
        }
        if not args.dry_run:
            from knowledge.packs import set_pack_index_policy

            for target in pack_targets:
                set_pack_index_policy(
                    target.database_path,
                    args.enable_local_pack,
                    local_embedding_enabled=True,
                )
    if requested_action == "preflight":
        from knowledge.packs import ensure_install_capacity, load_pack, preflight_pack

        try:
            pack = load_pack(args.preflight_pack)
            preflight = preflight_pack(pack)
            ensure_install_capacity(root, preflight)
        except (OSError, ValueError) as exc:
            payload.update({"ok": False, "error_type": type(exc).__name__})
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2
        payload.update(
            {
                "ok": True,
                "pack_id": pack.pack_id,
                "material_type": pack.material_type,
                "entries_total": preflight.entries,
                "projected_chunks": preflight.projected_chunks,
                "estimated_working_bytes": preflight.estimated_working_bytes,
            }
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if requested_action == "cancel_job":
        from knowledge.pack_jobs import cancel_pack_job

        cancelled = cancel_pack_job(root, args.cancel_job)
        payload.update({"ok": cancelled, "job_id": args.cancel_job})
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if cancelled else 2
    if requested_action == "discard_job":
        from knowledge.pack_jobs import discard_degraded_pack_job

        discarded = discard_degraded_pack_job(root, args.discard_job)
        payload.update({"ok": discarded, "job_id": args.discard_job})
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if discarded else 2
    if requested_action == "status":
        payload["index"] = inspect_database(target.database_path)
        payload["pack_jobs"] = inspect_pack_jobs(root)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.dry_run:
        payload["index"] = dry_run_plan(target, full=args.full)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    from memory.local_embedding_provider import bind_process_local_embedding_provider

    bind_process_local_embedding_provider()
    all_complete = True
    try:
        result, complete = await rebuild_target(
            target,
            full=args.full,
            batch_size=args.batch_size,
        )
        payload["index"] = result
        all_complete = complete
    finally:
        from knowledge.vector_index import drain_knowledge_embedding_inference
        from utils.local_embedding_runtime import release_local_embedding_service

        await drain_knowledge_embedding_inference()
        await release_local_embedding_service()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all_complete else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

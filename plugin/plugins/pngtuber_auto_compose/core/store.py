"""SQLite persistence for PNGTuber Auto Compose.

The plugin needs to remember more than one flat job record: source images,
generated candidates, masks, expression patches, package files, and external
ComfyUI prompt runs all become first-class artifacts as the pipeline grows.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


JOB_COLUMNS = (
    "job_id",
    "status",
    "stage",
    "message",
    "mode",
    "note",
    "source_filename",
    "source_mime",
    "source_path",
    "package_path",
    "created_at",
    "updated_at",
    "qa_json",
    "metadata_json",
)


def now_ts() -> float:
    return time.time()


def _to_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _from_json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


class JobStore:
    """Small SQLite repository with dict-shaped plugin API results."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    source_filename TEXT NOT NULL DEFAULT '',
                    source_mime TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    package_path TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    qa_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    label TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    mime TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    prompt_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_artifacts_job_id ON artifacts(job_id);
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_job_id ON workflow_runs(job_id);
                """
            )

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
            return [self._inflate_job(conn, row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)).fetchone()
            if row is None:
                return None
            return self._inflate_job(conn, row)

    def save_job(self, job: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_job(job)
        placeholders = ", ".join("?" for _ in JOB_COLUMNS)
        updates = ", ".join(f"{column} = excluded.{column}" for column in JOB_COLUMNS if column != "job_id")
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO jobs ({", ".join(JOB_COLUMNS)})
                VALUES ({placeholders})
                ON CONFLICT(job_id) DO UPDATE SET {updates}
                """,
                tuple(normalized[column] for column in JOB_COLUMNS),
            )
            self.replace_artifacts(normalized["job_id"], job.get("artifacts", []), conn=conn)
        stored = self.get_job(normalized["job_id"])
        return stored if stored is not None else dict(job)

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        job.update(fields)
        job["updated_at"] = now_ts()
        return self.save_job(job)

    def delete_job(self, job_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE job_id = ?", (str(job_id),))
            return cursor.rowcount > 0

    def clear_jobs(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()
            count = int(row["count"] if row else 0)
            conn.execute("DELETE FROM jobs")
            return count

    def replace_artifacts(
        self,
        job_id: str,
        artifacts: Any,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        close_conn = conn is None
        active = conn or self._connect()
        try:
            active.execute("DELETE FROM artifacts WHERE job_id = ?", (str(job_id),))
            for artifact in artifacts if isinstance(artifacts, list) else []:
                if not isinstance(artifact, dict):
                    continue
                active.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, job_id, type, role, label, path, mime, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(artifact.get("artifact_id") or uuid.uuid4().hex),
                        str(job_id),
                        str(artifact.get("type") or ""),
                        str(artifact.get("role") or artifact.get("label") or ""),
                        str(artifact.get("label") or ""),
                        str(artifact.get("path") or ""),
                        str(artifact.get("mime") or ""),
                        _to_json(artifact.get("metadata", {})),
                        float(artifact.get("created_at") or now_ts()),
                    ),
                )
        finally:
            if close_conn:
                active.commit()
                active.close()

    def create_workflow_run(self, job_id: str, workflow_id: str, status: str = "queued", **fields: Any) -> dict[str, Any]:
        run = {
            "run_id": str(fields.get("run_id") or uuid.uuid4().hex),
            "job_id": str(job_id),
            "workflow_id": str(workflow_id),
            "status": str(status),
            "stage": str(fields.get("stage") or ""),
            "prompt_id": str(fields.get("prompt_id") or ""),
            "error": str(fields.get("error") or ""),
            "metadata_json": _to_json(fields.get("metadata", {})),
            "created_at": float(fields.get("created_at") or now_ts()),
            "updated_at": float(fields.get("updated_at") or now_ts()),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, job_id, workflow_id, status, stage, prompt_id, error,
                    metadata_json, created_at, updated_at
                ) VALUES (
                    :run_id, :job_id, :workflow_id, :status, :stage, :prompt_id, :error,
                    :metadata_json, :created_at, :updated_at
                )
                """,
                run,
            )
        return self._inflate_run(run)

    def update_workflow_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (str(run_id),)).fetchone()
            if row is None:
                return None
            run = dict(row)
            metadata = _from_json(run.get("metadata_json"), {})
            if "metadata" in fields and isinstance(fields["metadata"], dict):
                metadata.update(fields["metadata"])
            for key in ("status", "stage", "prompt_id", "error"):
                if key in fields:
                    run[key] = str(fields[key] or "")
            run["metadata_json"] = _to_json(metadata)
            run["updated_at"] = float(fields.get("updated_at") or now_ts())
            conn.execute(
                """
                UPDATE workflow_runs
                SET status = :status,
                    stage = :stage,
                    prompt_id = :prompt_id,
                    error = :error,
                    metadata_json = :metadata_json,
                    updated_at = :updated_at
                WHERE run_id = :run_id
                """,
                run,
            )
        stored = self.get_workflow_run(run_id)
        return stored

    def get_workflow_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (str(run_id),)).fetchone()
            if row is None:
                return None
            return self._inflate_run(dict(row))

    def list_workflow_runs(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE job_id = ? ORDER BY created_at DESC",
                (str(job_id),),
            ).fetchall()
            return [self._inflate_run(dict(row)) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _normalize_job(self, job: dict[str, Any]) -> dict[str, Any]:
        created_at = float(job.get("created_at") or now_ts())
        updated_at = float(job.get("updated_at") or now_ts())
        return {
            "job_id": str(job.get("job_id") or uuid.uuid4().hex[:12]),
            "status": str(job.get("status") or "queued"),
            "stage": str(job.get("stage") or "created"),
            "message": str(job.get("message") or ""),
            "mode": str(job.get("mode") or ""),
            "note": str(job.get("note") or ""),
            "source_filename": str(job.get("source_filename") or ""),
            "source_mime": str(job.get("source_mime") or ""),
            "source_path": str(job.get("source_path") or ""),
            "package_path": str(job.get("package_path") or ""),
            "created_at": created_at,
            "updated_at": updated_at,
            "qa_json": _to_json(job.get("qa", {})),
            "metadata_json": _to_json(job.get("metadata", {})),
        }

    def _inflate_job(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        job["qa"] = _from_json(job.pop("qa_json", "{}"), {})
        job["metadata"] = _from_json(job.pop("metadata_json", "{}"), {})
        artifact_rows = conn.execute(
            "SELECT * FROM artifacts WHERE job_id = ? ORDER BY created_at ASC",
            (job["job_id"],),
        ).fetchall()
        job["artifacts"] = [self._inflate_artifact(dict(artifact)) for artifact in artifact_rows]
        job["workflow_runs"] = self.list_workflow_runs(job["job_id"])
        return job

    def _inflate_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        artifact["metadata"] = _from_json(artifact.pop("metadata_json", "{}"), {})
        return artifact

    def _inflate_run(self, run: dict[str, Any]) -> dict[str, Any]:
        run["metadata"] = _from_json(run.pop("metadata_json", "{}"), {})
        return run

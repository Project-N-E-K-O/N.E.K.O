"""Durable manifests and immutable document versions; SQLite is always derived."""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import threading
from typing import Callable

from utils.file_utils import atomic_write_bytes

from . import indexing
from .embedding_compat import semantic_cosine
from .package_validate import DEFAULT_LIMITS as PACKAGE_LIMITS
from .models import LibraryError, LibraryLimits, checked_id, checked_name, digest, new_id, now
from .persistence import (
    MAX_METADATA_BYTES, CommittedWriteError, ensure_directory, sync_directory, sync_file,
    write_json as atomic_write_json,
)


class LibraryStore:
    """Own one root, with a single manifest replacement as each activation point."""

    def __init__(self, root: Path, limits: LibraryLimits, write_guard: Callable | None = None):
        self.root = Path(root)
        self.limits = limits
        self.guard = write_guard or nullcontext
        self.lock = threading.RLock()

    def initialize(self) -> None:
        """Create only domain directories under the explicit application-owned root."""
        with self.lock, self.guard():
            for name in ("libraries", "indexes", "artifacts", "jobs", "staging/previews", "jobs/snapshots"):
                ensure_directory(self.root / name)
            sync_directory(self.root.parent)

    @staticmethod
    def read(path: Path) -> dict:
        """Bound persisted JSON and fail closed instead of overwriting damaged user data."""
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_METADATA_BYTES + 1)
            if len(raw) > MAX_METADATA_BYTES:
                raise LibraryError("metadata_too_large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise LibraryError("metadata_invalid")
            return result
        except FileNotFoundError as exc:
            raise LibraryError("not_found") from exc
        except LibraryError:
            raise
        except (ValueError, UnicodeError) as exc:
            raise LibraryError("metadata_invalid") from exc

    def _path(self, library_id: str) -> Path:
        return self.root / "libraries" / checked_id(library_id) / "manifest.json"

    def manifest(self, library_id: str) -> dict:
        """Read active authority; tombstones prevent delayed work recreating a library."""
        result = self.read(self._path(library_id))
        if result.get("schema_version") != 1 or result.get("library_id") != library_id:
            raise LibraryError("metadata_invalid")
        if result.get("deleted"):
            raise LibraryError("library_deleted")
        return result

    def _write_manifest(self, manifest: dict) -> None:
        """Provide one narrow activation boundary, also used by failure-injection tests."""
        path = self._path(manifest["library_id"])
        try:
            atomic_write_json(path, manifest)
        except CommittedWriteError:
            if self.read(path) != manifest:
                raise
            # The manifest has already switched. Report the real committed
            # outcome, carrying an explicit warning only in the response/task.
            # This field is deliberately not written back into the manifest.
            manifest["warnings"] = ["storage_sync_unconfirmed"]
        # Callers hold the library lock and write guard. Cleanup starts only
        # after the real activation pointer is visible, never while preparing
        # an index or after a failed manifest replacement.
        self._prune_old_indexes(manifest["library_id"])

    @staticmethod
    def _index_link(path: Path) -> bool:
        """Treat every Windows reparse tag, including junctions, as untrusted."""
        # Python 3.11 Path.is_symlink does not cover directory junctions.
        # lstat inspects the boundary itself instead of following its target.
        return path.is_symlink() or bool(getattr(path.lstat(), "st_reparse_tag", 0))

    def _prune_old_indexes(self, library_id: str) -> None:
        """Best-effort cleanup of canonical older derived files under owned locks."""
        try:
            libraries, indexes = self.root / "libraries", self.root / "indexes"
            if self._index_link(libraries) or self._index_link(indexes):
                return
            directory = libraries / checked_id(library_id)
            pointer, index_directory = directory / "manifest.json", indexes / library_id
            if (self._index_link(directory) or self._index_link(pointer) or self._index_link(index_directory)
                    or not pointer.is_file() or not index_directory.is_dir()):
                return
            manifest = self.read(pointer)
            revision, documents = manifest.get("revision"), manifest.get("documents")
            if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
                    or manifest.get("library_id") != library_id or type(revision) is not int or revision < 0
                    or not isinstance(documents, dict) or len(documents) > self.limits.max_documents):
                return
            for identity, version in documents.items():
                checked_id(identity)
                checked_id(version)
            current = index_directory / f"{revision}.sqlite"
            if self._index_link(current) or not current.is_file():
                return  # Missing/unknown active state is not a cleanup authority.
            changed = False
            for path in index_directory.iterdir():
                if self._index_link(path) or not path.is_file() or path.suffix != ".sqlite":
                    continue
                stem = path.stem
                if not stem.isascii() or not stem.isdecimal() or str(int(stem)) != stem:
                    continue
                if int(stem) >= revision:
                    continue  # Keep active and newer uncommitted candidates.
                try:
                    path.unlink(missing_ok=True)
                    changed = True
                except OSError:
                    continue  # A busy file can be retried after the next commit/start.
            if changed:
                sync_directory(index_directory)
        except Exception:
            # Optional derived-data cleanup must neither fail a visible commit
            # nor block recovery because one manifest/file cannot be inspected.
            return

    def prune_indexes(self) -> None:
        """Retry old-index cleanup after exclusive application startup recovery."""
        try:
            with self.lock, self.guard():
                directory = self.root / "libraries"
                if self._index_link(directory) or not directory.is_dir():
                    return
                for library in directory.iterdir():
                    if not self._index_link(library) and library.is_dir():
                        self._prune_old_indexes(library.name)
        except Exception:
            return  # Maintenance/read errors affect optional cleanup, not startup.

    def list(self) -> list[dict]:
        """Return active library manifests without touching external source files."""
        with self.lock:
            return [value for path in sorted((self.root / "libraries").glob("*/manifest.json"))
                    if not (value := self.read(path)).get("deleted")]

    def create(self, name: str, *, kind: str = "local", library_id: str | None = None) -> dict:
        """Allocate stable library and work identities before any build or publication."""
        with self.lock, self.guard():
            if len(self.list()) >= self.limits.max_libraries:
                raise LibraryError("capacity_libraries")
            identifier = checked_id(library_id) if library_id else new_id()
            if self._path(identifier).exists():
                raise LibraryError("library_exists")
            result = {"schema_version": 1, "library_id": identifier, "work_id": new_id(), "name": checked_name(name),
                      "kind": kind, "revision": 0, "documents": {}, "created_at": now(), "updated_at": now()}
            self._path(identifier).parent.mkdir(parents=True)
            self._write_manifest(result)
            return result

    def rename(self, library_id: str, name: str) -> dict:
        """Rename display metadata without recomputing document vectors."""
        with self.lock, self.guard():
            manifest = self.manifest(library_id)
            manifest.update(name=checked_name(name), updated_at=now())
            self._write_manifest(manifest)
            return manifest

    def documents(self, manifest: dict) -> list[dict]:
        """Resolve immutable committed versions; durable text is the source of truth."""
        base = self._path(manifest["library_id"]).parent / "versions"
        return [self.read(base / f"{checked_id(version)}.json") for version in manifest["documents"].values()]

    def document(self, library_id: str, document_id: str) -> dict:
        """Return a committed document, including complete text and real source locations."""
        with self.lock:
            manifest = self.manifest(library_id)
            version = manifest["documents"].get(checked_id(document_id))
            if not version:
                raise LibraryError("document_not_found")
            return self.read(self._path(library_id).parent / "versions" / f"{checked_id(version)}.json")

    def stage(self, library_id: str, filename: str, data: bytes, document_id: str | None) -> dict:
        """Save the managed original before parsing; preview is not an active document."""
        if not data:
            raise LibraryError("empty_file")
        if len(data) > self.limits.max_document_bytes:
            raise LibraryError("document_too_large")
        with self.lock, self.guard():
            manifest = self.manifest(library_id)
            if manifest["kind"] != "local":
                raise LibraryError("installed_library_read_only")
            if len(list((self.root / "staging/previews").glob("*.json"))) >= 32:
                raise LibraryError("preview_capacity")
            if document_id and document_id not in manifest["documents"]:
                raise LibraryError("document_not_found")
            identifier = new_id()
            record = {"preview_id": identifier, "library_id": library_id,
                      "document_id": checked_id(document_id) if document_id else new_id(),
                      "base_version": manifest["documents"].get(document_id), "filename": str(filename)[:160],
                      "sha256": digest(data), "size": len(data), "created_at": now(), "status": "pending"}
            atomic_write_bytes(self.root / "staging/previews" / f"{identifier}.original", data)
            atomic_write_json(self.preview_path(identifier), record)
            return record

    def preview_path(self, identifier: str) -> Path:
        """Resolve only an opaque preview identity under domain staging."""
        return self.root / "staging/previews" / f"{checked_id(identifier)}.json"

    def preview(self, identifier: str) -> dict:
        """Reject previews of a deleted or replaced target before confirmation."""
        with self.lock:
            record = self.read(self.preview_path(identifier))
            manifest = self.manifest(record["library_id"])
            if record["base_version"] != manifest["documents"].get(record["document_id"]):
                raise LibraryError("version_conflict")
            return record

    def save_preview(self, identifier: str, parsed: dict, check: Callable) -> dict:
        """Drop a late parser result before it can publish a cancelled/deleted preview."""
        with self.lock, self.guard():
            check()
            record = self.preview(identifier)
            record.update(parsed, status="ready", truncated=False)
            atomic_write_json(self.preview_path(identifier), record)
            return record

    def _index_path(self, manifest: dict) -> Path:
        return self.root / "indexes" / manifest["library_id"] / f"{manifest['revision']}.sqlite"

    def _activate(self, manifest: dict, versions: list[dict], gate: Callable | None = None) -> dict:
        """Prepare immutable data and index before committing the sole active pointer."""
        if len(versions) > self.limits.max_documents:
            raise LibraryError("capacity_documents")
        if sum(len(document["content"]) for document in versions) > self.limits.max_library_chars:
            raise LibraryError("capacity_text")
        base = self._path(manifest["library_id"]).parent / "versions"
        ensure_directory(base)
        for version in versions:
            path = base / f"{checked_id(version['version_id'])}.json"
            retained = path.exists()
            if not retained:
                atomic_write_json(path, version)
            # Validate the persisted representation before switching the sole
            # activation pointer, including files left by interrupted work.
            if self.read(path) != version:
                raise LibraryError("metadata_invalid")
            if retained:
                # An earlier replacement may have survived a failed directory
                # flush. Existence and readability alone do not restore that
                # durability precondition when an interrupted task retries.
                sync_file(path)
                sync_directory(base)
        indexing.build_index(self._index_path(manifest), versions, self.limits)
        if gate:
            gate()
        # Failed metadata writes leave the previous manifest, text and vectors intact.
        self._write_manifest(manifest)
        return manifest

    def commit_document(self, preview: dict, version: dict, gate: Callable) -> dict:
        """Merge a prepared version only if its target version and library still exist."""
        with self.lock, self.guard():
            current = self.preview(preview["preview_id"])
            manifest = self.manifest(current["library_id"])
            for old in self.documents(manifest):
                if old["sha256"] == version["sha256"]:
                    return {"document_id": old["document_id"], "unchanged": True}
            originals = self._path(current["library_id"]).parent / "originals"
            originals.mkdir(exist_ok=True)
            raw = (self.root / "staging/previews" / f"{current['preview_id']}.original").read_bytes()
            if digest(raw) != current["sha256"]:
                raise LibraryError("preview_changed")
            atomic_write_bytes(originals / current["sha256"], raw)
            manifest = deepcopy(manifest)
            manifest["documents"][version["document_id"]] = version["version_id"]
            manifest.update(revision=manifest["revision"] + 1, updated_at=now())
            old = [document for document in self.documents(self.manifest(current["library_id"]))
                   if document["document_id"] != version["document_id"]]
            self._activate(manifest, [*old, version], gate)
            result = {"document_id": version["document_id"], "version_id": version["version_id"],
                      "semantic_state": version["semantic_state"], "unchanged": False}
            if manifest.get("warnings"):
                result["warnings"] = manifest["warnings"]
            return result

    def discard_preview(self, identifier: str) -> None:
        """Remove only domain-owned staging after the parser worker has finished."""
        with self.lock, self.guard():
            self.preview_path(identifier).unlink(missing_ok=True)
            (self.root / "staging/previews" / f"{checked_id(identifier)}.original").unlink(missing_ok=True)

    def delete_document(self, library_id: str, document_id: str) -> None:
        """Commit removal without deleting immutable inputs retained by in-flight tasks."""
        with self.lock, self.guard():
            manifest = deepcopy(self.manifest(library_id))
            if checked_id(document_id) not in manifest["documents"]:
                raise LibraryError("document_not_found")
            del manifest["documents"][document_id]
            manifest.update(revision=manifest["revision"] + 1, updated_at=now())
            self._activate(manifest, self.documents(manifest))

    def delete_library(self, library_id: str) -> None:
        """Persist a tombstone rather than allowing delayed tasks to recreate the root."""
        with self.lock, self.guard():
            manifest = self.manifest(library_id)
            manifest.update(deleted=True, updated_at=now())
            self._write_manifest(manifest)

    def search(self, library_id: str, query: str, vector: list | None, spec: dict | None, limit: int) -> dict:
        """Rebuild missing/damaged derived indexes from authoritative saved versions only."""
        with self.lock:
            manifest = self.manifest(library_id)
            documents = self.documents(manifest)
            path = self._index_path(manifest)
            try:
                hits = indexing.rank(path, documents, query, vector, spec, limit)
            except (OSError, sqlite3.DatabaseError):
                with self.guard():
                    indexing.build_index(path, documents, self.limits)
                hits = indexing.rank(path, documents, query, vector, spec, limit)
            total = sum(len(document["chunks"]) for document in documents)
            # A cross-dimensional prefix with zero norm is unusable, even if
            # every model identity field is compatible. Report actual rows.
            compatible = sum(semantic_cosine(chunk.get("vector"), document.get("embedding_spec"), vector, spec) is not None
                             for document in documents for chunk in document["chunks"]) if vector is not None else 0
            state = "ready" if total and compatible == total else "partial" if compatible else "unavailable"
            return {"hits": hits, "semantic_state": state}

    def freeze(self, library_id: str, version: str, source: dict, document_ids: list[str] | None = None) -> dict:
        """Persist a self-contained author snapshot immune to later document edits."""
        with self.lock, self.guard():
            manifest = self.manifest(library_id)
            documents = self.documents(manifest)
            if document_ids is not None:
                if (not isinstance(document_ids, list) or not document_ids
                        or any(not isinstance(value, str) for value in document_ids)
                        or len(set(document_ids)) != len(document_ids)
                        or not set(document_ids) <= set(manifest["documents"])):
                    raise LibraryError("invalid_document_selection")
                selected = set(document_ids)
                documents = [document for document in documents if document["document_id"] in selected]
            if not documents:
                raise LibraryError("empty_library")
            if sum(len(value["chunks"]) for value in documents) > PACKAGE_LIMITS.max_chunks:
                # Local retrieval permits a larger library. Reject only the
                # selected export scope before writing an unusable build snapshot.
                raise LibraryError("package_capacity_exceeded")
            snapshot = {"snapshot_id": new_id(), "library_id": library_id, "package_id": manifest["work_id"],
                        "name": manifest["name"], "version": version, "source": source,
                        "chunking_version": indexing.CHUNKING_VERSION,
                        "documents": [{"document_id": value["document_id"], "name": value["filename"],
                                       "version": value["version_id"], "sha256": value["sha256"],
                                       "content": value["content"]} for value in documents],
                        # Builds always encode frozen source inputs. Cached
                        # document vectors are not part of the build contract
                        # and would duplicate tens of MiB on every build click.
                        "chunks": [deepcopy({key: item for key, item in chunk.items() if key != "vector"})
                                   for value in documents for chunk in value["chunks"]],
                        "embedding_spec": documents[0].get("embedding_spec")}
            atomic_write_json(self.root / "jobs/snapshots" / f"{snapshot['snapshot_id']}.json", snapshot)
            return snapshot

    def install(self, snapshot: dict, installation: dict, library_id: str | None, gate: Callable | None,
                job_created_at: str | None = None) -> dict:
        """Atomically activate validated precomputed data, without parsing or embedding."""
        with self.lock, self.guard():
            work_id = snapshot["package_id"]
            if job_created_at:
                # An anonymous update may have been admitted before its destination
                # was deleted. Its arrival must not look like a fresh reinstall.
                for path in (self.root / "libraries").glob("*/manifest.json"):
                    prior = self.read(path)
                    if (prior.get("deleted") and prior.get("installation", {}).get("work_id") == work_id
                            and prior["updated_at"] >= job_created_at):
                        raise LibraryError("library_deleted")
            matches = [item for item in self.list() if item.get("installation", {}).get("work_id") == work_id]
            if library_id:
                manifest = self.manifest(library_id)
                if manifest.get("installation", {}).get("work_id") != work_id:
                    raise LibraryError("package_identity_mismatch")
            elif matches:
                manifest = matches[0]
            else:
                identifier = new_id()
                manifest = {"schema_version": 1, "library_id": identifier, "work_id": work_id, "name": snapshot["name"],
                            "kind": "installed", "revision": 0, "documents": {}, "created_at": now()}
                self._path(identifier).parent.mkdir(parents=True, exist_ok=True)
            history = manifest.get("installed_versions", {})
            package_version = str(installation["version"])
            previous_hash = history.get(package_version)
            if previous_hash and previous_hash != installation["artifact_sha256"]:
                raise LibraryError("immutable_version_conflict")
            if manifest.get("installation", {}).get("artifact_sha256") == installation["artifact_sha256"]:
                return manifest
            if matches and not library_id:
                # Content identity is idempotent, but another version of an
                # installed work may only replace a user-selected destination.
                # Historical version/hash conflicts above remain the stronger
                # error even when the caller omitted that destination.
                raise LibraryError("package_update_requires_target")
            versions = []
            chunks = snapshot["chunks"]
            for value in snapshot["documents"]:
                document_id = checked_id(value["document_id"])
                versions.append({"document_id": document_id, "version_id": new_id(), "filename": value["name"],
                                 "sha256": value["sha256"], "size": len(value["content"].encode()),
                                 "content": value["content"], "format": "package", "sections": [],
                                 "chunks": [chunk for chunk in chunks if chunk["document_id"] == document_id],
                                 "embedding_spec": snapshot["embedding_spec"],
                                 "semantic_state": "ready" if installation.get("semantic_available", True) else "unavailable"})
            manifest = deepcopy(manifest)
            manifest.update(name=snapshot["name"], revision=manifest["revision"] + 1, updated_at=now(),
                            documents={value["document_id"]: value["version_id"] for value in versions},
                            installation={**installation, "work_id": work_id},
                            installed_versions={**history, package_version: installation["artifact_sha256"]})
            return self._activate(manifest, versions, gate)

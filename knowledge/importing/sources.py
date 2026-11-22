"""Immutable originals and parsed versions; active v1 pack tags select versions."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from utils.file_utils import atomic_write_bytes, atomic_write_json
from ..models import KnowledgePackError, pack_id_is_valid
from ..registry import utc_now
from .documents import SourceLimits, parse_source

SIDECAR_MAX_BYTES = 8 * 1024 * 1024


def _encoded_record(value: dict) -> bytes:
    """Use the same bound for writes and reads, without indentation fan-out."""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > SIDECAR_MAX_BYTES:
        raise KnowledgePackError("document_text_too_large")
    return raw


def _id(value: str) -> str:
    """Never accept paths or arbitrary filenames as a sidecar identity."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise KnowledgePackError("invalid_request")
    return value


class SourceStore:
    """Store provenance separately from the shared SQLite vector index."""

    def __init__(self, root: Path):
        self.root = root / "sources"

    def directory(self, pack_id: str) -> Path:
        if not pack_id_is_valid(pack_id):
            raise KnowledgePackError("invalid_request")
        return self.root / pack_id

    @staticmethod
    def read(path: Path) -> dict:
        """Cap sidecar reads before decoding; vectors never belong here."""
        try:
            with path.open("rb") as stream:
                raw = stream.read(SIDECAR_MAX_BYTES + 1)
        except FileNotFoundError as error:
            raise KnowledgePackError("source_not_found") from error
        if len(raw) > SIDECAR_MAX_BYTES:
            raise KnowledgePackError("document_text_too_large")
        return json.loads(raw)

    def preview(self, filename: str, raw: bytes | Path, *, pack_id: str = "", doc_id: str = "", name: str = "") -> dict:
        """Retain exact retry input and a digest-bound, complete text preview."""
        if isinstance(raw, Path):
            # Only internal request spools reach this worker path. Bound the
            # read before parsing and avoid an event-loop bytearray/bytes copy.
            with raw.open("rb") as stream:
                raw = stream.read(SourceLimits().max_document_bytes + 1)
        parsed = parse_source(filename, raw, SourceLimits())
        # Preserve whether the user selected an existing target before a new
        # preview's generated ID makes the two creation paths look identical.
        requires_existing_pack = bool(pack_id)
        pack_id = pack_id or "imp." + uuid.uuid4().hex
        doc_id = _id(doc_id) if doc_id else uuid.uuid4().hex
        folder = self.directory(pack_id) / "previews"
        folder.mkdir(parents=True, exist_ok=True)
        preview_id = uuid.uuid4().hex
        preview = {"preview_id": preview_id, "pack_id": pack_id, "doc_id": doc_id,
                   "requires_existing_pack": requires_existing_pack,
                   "name": parsed["filename"], "pack_name": (name or parsed["filename"])[:200],
                   "sha256": hashlib.sha256(raw).hexdigest(), "content": parsed["content"],
                   "sections": parsed["sections"], "chars": len(parsed["content"]), "created_at": utc_now()}
        encoded = _encoded_record(preview)
        atomic_write_bytes(folder / (preview_id + ".original"), raw)
        atomic_write_bytes(folder / (preview_id + ".json"), encoded)
        return preview

    def find_preview(self, preview_id: str) -> dict:
        """Resolve only a generated preview identity, not a caller-provided path."""
        identity = _id(preview_id)
        for path in self.root.glob(f"*/previews/{identity}.json"):
            return self.read(path)
        raise KnowledgePackError("preview_not_found")

    def discard_preview(self, preview_id: str) -> bool:
        """Delete one cancelled/replaced preview's own two files.

        A preview already frozen into a version stays: retries of that commit
        still resolve it. Callers serialize this with ``retain_preview``.
        """
        identity = _id(preview_id)
        for path in self.root.glob(f"*/previews/{identity}.json"):
            pack_folder = path.parent.parent
            try:
                preview = self.read(path)
                versions = self.versions(preview["pack_id"], preview["doc_id"])
            except (KnowledgePackError, ValueError, KeyError, TypeError):
                # An unreadable preview record must still be deletable; only
                # its pack's own version records can show that it was frozen.
                versions = [self.read(item) for item in (pack_folder / "versions").glob("*.json")]
            if any(value.get("preview_id") == identity for value in versions):
                return False
            # The descriptive JSON goes last, so an interrupted delete can be
            # repeated by the same identity instead of orphaning the original.
            path.with_suffix(".original").unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            if not (pack_folder / "manifest.json").exists():
                # A never-committed new pack existed only for previews. Remove
                # its now-empty folders; a non-empty folder is left untouched.
                for folder in (path.parent, pack_folder):
                    try:
                        folder.rmdir()
                    except OSError:
                        break
            return True
        return False

    def retain_preview(self, preview_id: str, sha256: str) -> dict:
        """Write an immutable version before scheduling its ordinary pack import."""
        preview = self.find_preview(preview_id)
        if preview["sha256"] != sha256:
            raise KnowledgePackError("preview_changed")
        folder = self.directory(preview["pack_id"])
        versions = self.versions(preview["pack_id"], preview["doc_id"])
        # One frozen preview is one immutable version, including retries after
        # admission failure. Never manufacture a new revision on double-click.
        for existing in versions:
            if existing.get("preview_id") == preview_id and existing.get("sha256") == sha256:
                # A previous attempt may have written the immutable version
                # but failed before its descriptive manifest was durable.
                self._retain_manifest(preview)
                return existing
        version = max((value["version"] for value in versions), default=0) + 1
        original = folder / "previews" / (preview_id + ".original")
        try:
            with original.open("rb") as stream:
                raw = stream.read(SourceLimits().max_document_bytes + 1)
        except FileNotFoundError as error:
            # An interrupted discard removes the original before its JSON.
            raise KnowledgePackError("preview_not_found") from error
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise KnowledgePackError("preview_changed")
        value = {**preview, "version": version}
        encoded = _encoded_record(value)
        target = folder / "originals" / preview["doc_id"] / f"v{version}" / "original"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, raw)
        output = folder / "versions" / f"{preview['doc_id']}.v{version}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(output, encoded)
        # This is descriptive history only. Active versions are selected by
        # the committed pack's doc:/ver: tags, so a failed job cannot activate it.
        self._retain_manifest(preview)
        return value

    def _retain_manifest(self, preview: dict) -> None:
        """Keep one preview's history discoverable before queue admission."""
        manifest_path = self.directory(preview["pack_id"]) / "manifest.json"
        try:
            previous = self.read(manifest_path)
        except KnowledgePackError as error:
            if error.reason != "source_not_found":
                raise
            previous = {}
        # History visibility changes only with explicit removal/activation,
        # never merely because a preview was retained before queue admission.
        retained = previous.get("retained", False) if isinstance(previous, dict) else False
        atomic_write_json(manifest_path, {"pack_id": preview["pack_id"], "name": preview["pack_name"], "retained": retained})

    def versions(self, pack_id: str, doc_id: str) -> list[dict]:
        identity = _id(doc_id)
        return sorted((self.read(path) for path in (self.directory(pack_id) / "versions").glob(f"{identity}.v*.json")),
                      key=lambda value: value["version"])

    def version(self, pack_id: str, doc_id: str, version: int) -> dict:
        if type(version) is not int or version < 1:
            raise KnowledgePackError("invalid_request")
        path = self.directory(pack_id) / "versions" / f"{_id(doc_id)}.v{version}.json"
        if not path.is_file():
            raise KnowledgePackError("source_version_not_found")
        return self.read(path)

    def name(self, pack_id: str) -> str:
        return self.read(self.directory(pack_id) / "manifest.json")["name"]

    def has_history(self, pack_id: str) -> bool:
        """Origin labels do not prove that this device owns editable originals."""
        folder = self.directory(pack_id)
        try:
            manifest = self.read(folder / "manifest.json")
            return (isinstance(manifest, dict) and manifest.get("pack_id") == pack_id
                    and any((folder / "versions").glob("*.json"))
                    and any((folder / "originals").glob("*/*/original")))
        except (KnowledgePackError, OSError, ValueError):
            return False

    def all_documents(self, pack_id: str) -> list[dict]:
        """Return the newest retained version of every known document."""
        identities = {path.name.split(".")[0] for path in (self.directory(pack_id) / "versions").glob("*.json")}
        return [versions[-1] for identity in sorted(identities)
                if (versions := self.versions(pack_id, identity))]

    def retained_packs(self) -> list[dict]:
        """Only explicitly retained empty groups remain in the pack browser."""
        values = []
        for path in self.root.glob("*/manifest.json"):
            try:
                value = self.read(path)
                if (isinstance(value, dict) and value.get("retained") is True
                        and pack_id_is_valid(value.get("pack_id"))
                        and isinstance(value.get("name"), str) and value["name"].strip()):
                    values.append(value)
            except (KnowledgePackError, ValueError):
                continue
        return values

    def set_retained(self, pack_id: str, retained: bool) -> None:
        """Change history-row visibility without deleting originals or versions."""
        path = self.directory(pack_id) / "manifest.json"
        try:
            value = self.read(path)
        except KnowledgePackError as error:
            if error.reason == "source_not_found":
                return  # Ordinary installed packages have no source sidecar.
            if not retained:
                return
            raise
        except (OSError, ValueError):
            # Hiding a removed pack must not depend on damaged unrelated
            # history metadata. Retaining editable history still fails closed.
            if not retained:
                return
            raise
        if not isinstance(value, dict):
            if not retained:
                return
            raise KnowledgePackError("invalid_request")
        if value.get("retained", False) != retained:
            atomic_write_json(path, {**value, "retained": retained})

    def documents(self, pack, pack_id: str) -> list[dict]:
        """Recover source selection from the sole committed pack authority."""
        selected = {}
        for entry in pack.entries if pack is not None else ():
            docs = [tag[4:] for tag in entry.tags if tag.startswith("doc:")]
            versions = [tag[4:] for tag in entry.tags if tag.startswith("ver:")]
            if len(docs) == len(versions) == 1 and versions[0].isdigit():
                selected[docs[0]] = int(versions[0])
        return [self.version(pack_id, doc_id, version) for doc_id, version in selected.items()]

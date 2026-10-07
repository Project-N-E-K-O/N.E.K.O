"""Scoped local references for remote voices, without vendor routing knowledge."""

import asyncio
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

from config import DEFAULT_CONFIG_DATA
from utils.voice_config import is_imported_voice_ref

VOICE_STORAGE_LOCK = threading.RLock()
REMOTE_VOICE_BUCKET_PREFIX = "__REMOTE_VOICES__"


@dataclass(frozen=True)
class ImportedVoiceWriteResult:
    applied: bool
    record: dict


def voice_storage_transaction(function):
    """Serialize all read/modify/write operations in the voice library."""
    @wraps(function)
    def transaction(self, *args, **kwargs):
        with VOICE_STORAGE_LOCK:
            return function(self, *args, **kwargs)
    return transaction


class ImportedVoiceStorageMixin:
    def _load_voice_storage_for_write(self):
        # In-memory/storage-backend subclasses remain a supported seam. The
        # normal JSON implementation must not turn corruption into an empty DB.
        from .voice_storage import VoiceStorageMixin
        if type(self).load_voice_storage is not VoiceStorageMixin.load_voice_storage:
            storage = self.load_voice_storage()
        else:
            try:
                storage = self.load_json_config("voice_storage.json")
            except FileNotFoundError:
                storage = deepcopy(DEFAULT_CONFIG_DATA["voice_storage.json"])
        if not isinstance(storage, dict) or any(
            not isinstance(bucket, dict) for bucket in storage.values()
        ):
            raise ValueError("VOICE_STORAGE_INVALID")
        return storage

    def _imported_voice_scope_is_active(self, metadata):
        return self._current_imported_scope(metadata.get("provider"), metadata) == metadata.get("scope_id")

    def _current_imported_scope(self, provider, voice_data=None):
        from utils.voice_management.providers import get_adapter
        try:
            runtime = get_adapter(provider).resolve_runtime(self, voice_data=voice_data)
            return runtime.scope_id if runtime.api_key else None
        except Exception:
            return None

    @staticmethod
    def _find_imported_voice(storage, local_ref):
        if not is_imported_voice_ref(local_ref):
            return None
        for bucket_key, bucket in storage.items():
            if not bucket_key.startswith(REMOTE_VOICE_BUCKET_PREFIX) or not isinstance(bucket, dict):
                continue
            metadata = bucket.get(local_ref)
            if isinstance(metadata, dict) and (
                metadata.get("origin") == "import"
                and bucket_key == REMOTE_VOICE_BUCKET_PREFIX + str(metadata.get("scope_id", ""))
            ):
                return bucket_key, metadata
        return None

    def get_imported_voice(self, local_ref, include_inactive=False):
        storage = self.load_voice_storage()
        found = self._find_imported_voice(storage, local_ref)
        if not found:
            return None
        metadata = deepcopy(found[1])
        active = self._imported_voice_scope_is_active(metadata)
        if not active and not include_inactive:
            return None
        metadata["availability"] = "available" if active else "unavailable"
        return metadata

    def _imported_voices_from_storage(self, storage, include_inactive=False):
        result = {}
        active_scopes = {}
        for bucket_key, bucket in storage.items():
            if not bucket_key.startswith(REMOTE_VOICE_BUCKET_PREFIX) or not isinstance(bucket, dict):
                continue
            for local_ref, metadata in bucket.items():
                if not is_imported_voice_ref(local_ref) or not isinstance(metadata, dict):
                    continue
                if metadata.get("origin") != "import" or bucket_key != (
                    REMOTE_VOICE_BUCKET_PREFIX + str(metadata.get("scope_id", ""))
                ):
                    continue
                provider = metadata.get("provider")
                identity = (provider, metadata.get("scope_id"))
                if identity not in active_scopes:
                    active_scopes[identity] = self._current_imported_scope(provider, metadata)
                active = active_scopes[identity] == metadata.get("scope_id")
                if active or include_inactive:
                    value = deepcopy(metadata)
                    value["availability"] = "available" if active else "unavailable"
                    result[local_ref] = value
        return result

    @voice_storage_transaction
    def import_remote_voice(self, scope_id, provider, remote_voice_id, voice_data):
        if not scope_id or not provider or not remote_voice_id:
            raise ValueError("VOICE_IMPORT_INVALID")
        storage = self._load_voice_storage_for_write()
        identity = {"scope_id": scope_id, "provider": provider}
        if not self._imported_voice_scope_is_active(identity):
            raise ValueError("VOICE_CONTEXT_CHANGED")
        bucket = storage.setdefault(REMOTE_VOICE_BUCKET_PREFIX + scope_id, {})
        for local_ref, metadata in bucket.items():
            if isinstance(metadata, dict) and (
                metadata.get("provider") == provider
                and metadata.get("remote_voice_id") == remote_voice_id
                and metadata.get("origin") == "import"
            ):
                return local_ref, deepcopy(metadata), False
        local_ref = "voice_" + uuid4().hex
        while any(local_ref in existing_bucket for existing_bucket in storage.values()):
            local_ref = "voice_" + uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        metadata = deepcopy(voice_data)
        metadata.update({
            "local_ref": local_ref, "scope_id": scope_id, "provider": provider,
            "remote_voice_id": remote_voice_id, "source": "clone", "origin": "import",
            "imported_at": now, "created_at": now,
        })
        metadata.setdefault("remote_created_at", None)
        bucket[local_ref] = metadata
        self.save_voice_storage(storage)
        return local_ref, deepcopy(metadata), True

    async def aimport_remote_voice(self, scope_id, provider, remote_voice_id, voice_data):
        return await asyncio.to_thread(
            self.import_remote_voice, scope_id, provider, remote_voice_id, voice_data,
        )

    @voice_storage_transaction
    def update_imported_voice(
        self, local_ref, scope_id, voice_data, *, expected_operation_id=None, expected_record_revision=None,
        return_receipt=False,
    ):
        """Update an owned record; a stale conditional refresh returns the stored winner."""
        storage = self._load_voice_storage_for_write()
        found = self._find_imported_voice(storage, local_ref)
        if not found or found[1].get("scope_id") != scope_id:
            raise ValueError("VOICE_CONTEXT_CHANGED")
        if expected_operation_id is not None and (
            found[1].get("overwrite_operation_id", "") != expected_operation_id
        ):
            raise ValueError("VOICE_CONTEXT_CHANGED")
        revision = found[1].get("_record_revision", 0)
        if type(revision) is not int or revision < 0:
            raise ValueError("VOICE_STORAGE_INVALID")
        if expected_record_revision is not None and revision != expected_record_revision:
            # Scope and operation ownership were checked first. Return only an
            # already persisted record, never the rejected upstream observation.
            winner = deepcopy(found[1])
            return ImportedVoiceWriteResult(False, winner) if return_receipt else winner
        metadata = deepcopy(found[1])
        immutable = {
            "local_ref", "scope_id", "provider", "remote_voice_id", "source",
            "origin", "imported_at", "created_at", "_record_revision",
        }
        metadata.update({key: deepcopy(value) for key, value in voice_data.items() if key not in immutable})
        # Even a no-op write invalidates earlier observations. Missing on old
        # records means zero; this counter is independent of vendor revisions.
        metadata["_record_revision"] = revision + 1
        storage[found[0]][local_ref] = metadata
        self.save_voice_storage(storage)
        saved = deepcopy(metadata)
        return ImportedVoiceWriteResult(True, saved) if return_receipt else saved

    async def aupdate_imported_voice(
        self, local_ref, scope_id, voice_data, *, expected_operation_id=None, expected_record_revision=None,
        return_receipt=False,
    ):
        return await asyncio.to_thread(
            self.update_imported_voice, local_ref, scope_id, voice_data,
            expected_operation_id=expected_operation_id,
            expected_record_revision=expected_record_revision,
            return_receipt=return_receipt,
        )

    @voice_storage_transaction
    def delete_imported_voice(self, local_ref):
        storage = self._load_voice_storage_for_write()
        found = self._find_imported_voice(storage, local_ref)
        if not found:
            return False
        # Retain the operation owner until remote completion/failure is proven.
        # Otherwise reimport creates a fresh UUID and bypasses the pending guard.
        if found[1].get("overwrite_status") in {"processing", "unknown"}:
            raise ValueError("VOICE_OPERATION_IN_PROGRESS")
        del storage[found[0]][local_ref]
        self.save_voice_storage(storage)
        return True

    async def adelete_imported_voice(self, local_ref):
        return await asyncio.to_thread(self.delete_imported_voice, local_ref)

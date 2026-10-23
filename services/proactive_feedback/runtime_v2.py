"""Versioned control-first delivery; legacy transport remains independently readable."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import sqlite3
import threading
import time
import uuid

import httpx

from services.proactive_feedback import host
from services.proactive_feedback.protocol import canonical_bytes, valid_receipt
from services.proactive_feedback.runtime import ProactiveFeedbackRuntime, SettingsAcknowledgementPending, SyncFailure
from portalocker.exceptions import LockException

BINDING_KEYS = ("account_incarnation", "clear_generation", "registration_id", "registration_version")
MAX_WIRE_SEQUENCE = 2**53 - 1


def _wire_text(value, maximum):
    return isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value


def _checked_anchor(value):
    if (not isinstance(value, dict) or not _wire_text(value.get("anchor_id"), 100)
            or not _wire_text(value.get("anchor_token"), 8192)):
        raise SyncFailure("invalid_anchor")
    return value


class ProactiveFeedbackRuntimeV2(ProactiveFeedbackRuntime):
    def __init__(self, *args, **kwargs):
        self.current_settings_provider = kwargs.pop("current_settings_provider", None)
        super().__init__(*args, **kwargs)
        self._control = None
        self._native_anchor = None
        self._clock_session = str(uuid.uuid4())
        self._control_state = "initializing"
        self._has_v2_receipt = False

    @staticmethod
    def _tick():
        return time.monotonic_ns() // 1_000_000

    def control_snapshot(self, owner):
        control, anchor = self._control, self._native_anchor
        if self._closing or not control or control.get("owner_key") != owner.get("key") or not anchor:
            return None
        return {**deepcopy(control), "version": "desktop-feedback-capture-v2",
                "capture": {"clock_session_id": self._clock_session, "tick_ms": self._tick(),
                            "anchor_id": anchor["anchor_id"]},
                "time_proof": {"anchor_token": anchor["anchor_token"],
                               "send_tick_ms": anchor["send_tick_ms"],
                               "receive_tick_ms": anchor["receive_tick_ms"]}}

    async def _session(self):
        saved = await self._snapshot()
        owner = host.owner_from_snapshot(saved)
        if not owner["user_id"]:
            raise SyncFailure("authentication_required")
        if owner["community"] != self.origin:
            raise SyncFailure("community_origin_mismatch", permanent=True)
        async with asyncio.timeout(30):
            resolved = await self.session_provider()
        await self._guard(owner)
        snapshot = resolved.get("snapshot") if isinstance(resolved, dict) else None
        if not isinstance(resolved, dict) or not resolved.get("logged_in") or host.owner_from_snapshot(snapshot) != owner:
            raise SyncFailure("authentication_required")
        token = (snapshot or {}).get("access_token")
        if not isinstance(token, str) or not token:
            raise SyncFailure("authentication_required")
        identity = await self._json_request("GET", "/api/users/me", token, owner)
        user = identity.get("user") if isinstance(identity, dict) else None
        if not isinstance(user, dict) or str(user.get("id")) != str(owner["user_id"]):
            raise SyncFailure("account_mismatch", permanent=True)
        await self._guard(owner, token)
        return owner, token

    async def _refresh_control(self, owner, token):
        state = await self._json_request("GET", "/api/proactive/feedback/v2/control", token, owner)
        if (not isinstance(state, dict) or not isinstance(state.get("registrations"), dict)
                or not _wire_text(state.get("account_incarnation"), 128)
                or type(state.get("clear_generation")) is not int
                or not 0 <= state["clear_generation"] <= MAX_WIRE_SEQUENCE):
            raise SyncFailure("invalid_control")
        installation = await asyncio.to_thread(self.store.storage_incarnation, owner)
        registration = next(((key, value) for key, value in state["registrations"].items()
                             if isinstance(value, dict) and value.get("installation_id") == installation), None)
        if registration is None:
            self._control, self._native_anchor = None, None
            self._control_state = "device_activation_required"
            return False
        key, value = registration
        if (not _wire_text(key, 100) or type(value.get("version")) is not int
                or not 1 <= value["version"] <= MAX_WIRE_SEQUENCE or type(value.get("active")) is not bool):
            raise SyncFailure("invalid_control")
        previous = await asyncio.to_thread(self.store.load_control, owner)
        binding = {"account_incarnation": state["account_incarnation"],
                   "clear_generation": state["clear_generation"], "registration_id": key,
                   "registration_version": value["version"]}
        changed = not previous or any(previous.get(name) != binding[name] for name in BINDING_KEYS)
        revision = (previous or {}).get("binding_revision", 0) + int(changed)
        control = {"owner_key": owner["key"], **binding, "binding_revision": revision}
        await self._guard(owner, token)
        # This is a qualification transition only, not an event capture. Stored
        # original contexts retain their birth/capture and cannot be rebound.
        await asyncio.to_thread(self._apply_control, owner, control)
        self._control = control
        if changed:
            self._native_anchor = None
            self._has_v2_receipt = False
            self._retry_at.pop(owner["key"], None)
            self._failures.pop(owner["key"], None)
        if value.get("active") is not True:
            self._native_anchor = None
            self._control_state = "registration_removed"
            return False
        sent = self._tick()
        anchor = await self._json_request("POST", "/api/proactive/feedback/v2/anchors", token, owner,
                                          body=canonical_bytes({"binding": binding,
                                                                "clock_session_id": self._clock_session}))
        received = self._tick()
        _checked_anchor(anchor)
        self._native_anchor = {**anchor, "send_tick_ms": sent, "receive_tick_ms": received}
        self._control_state = "ready"
        self._verified_sessions[owner["key"]] = token
        if self._errors.get(owner["key"]) == "upgrade_required":
            self._errors.pop(owner["key"], None)
        return True

    def _capture_current_settings(self, owner):
        with self.attribution_context() as current_owner:
            if self._closing or current_owner != owner:
                raise SyncFailure("session_changed")
            if self.current_settings_provider is None or not self.store.current_settings_baseline_needed(owner):
                return
            value = self.current_settings_provider(owner)
            if self._closing:
                raise SyncFailure("closing")
            self.store.ensure_current_settings_baseline(owner, value["payload"], value["context"])

    def _apply_control(self, owner, control):
        with self.attribution_context() as current_owner:
            if self._closing or current_owner != owner:
                raise SyncFailure("session_changed")
            return self.store.apply_control(owner, control)

    def _record_failure(self, code, owner=None):
        self._control_state = code
        if code == "upgrade_required":
            self._native_anchor = None
        if code in {"authentication_required", "account_mismatch", "session_changed", "closing"}:
            self._control, self._native_anchor = None, None
            if owner is None:
                self._verified_sessions.clear()
            else:
                self._verified_sessions.pop(owner["key"], None)
        if owner is not None:
            self._errors[owner["key"]] = code

    async def capture_context(self, clock_session_id):
        uuid.UUID(clock_session_id)
        async with self._lock:
            owner = None
            try:
                owner, token = await self._session()
                if not await self._refresh_control(owner, token):
                    raise SyncFailure(self._control_state)
                binding = {key: self._control[key] for key in BINDING_KEYS}
                anchor = await self._json_request("POST", "/api/proactive/feedback/v2/anchors", token, owner,
                                                  body=canonical_bytes({"binding": binding,
                                                                        "clock_session_id": clock_session_id}))
                _checked_anchor(anchor)
                return {**self._control, **anchor}
            except SyncFailure as error:
                self._record_failure(error.code, owner)
                raise
            except (OSError, sqlite3.Error, httpx.HTTPError, TimeoutError):
                self._record_failure("cloud_unavailable", owner)
                raise

    async def activate_device(self, replace_registration_id=None, display_name=None, expected_owner_key=None, *,
                              expected_replace_registration_version=None, activation_context=None):
        """Only a user-triggered local endpoint calls this, never sync_once."""
        if replace_registration_id is not None:
            replace_registration_id = str(uuid.UUID(replace_registration_id))
        if ((replace_registration_id is None) != (expected_replace_registration_version is None)
                or (expected_replace_registration_version is not None
                    and (type(expected_replace_registration_version) is not int
                         or not 1 <= expected_replace_registration_version <= MAX_WIRE_SEQUENCE))):
            raise SyncFailure("cloud_conflict")
        async with self._lock:
            owner = None
            try:
                owner, token = await self._session()
                if expected_owner_key is not None and owner["key"] != expected_owner_key:
                    raise SyncFailure("session_changed")
                installation = await asyncio.to_thread(self.store.storage_incarnation, owner)
                if activation_context is None:
                    view = await self._json_request("GET", "/api/proactive/feedback/v2/devices", token, owner)
                    activation_context = view.get("activation_context") if isinstance(view, dict) else None
                if not _wire_text(activation_context, 2048):
                    raise SyncFailure("invalid_control")
                command = {"installation_id": installation, "replace_registration_id": replace_registration_id,
                           "activation_context": activation_context}
                if replace_registration_id is not None:
                    command["expected_replace_registration_version"] = expected_replace_registration_version
                if display_name:
                    command["display_name"] = display_name
                result = await self._json_request("POST", "/api/proactive/feedback/v2/devices/activate", token, owner,
                                                  body=canonical_bytes(command))
                await self._refresh_control(owner, token)
                self._wake.set()
                return result
            except SyncFailure as error:
                self._record_failure(error.code, owner)
                raise
            except (OSError, sqlite3.Error, httpx.HTTPError, TimeoutError):
                self._record_failure("cloud_unavailable", owner)
                raise

    async def list_devices(self):
        async with self._lock:
            owner = None
            try:
                owner, token = await self._session()
                value = await self._json_request("GET", "/api/proactive/feedback/v2/devices", token, owner)
                if (not isinstance(value, dict) or not isinstance(value.get("devices"), list)
                        or len(value["devices"]) > 32 or value.get("max_active_devices") != 5):
                    raise SyncFailure("invalid_control")
                installation = await asyncio.to_thread(self.store.storage_incarnation, owner)
                devices = []
                for row in value["devices"]:
                    if (not isinstance(row, dict) or not isinstance(row.get("registration_id"), str)
                            or type(row.get("registration_version")) is not int or type(row.get("active")) is not bool):
                        raise SyncFailure("invalid_control")
                    devices.append({**row, "is_current": row.get("installation_id") == installation})
                await self._guard(owner, token)
                return {**value, "devices": devices, "owner_key": owner["key"]}
            except SyncFailure as error:
                self._record_failure(error.code, owner)
                raise
            except (OSError, sqlite3.Error, httpx.HTTPError, TimeoutError):
                self._record_failure("cloud_unavailable", owner)
                raise

    async def remove_device(self, registration_id, expected_registration_version, expected_owner_key=None):
        registration_id = str(uuid.UUID(registration_id))
        async with self._lock:
            owner = None
            try:
                owner, token = await self._session()
                if expected_owner_key is not None and owner["key"] != expected_owner_key:
                    raise SyncFailure("session_changed")
                value = await self._json_request("POST", f"/api/proactive/feedback/v2/devices/{registration_id}/remove", token, owner,
                                                 body=canonical_bytes({"expected_registration_version": expected_registration_version}))
                await self._refresh_control(owner, token)
                self._wake.set()
                return value
            except SyncFailure as error:
                self._record_failure(error.code, owner)
                raise
            except (OSError, sqlite3.Error, httpx.HTTPError, TimeoutError):
                self._record_failure("cloud_unavailable", owner)
                raise

    def _commit_batch_error(self, owner, token, batch, failure, cancelled):
        with self.attribution_context() as current_owner:
            snapshot = self.fenced_snapshot_provider()
            if self._closing or cancelled.is_set():
                raise SyncFailure("closing")
            if (current_owner != owner or host.owner_from_snapshot(snapshot) != owner
                    or (snapshot or {}).get("access_token") != token):
                raise SyncFailure("session_changed")
            if not self.store.mark_batch_error(owner, batch["batch_id"], failure.code, failure.permanent):
                raise SyncFailure("session_changed")

    async def sync_once(self):
        async with self._lock:
            if self._closing:
                return {"state": "stopped"}
            batch, owner = None, None
            try:
                owner, token = await self._session()
                # Always refresh control, including an empty/paused/blocked
                # telemetry queue. Journal import and freezing happen later.
                if not await self._refresh_control(owner, token):
                    return {"state": self._control_state}
                if self.upload_paused:
                    return {"state": "paused", "error_code": "upload_paused"}
                if self.settings_drain is not None:
                    async with self._settings_lock:
                        try:
                            await asyncio.to_thread(self.settings_drain, self.store)
                            self._settings_import_error = ""
                        except SettingsAcknowledgementPending:
                            self._settings_import_error = "settings_import_unavailable"
                            await self._guard(owner, token)
                            return {"state": "retry_wait", "error_code": self._settings_import_error}
                        except (OSError, sqlite3.Error, ValueError, LockException):
                            self._settings_import_error = "settings_import_unavailable"
                if self.current_settings_provider is not None:
                    async with self._settings_lock:
                        try:
                            await asyncio.to_thread(self._capture_current_settings, owner)
                        except (OSError, sqlite3.Error, ValueError, LockException):
                            self._settings_import_error = "settings_import_unavailable"
                status = await asyncio.to_thread(self.store.sync_status, owner)
                await self._guard(owner, token)
                if status["blocked"] and status["last_error"] == "upgrade_required":
                    # Reaching here proves the v2 control/anchor handshake now
                    # works. Release only this diagnosed upgrade block, with
                    # the same credential/cancellation fence as explicit retry.
                    cancelled = threading.Event()
                    try:
                        await asyncio.to_thread(self._resume_commit, owner, token,
                            status["batch_id"], status["batch_hash"], cancelled, expected_error="upgrade_required")
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise
                    await self._guard(owner, token)
                    self._retry_at.pop(owner["key"], None)
                    self._failures.pop(owner["key"], None)
                    self._errors.pop(owner["key"], None)
                    status["blocked"] = False
                if status["blocked"]:
                    return {"state": "blocked", "error_code": status["last_error"]}
                if self._retry_at.get(owner["key"], 0) > self.clock():
                    return {"state": "retry_wait", "error_code": self._errors.get(owner["key"], "retry_wait")}
                batch = await asyncio.to_thread(self.store.prepare_batch, owner, 100)
                if batch is None and await asyncio.to_thread(self.store.retry_deferred, owner):
                    batch = await asyncio.to_thread(self.store.prepare_batch, owner, 100)
                if batch is None:
                    return {"state": "idle"}
                await self._guard(owner, token)
                receipt = await self._json_request("POST", "/api/proactive/feedback/v2/batches", token, owner,
                                                   body=await asyncio.to_thread(canonical_bytes, batch))
                if not await asyncio.to_thread(valid_receipt, batch, receipt):
                    raise SyncFailure("invalid_receipt")
                if not await asyncio.to_thread(self._commit_receipt, owner, receipt):
                    raise SyncFailure("session_changed")
                self._has_v2_receipt = True
                self._errors.pop(owner["key"], None)
                self._failures.pop(owner["key"], None)
                self._retry_at.pop(owner["key"], None)
                return {"state": "synced", "batch_id": batch["batch_id"], "items": len(batch["items"])}
            except SyncFailure as error:
                failure = error
            except (OSError, sqlite3.Error, httpx.HTTPError, TimeoutError, ValueError):
                failure = SyncFailure("cloud_unavailable")
            code = failure.code
            if code in {"closing", "session_changed"}:
                self._record_failure(code, owner)
                return {"state": "stopped" if code == "closing" else "session_changed", "error_code": code}
            if batch is not None:
                cancelled = threading.Event()
                try:
                    await asyncio.to_thread(self._commit_batch_error, owner, token, batch, failure, cancelled)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
                except SyncFailure as error:
                    self._record_failure(error.code, owner)
                    return {"state": "stopped" if error.code == "closing" else "session_changed", "error_code": error.code}
                except (OSError, sqlite3.Error):
                    failure = SyncFailure("cloud_unavailable")
                    code = failure.code
                count = max(self._failures.get(owner["key"], 0), status["attempts"]) + 1
                self._failures[owner["key"]] = count
                self._retry_at[owner["key"]] = self.clock() + min(900, 30 * 2 ** min(count - 1, 5))
            self._record_failure(code, owner)
            state = "upgrade_required" if code == "upgrade_required" else "blocked" if failure.permanent and batch is not None else "retry_wait"
            return {"state": state, "error_code": code}

    async def status(self):
        from utils.preferences import settings_capture_status
        # This diagnostic covers all local accounts. Read it before the
        # parent takes its account snapshot and performs its final identity
        # fence; no awaited diagnostic may outlive that check.
        settings_status = await asyncio.to_thread(settings_capture_status)
        result = await super().status()
        has_receipt = result.get("acked", 0) > 0
        return {**result, "protocol_version": 2, "control_state": self._control_state,
                "statistics_incomplete": bool(result.get("statistics_incomplete")
                                              or settings_status["statistics_incomplete"]),
                "has_cloud_receipt": has_receipt,
                "upgrade_required": self._control_state == "upgrade_required" or result.get("upgrade_required", False),
                "fully_synced": result["local_queue_empty"] and result["identity_verified"]
                and self._control_state == "ready"
                and has_receipt and not self.upload_paused}

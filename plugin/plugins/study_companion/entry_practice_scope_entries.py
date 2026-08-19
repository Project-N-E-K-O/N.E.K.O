from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from .entry_common import (
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    _plugin_lock,
    asyncio,
    plugin_entry,
    tr,
    ui,
)
from .models import PracticeScopeV1
from .practice_scope import PracticeScopeError, build_practice_scope


_MAX_PRACTICE_SCOPE_TOPICS = 5000
_PRACTICE_SCOPE_INPUT_PROPERTIES = {
    "schema_version": {"type": "integer", "default": 1},
    "mode": {
        "type": "string",
        "enum": ["explicit_scope", "explicit_topic"],
        "default": "explicit_scope",
    },
    "stage": {"type": "string", "default": ""},
    "subject": {"type": "string", "default": ""},
    "course_family": {"type": "string", "default": ""},
    "chapter": {"type": "string", "default": ""},
    "unit": {"type": "string", "default": ""},
    "topic_id": {"type": "string", "default": ""},
}


def _scope_request_from_state(scope: Mapping[str, object]) -> dict[str, object]:
    return {
        key: scope.get(key)
        for key in _PRACTICE_SCOPE_INPUT_PROPERTIES
        if key in scope
    }


class _PracticeScopeEntriesMixin:
    def _practice_scope_write_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_active_practice_scope_write_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._active_practice_scope_write_lock = lock
        return lock

    def _practice_scope_topics(self, requested_scope: Mapping[str, object]) -> list[dict]:
        mode = str(requested_scope.get("mode") or "explicit_scope").strip().lower()
        if mode.replace("-", "_") == "explicit_topic":
            topic = self._store.get_topic(str(requested_scope.get("topic_id") or ""))
            return [topic] if topic is not None else []
        stage = str(requested_scope.get("stage") or "").strip().lower().replace("-", "_")
        subject = str(requested_scope.get("subject") or "").strip().lower().replace("-", "_")
        course_family = (
            str(requested_scope.get("course_family") or "")
            .strip()
            .lower()
            .replace("-", "_")
        )
        topics = self._store.list_topics(
            _MAX_PRACTICE_SCOPE_TOPICS + 1,
            subject or None,
            stage or None,
            chapter=str(requested_scope.get("chapter") or "").strip() or None,
            unit=str(requested_scope.get("unit") or "").strip() or None,
            course_family=course_family or None,
        )
        if len(topics) > _MAX_PRACTICE_SCOPE_TOPICS:
            raise PracticeScopeError(
                "practice scope matches too many topics",
                code="PRACTICE_SCOPE_TOO_LARGE",
            )
        return topics

    def _canonical_practice_scope(
        self, requested_scope: Mapping[str, object], *, revision: int
    ) -> PracticeScopeV1:
        topics = self._practice_scope_topics(requested_scope)
        return build_practice_scope(requested_scope, topics, revision=revision)

    def _resolve_active_practice_scope(self) -> PracticeScopeV1 | None:
        stored = getattr(self._state, "active_practice_scope", {})
        if not isinstance(stored, Mapping) or not stored:
            return None
        revision = max(
            1,
            int(
                stored.get("scope_revision")
                or getattr(self._state, "practice_scope_revision", 0)
                or 1
            ),
        )
        try:
            canonical = self._canonical_practice_scope(
                _scope_request_from_state(stored), revision=revision
            )
        except PracticeScopeError as exc:
            raise SdkError(
                "saved practice scope is no longer available",
                code="PRACTICE_SCOPE_INVALIDATED",
            ) from exc
        if str(stored.get("scope_key") or "") != canonical.scope_key:
            raise SdkError(
                "saved practice scope no longer matches the knowledge graph",
                code="PRACTICE_SCOPE_INVALIDATED",
            )
        canonical.set_at = str(stored.get("set_at") or canonical.set_at)
        canonical.source = str(stored.get("source") or canonical.source)
        return canonical

    @ui.action()
    @plugin_entry(
        id="study_set_practice_scope",
        name=tr("entries.practice_scope.set.name", default="Set Practice Scope"),
        description=tr(
            "entries.practice_scope.set.description",
            default="Validate and persist the active knowledge-map practice scope.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "object",
                    "properties": _PRACTICE_SCOPE_INPUT_PROPERTIES,
                    "additionalProperties": False,
                }
            },
            "required": ["scope"],
        },
        timeout=30.0,
        llm_result_fields=["active", "scope"],
    )
    async def study_set_practice_scope(self, scope: dict | None = None, **_):
        if not isinstance(scope, dict):
            return Err(
                SdkError(
                    "practice scope must be an object", code="INVALID_PRACTICE_SCOPE"
                )
            )
        try:
            async with self._practice_scope_write_lock():
                async with _plugin_lock(self._lock):
                    revision = max(
                        0, int(getattr(self._state, "practice_scope_revision", 0) or 0)
                    ) + 1
                canonical = await asyncio.to_thread(
                    self._canonical_practice_scope, scope, revision=revision
                )
                async with _plugin_lock(self._lock):
                    next_state = deepcopy(self._state)
                    next_state.practice_scope_revision = revision
                    next_state.active_practice_scope = canonical.to_state_dict()
                    await asyncio.to_thread(self._store.save_state, next_state)
                    with self._targeted_context_lock:
                        self._state.practice_scope_revision = revision
                        self._state.active_practice_scope = canonical.to_state_dict()
            return Ok(
                {
                    "active": True,
                    "scope": canonical.to_public_dict(),
                    "scope_revision": canonical.scope_revision,
                }
            )
        except PracticeScopeError as exc:
            return Err(SdkError(str(exc), code=exc.code))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_set_practice_scope")

    @ui.action()
    @plugin_entry(
        id="study_get_practice_scope",
        name=tr("entries.practice_scope.get.name", default="Get Practice Scope"),
        description=tr(
            "entries.practice_scope.get.description",
            default="Return the active canonical practice scope.",
        ),
        input_schema={"type": "object", "properties": {}},
        timeout=30.0,
        llm_result_fields=["active", "scope"],
    )
    async def study_get_practice_scope(self, **_):
        try:
            async with _plugin_lock(self._lock):
                canonical = await asyncio.to_thread(
                    self._resolve_active_practice_scope
                )
                revision = int(
                    getattr(self._state, "practice_scope_revision", 0) or 0
                )
            return Ok(
                {
                    "active": canonical is not None,
                    "scope": canonical.to_public_dict() if canonical else {},
                    "scope_revision": revision,
                }
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_get_practice_scope")

    @ui.action()
    @plugin_entry(
        id="study_clear_practice_scope",
        name=tr("entries.practice_scope.clear.name", default="Clear Practice Scope"),
        description=tr(
            "entries.practice_scope.clear.description",
            default="Clear the active practice scope and return to automatic selection.",
        ),
        input_schema={"type": "object", "properties": {}},
        timeout=30.0,
        llm_result_fields=["active", "scope_revision"],
    )
    async def study_clear_practice_scope(self, **_):
        try:
            async with self._practice_scope_write_lock():
                async with _plugin_lock(self._lock):
                    revision = max(
                        0,
                        int(
                            getattr(self._state, "practice_scope_revision", 0)
                            or 0
                        ),
                    ) + 1
                    next_state = deepcopy(self._state)
                    next_state.practice_scope_revision = revision
                    next_state.active_practice_scope = {}
                    await asyncio.to_thread(self._store.save_state, next_state)
                    with self._targeted_context_lock:
                        self._state.practice_scope_revision = revision
                        self._state.active_practice_scope = {}
            return Ok({"active": False, "scope": {}, "scope_revision": revision})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_clear_practice_scope")

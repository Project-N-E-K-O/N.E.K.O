"""N.E.K.O capabilities for the headless workshop; no implicit service startup."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

from .sdk import TheaterWorkshop, WorkshopError, PackageError, ModelReply, LLMCallFailure
from .sdk.workshop import canonical_root


class InProcessPackageGateway:
    def compile(self, story):
        from services.theater.numeric_v2 import NumericV2Compiler, NumericV2CompileError

        try:
            return NumericV2Compiler().compile_v2_2(story)
        except NumericV2CompileError as error:
            code = ("numeric_v2_upgrade_required" if any(
                issue.code == "numeric_v2_upgrade_required" for issue in error.issues
            ) else "numeric_v2_compile_failed")
            raise PackageError(code, {"issues": [asdict(i) for i in error.issues]}) from error

    def validate(self, json_bytes):
        return self.compile(json.loads(json_bytes))


class NekoWorkshopModel:
    """User-selected workshop model, isolated from chat/actor configuration."""

    TIMEOUT = 120.0

    def __init__(self, model_config):
        self._settings = deepcopy(dict(model_config or {}))

    def __eq__(self, other):
        return isinstance(other, NekoWorkshopModel) and self._settings == other._settings

    def __call__(self, messages, *, max_tokens, max_retries, response_format,
                 thinking, operation, temperature=None):
        from utils.llm_client import (
            create_chat_llm, set_active_character, reset_active_character,
            set_dialog_slop_lang, reset_dialog_slop_lang,
            _anthropic_usage_with_openai_aliases,
        )

        if max_retries != 1:
            raise WorkshopError("workshop_attempt_budget_invalid")
        settings = self._settings
        model = str(settings.get("model") or "").strip()
        if not model:
            raise WorkshopError("workshop_model_required")
        if not str(settings.get("base_url") or "").strip():
            # Do not send a selected provider's credential to an implicit
            # client default endpoint when the caller omitted its URL.
            raise WorkshopError("workshop_model_endpoint_required")
        if not str(settings.get("api_key") or "").strip():
            return LLMCallFailure("model_auth_failed", error_code="model_auth_failed",
                                  exception_type="MissingWorkshopCredential")
        client = None
        # asyncio.to_thread copies the caller's context. Author prompts must not
        # inherit normal-chat name substitution or style filtering.
        character_token = set_active_character("", "")
        slop_token = set_dialog_slop_lang(None)
        try:
            client = create_chat_llm(model=model, base_url=settings.get("base_url"),
                api_key=settings.get("api_key"), provider_type=settings.get("provider_type"),
                timeout=self.TIMEOUT, max_retries=0,
                max_completion_tokens=max_tokens)
            # User-selected providers use the host's token/thinking/temperature
            # policy; preserve the operation budget and do not add retries.
            response = client.invoke(messages, response_format=response_format)
            usage = (response.response_metadata or {}).get("token_usage") or None
            if usage and "input_tokens" in usage and "prompt_tokens" not in usage:
                usage = _anthropic_usage_with_openai_aliases(usage)
            content = response.content
            if not isinstance(content, str):
                content = LLMCallFailure("model_provider_error", error_code="model_provider_error",
                                         exception_type="InvalidModelResponse")
            return ModelReply(content, model, usage)
        except Exception as error:
            name, status = type(error).__name__, getattr(error, "status_code", None)
            if isinstance(error, TimeoutError) or "Timeout" in name:
                code = "model_timeout"
            elif "Connection" in name or name in {"ConnectError", "HTTPError"}:
                code = "model_connection_failed"
            elif status in {401, 403}:
                code = "model_auth_failed"
            elif status == 429:
                code = "model_rate_limited"
            elif isinstance(status, int) and 400 <= status < 500:
                code = "model_request_invalid"
            else:
                code = "model_provider_error"
            return ModelReply(LLMCallFailure(code, error_code=code, exception_type=name,
                                             status_code=status), model)
        finally:
            reset_active_character(character_token)
            reset_dialog_slop_lang(slop_token)
            if client is not None:
                client.close()


def open_workshop(config_manager, *, model_config=None, model_call=None) -> "WorkshopHost":
    """Explicitly open/reuse this process's writer. Call on a worker in async code."""
    from services.theater.numeric_v2_identity import numeric_v2_authoring_names
    from utils.cloudsave_runtime import cloudsave_writable_transaction

    app_root = canonical_root(config_manager.app_docs_dir)
    project_root = app_root / "theater" / "workshop" / "projects"
    if model_config is not None and model_call is not None:
        raise WorkshopError("workshop_model_input_conflict")

    @contextmanager
    def write_transaction():
        with cloudsave_writable_transaction(config_manager, operation="save",
                                             target="theater/workshop"):
            if canonical_root(config_manager.app_docs_dir) != app_root:
                raise WorkshopError("workshop_storage_root_changed")
            yield

    # Publish SDK ownership and its adapter together, so concurrent callers
    # cannot observe an instance whose host has not yet been attached.
    with TheaterWorkshop._instances_lock:
        if model_config is None and model_call is None:
            existing = TheaterWorkshop._instances.get(project_root)
            if existing is not None and existing._owner is config_manager and not existing._closing:
                return existing._neko_host
        sdk = TheaterWorkshop.open(project_root=project_root,
            control_root=Path(config_manager.local_state_dir), gateway=InProcessPackageGateway(),
            model_call=model_call if model_call is not None else NekoWorkshopModel(model_config),
            write_transaction=write_transaction,
            authoring_names=lambda: numeric_v2_authoring_names(config_manager), owner=config_manager)
        if not hasattr(sdk, "_neko_host"):
            sdk._neko_host = WorkshopHost(sdk, app_root)
        return sdk._neko_host


async def _worker(function, *args, **kwargs):
    """Cancellation cannot release a lifecycle lock while its worker still writes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve any failure to avoid an unobserved task exception. The
        # caller still sees cancellation; persisted state is read on recovery.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


class WorkshopHost:
    OPERATIONS = frozenset({
        "metric_presets", "create_project", "list_projects", "get_project", "allocate_id", "import_project",
        "update_project", "delete_project", "import_story", "generate",
        "assess_quality", "optimize_node", "enhance_node", "set_mainline_order",
        "branch_options", "get_branch_draft", "draft_branch_ending", "draft_branch_path",
        "apply_branch", "compile", "validate", "export",
    })

    def __init__(self, sdk, app_root):
        self.sdk = sdk
        self._app_root = app_root

    async def call(self, operation, *args, **kwargs):
        if operation not in self.OPERATIONS:
            raise WorkshopError("workshop_operation_invalid")
        return await _worker(getattr(self.sdk, operation), *args, **kwargs)

    async def install(self, project_id, *, base_revision, recover_receipt=False):
        from services.theater.numeric_v2_store import numeric_v2_story_session_guard
        from services.theater.numeric_v2_registry import NumericV2PackageRegistry

        with self.sdk.operation(project_id):
            def prepare():
                with self.sdk._store.transaction():
                    return self.sdk._publish_candidate(project_id, base_revision)
            candidate = await _worker(prepare)
            theater_root = self._app_root / "theater"
            # This is the caller's serving loop, the same loop as the theater
            # router; never asyncio.run() a new loop to acquire this lock.
            async with numeric_v2_story_session_guard(theater_root, candidate.story_id):
                def install(story):
                    registry = NumericV2PackageRegistry(theater_root / "numeric_v2" / "packages")
                    if recover_receipt:
                        path = registry.package_path(candidate.story_id)
                        if not path.is_file():
                            raise WorkshopError("installed_package_required")
                        result = registry.validate_package(json.loads(path.read_bytes()))
                        if result["package_hash"] != candidate.package_hash:
                            raise WorkshopError("installed_package_hash_mismatch")
                        return result
                    return registry.import_package(story)
                return await _worker(self.sdk._install_candidate, candidate, install)

    async def close(self):
        await _worker(self.sdk.close)

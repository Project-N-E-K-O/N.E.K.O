from __future__ import annotations

import asyncio
import threading
from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr

from .constants import MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING
from .models import (
    MODE_CONCEPT_EXPLAIN,
    STATUS_ERROR,
    STATUS_READY,
    STATUS_STOPPED,
    StudyConfig,
    StudyState,
    build_config,
    utc_now_iso,
)
from .service import (
    build_dependency_status,
    build_explain_payload,
    build_ocr_payload,
    build_status_payload,
)
from .mode_manager import ModeManager, build_transition_phrase, handle_user_intent, normalize_mode
from .state import build_initial_state
from .store import StudyStore
from .study_ocr_pipeline import StudyOcrPipeline
from .tutor_llm_agent import TutorLLMAgent
from .ui_api import build_open_ui_payload


@neko_plugin
class StudyCompanionPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = threading.RLock()
        self._install_lock = threading.Lock()
        self._rapidocr_models_lock = threading.Lock()
        self._cfg = StudyConfig()
        self._state = build_initial_state(mode=MODE_COMPANION)
        self._store = StudyStore(
            self.data_path("study_companion.db"),
            self.config_dir / "data" / "study_seed.json",
            self.logger,
        )
        self._ocr_pipeline: StudyOcrPipeline | None = None
        self._agent: TutorLLMAgent | None = None
        self._mode_manager = ModeManager()

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            raw = await self.config.dump(timeout=5.0)
            self._cfg = build_config(raw if isinstance(raw, dict) else {})
            await asyncio.to_thread(self._store.open)
            self._cfg = await asyncio.to_thread(self._store.load_config, self._cfg)
            restored = await asyncio.to_thread(self._store.load_state, build_initial_state(mode=self._cfg.mode))
            with self._lock:
                self._state = restored
                self._state.status = STATUS_READY
                self._state.active_mode = normalize_mode(self._state.active_mode or self._cfg.mode)
                self._state.mode_started_at = float(self._state.mode_started_at or 0.0)
                self._state.mode_lock_until = float(self._state.mode_lock_until or 0.0)
                self._cfg.mode = self._state.active_mode
                self._cfg.default_mode = self._state.active_mode
                self._state.last_started_at = utc_now_iso()
                self._state.last_error = ""
                self._mode_manager.restore(
                    {
                        "current_mode": self._state.active_mode,
                        "mode_started_at": self._state.mode_started_at,
                        "recent_mode_switches": self._state.recent_mode_switches,
                        "suggestion_cooldowns": self._state.suggestion_cooldowns,
                        "session_suggestions": self._state.session_suggestions,
                        "mode_lock_until": self._state.mode_lock_until,
                    }
            )
            self._ocr_pipeline = StudyOcrPipeline(logger=self.logger, config=self._cfg)
            self._agent = TutorLLMAgent(logger=self.logger, config=self._cfg)
            await asyncio.to_thread(self._refresh_dependency_status)
            self.register_static_ui("static")
            self.set_list_actions(
                [
                    {
                        "id": "open_ui",
                        "kind": "ui",
                        "target": f"/plugin/{self.plugin_id}/ui/",
                        "open_in": "new_tab",
                    }
                ]
            )
            await self._persist_state()
            status_payload = await asyncio.to_thread(self._status_payload)
            return Ok({"status": STATUS_READY, "result": status_payload})
        except Exception as exc:
            with self._lock:
                self._state.status = STATUS_ERROR
                self._state.last_error = str(exc)
            return Err(SdkError(f"failed to start study_companion: {exc}"))

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self._agent is not None:
            await self._agent.shutdown()
        with self._lock:
            self._state.status = STATUS_STOPPED
        await asyncio.to_thread(self._store.save_state, self._state)
        await asyncio.to_thread(self._store.close)
        return Ok({"status": STATUS_STOPPED})

    def _refresh_dependency_status(self) -> dict[str, Any]:
        status = build_dependency_status(self._cfg)
        with self._lock:
            self._state.dependency_status = status
        return status

    async def _persist_state(self) -> None:
        await asyncio.to_thread(self._store.save_config, self._cfg)
        await asyncio.to_thread(self._store.save_state, self._state)

    async def _apply_mode_switch(self, mode: str, reason: str, *, language: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._mode_manager.restore(
                {
                    "current_mode": self._state.active_mode,
                    "mode_started_at": self._state.mode_started_at,
                    "recent_mode_switches": self._state.recent_mode_switches,
                    "suggestion_cooldowns": self._state.suggestion_cooldowns,
                    "session_suggestions": self._state.session_suggestions,
                    "mode_lock_until": self._state.mode_lock_until,
                }
            )
            result = self._mode_manager.switch_to(mode, reason, language=language or self._cfg.language)
            checkpoint = result.get("checkpoint") if isinstance(result.get("checkpoint"), dict) else {}
            self._state.active_mode = str(result.get("new_mode") or self._state.active_mode)
            self._state.mode_started_at = float(checkpoint.get("mode_started_at") or self._state.mode_started_at or 0.0)
            self._state.recent_mode_switches = checkpoint.get("recent_mode_switches") if isinstance(checkpoint.get("recent_mode_switches"), list) else self._state.recent_mode_switches
            self._state.suggestion_cooldowns = checkpoint.get("suggestion_cooldowns") if isinstance(checkpoint.get("suggestion_cooldowns"), dict) else self._state.suggestion_cooldowns
            self._state.session_suggestions = checkpoint.get("session_suggestions") if isinstance(checkpoint.get("session_suggestions"), list) else self._state.session_suggestions
            self._state.mode_lock_until = float(checkpoint.get("mode_lock_until") or self._state.mode_lock_until or 0.0)
            self._state.checkpoint = {
                **checkpoint,
                "changed": bool(result.get("changed")),
                "old_mode": result.get("old_mode"),
                "new_mode": result.get("new_mode"),
                "reason": result.get("reason"),
                "transition_phrase": result.get("transition_phrase"),
                "locked": bool(result.get("locked")),
                "lock_reason": result.get("lock_reason"),
                "lock_until": float(result.get("lock_until") or 0.0),
            }
            if result.get("changed"):
                self._cfg.mode = self._state.active_mode
                self._cfg.default_mode = self._state.active_mode
        if result.get("changed") and self._agent is not None:
            self._agent.update_config(self._cfg)
        await self._persist_state()
        return result

    def _status_payload(self) -> dict[str, Any]:
        history = self._store.list_interactions(limit=10)
        return build_status_payload(config=self._cfg, state=self._state, history=history)

    def _resolve_current_run_id(self, extra_args: dict[str, Any] | None = None) -> str:
        current = str(getattr(self.ctx, "run_id", "") or "").strip()
        if current:
            return current
        if isinstance(extra_args, dict):
            ctx_obj = extra_args.get("_ctx")
            if isinstance(ctx_obj, dict):
                return str(ctx_obj.get("run_id") or "").strip()
        return ""

    def _resolve_install_progress_callback(self, current_run_id: str):
        async def _progress_update(event: dict[str, Any]) -> None:
            if not current_run_id:
                return
            try:
                await self.run_update(
                    run_id=current_run_id,
                    progress=float(event.get("progress") or 0.0),
                    stage=str(event.get("phase") or ""),
                    message=str(event.get("message") or ""),
                    metrics={
                        "phase": str(event.get("phase") or ""),
                        "downloaded_bytes": int(event.get("downloaded_bytes") or 0),
                        "total_bytes": int(event.get("total_bytes") or 0),
                        "resume_from": int(event.get("resume_from") or 0),
                        "asset_name": str(event.get("asset_name") or ""),
                        "release_name": str(event.get("release_name") or ""),
                    },
                )
            except Exception as exc:
                self.logger.warning("study install progress run_update failed: {}", exc)

        return _progress_update

    @plugin_entry(
        id="study_open_ui",
        name=tr("entries.open_ui.name", default="Open Study Companion UI"),
        description=tr("entries.open_ui.description", default="Return the static UI path for study_companion."),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "path", "message_key"],
    )
    async def study_open_ui(self, **_):
        return Ok(build_open_ui_payload(plugin_id=self.plugin_id, available=self.get_static_ui_config() is not None))

    @plugin_entry(
        id="study_status",
        name=tr("entries.status.name", default="Study Companion Status"),
        description=tr("entries.status.description", default="Return runtime status, dependencies, and recent study interactions."),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["status", "active_mode"],
    )
    async def study_status(self, **_):
        payload = await asyncio.to_thread(self._status_payload)
        return Ok(payload)

    @plugin_entry(
        id="study_detect_mode_intent",
        name=tr("entries.detect_mode_intent.name", default="Detect Study Mode Intent"),
        description=tr("entries.detect_mode_intent.description", default="Detect whether a text snippet contains a study mode switch intent."),
        input_schema={"type": "object", "properties": {"text": {"type": "string", "default": ""}}},
        llm_result_fields=["mode", "pure_switch", "transition_phrase"],
    )
    async def study_detect_mode_intent(self, text: str = "", **_):
        return Ok(handle_user_intent(text, language=self._cfg.language))

    @plugin_entry(
        id="study_set_mode",
        name=tr("entries.set_mode.name", default="Set Study Mode"),
        description=tr("entries.set_mode.description", default="Switch the study companion between companion, interactive, and teaching modes."),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": [MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING]},
                "reason": {"type": "string", "default": "ui"},
            },
            "required": ["mode"],
        },
        llm_result_fields=["changed", "new_mode", "transition_phrase"],
    )
    async def study_set_mode(self, mode: str, reason: str = "ui", **_):
        try:
            result = await self._apply_mode_switch(mode, reason, language=self._cfg.language)
        except ValueError as exc:
            return Err(SdkError(str(exc)))
        return Ok(result)

    @plugin_entry(
        id="study_dependency_status",
        name=tr("entries.dependency_status.name", default="Study OCR Dependency Status"),
        description=tr("entries.dependency_status.description", default="Inspect RapidOCR, Tesseract, and capture dependencies used by study_companion."),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["missing_installable"],
    )
    async def study_dependency_status(self, **_):
        status = await asyncio.to_thread(self._refresh_dependency_status)
        await self._persist_state()
        return Ok(status)

    @plugin_entry(
        id="study_ocr_snapshot",
        name=tr("entries.ocr_snapshot.name", default="Study OCR Snapshot"),
        description=tr("entries.ocr_snapshot.description", default="Run a lightweight OCR snapshot. Phase 1 attempts fullscreen capture and returns diagnostics on failure."),
        input_schema={"type": "object", "properties": {}},
        timeout=45.0,
        llm_result_fields=["summary", "status", "diagnostic"],
    )
    async def study_ocr_snapshot(self, **_):
        if self._ocr_pipeline is None:
            return Err(SdkError("study OCR pipeline is not initialized"))
        snapshot = await asyncio.to_thread(self._ocr_pipeline.capture_snapshot)
        payload = build_ocr_payload(snapshot)
        if snapshot.text.strip():
            with self._lock:
                self._state.last_ocr_text = snapshot.text
                self._state.last_ocr_at = snapshot.captured_at
        await self._persist_state()
        return Ok(payload)

    @plugin_entry(
        id="study_explain_text",
        name=tr("entries.explain_text.name", default="Explain Study Text"),
        description=tr("entries.explain_text.description", default="Explain a concept from supplied text, or use the latest OCR text if text is omitted."),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": ""},
            },
        },
        timeout=45.0,
        llm_result_fields=["summary", "reply", "diagnostic"],
    )
    async def study_explain_text(self, text: str = "", **_):
        if self._agent is None:
            return Err(SdkError("study tutor agent is not initialized"))
        raw_text = str(text or "").strip()
        # Phase 1: detect an explicit mode intent and switch first when present.
        intent = handle_user_intent(raw_text, language=self._cfg.language) if raw_text else {"matched": False, "pure_switch": False, "mode": "", "remaining_text": ""}
        active_mode = self._state.active_mode
        mode_switch: dict[str, Any] = {}
        if intent.get("matched") and intent.get("kind") == "mode_switch":
            try:
                mode_switch = await self._apply_mode_switch(str(intent.get("mode") or MODE_COMPANION), f"intent:{intent.get('keyword') or 'text'}", language=self._cfg.language)
                active_mode = str(mode_switch.get("new_mode") or active_mode)
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            if intent.get("pure_switch"):
                transition_phrase = str(mode_switch.get("transition_phrase") or intent.get("transition_phrase") or "")
                return Ok(
                    {
                        **mode_switch,
                        "reply": transition_phrase,
                        "summary": transition_phrase,
                        "operation": MODE_CONCEPT_EXPLAIN,
                        "input_text": raw_text,
                        "degraded": False,
                    }
                )
        # Phase 2: resolve the text to explain.
        intent_kind = str(intent.get("kind") or "")
        source_text = str(intent.get("remaining_text") or "").strip()
        if not source_text and intent_kind != "concept_explain":
            source_text = raw_text
        used_ocr_fallback = False
        if not source_text:
            with self._lock:
                source_text = self._state.last_ocr_text
            used_ocr_fallback = bool(source_text.strip())
        # Phase 3: explain with the active mode selected above.
        reply = await self._agent.concept_explain(
            source_text,
            mode=active_mode,
            context={
                "source": "ocr_snapshot" if used_ocr_fallback or not raw_text else "manual",
                "mode": active_mode,
                "mode_switch": bool(mode_switch.get("changed")),
            },
        )
        with self._lock:
            self._state.last_reply = reply.reply
            self._state.last_reply_at = reply.created_at
            if reply.diagnostic and reply.degraded:
                self._state.last_error = reply.diagnostic
        await asyncio.to_thread(
            self._store.append_interaction,
            kind=MODE_CONCEPT_EXPLAIN,
            input_text=reply.input_text,
            output_text=reply.reply,
            metadata={
                "degraded": reply.degraded,
                "diagnostic": reply.diagnostic,
                "mode": active_mode,
                "mode_switch": mode_switch,
                "intent": intent,
            },
            history_limit=self._cfg.history_limit,
        )
        await self._persist_state()
        payload = build_explain_payload(reply)
        if mode_switch:
            payload["mode_switch"] = mode_switch
        if intent.get("matched"):
            payload["intent"] = intent
            if intent.get("pure_switch"):
                payload["transition_phrase"] = str(mode_switch.get("transition_phrase") or intent.get("transition_phrase") or "")
        return Ok(payload)

    @plugin_entry(
        id="study_install_tesseract",
        name=tr("entries.install_tesseract.name", default="Install Tesseract for Study OCR"),
        description=tr("entries.install_tesseract.description", default="Install local Tesseract OCR for study_companion and refresh dependency status."),
        input_schema={"type": "object", "properties": {"force": {"type": "boolean", "default": False}}},
        timeout=300.0,
        llm_result_fields=["summary"],
    )
    async def study_install_tesseract(self, force: bool = False, **kwargs):
        if not self._install_lock.acquire(blocking=False):
            return Err(SdkError("Tesseract install is already running"))
        run_id = self._resolve_current_run_id(kwargs)
        try:
            from plugin.plugins.galgame_plugin.tesseract_support import install_tesseract

            result = await install_tesseract(
                logger=self.logger,
                configured_path=self._cfg.ocr_tesseract_path,
                install_target_dir_raw=self._cfg.ocr_install_target_dir,
                manifest_url=self._cfg.ocr_install_manifest_url,
                timeout_seconds=self._cfg.ocr_install_timeout_seconds,
                languages=self._cfg.ocr_languages,
                force=bool(force),
                task_id=run_id or None,
                plugin_id=self.plugin_id,
                progress_callback=self._resolve_install_progress_callback(run_id),
            )
            self._refresh_dependency_status()
            await self._persist_state()
            return Ok({"summary": str(result.get("summary") or "Tesseract is ready"), "install_result": result})
        except Exception as exc:
            return Err(SdkError(f"Tesseract install failed: {exc}"))
        finally:
            self._install_lock.release()

    @plugin_entry(
        id="study_download_rapidocr_models",
        name=tr("entries.download_rapidocr_models.name", default="Download RapidOCR Models for Study OCR"),
        description=tr("entries.download_rapidocr_models.description", default="Download missing RapidOCR model files for the configured study_companion OCR language."),
        input_schema={"type": "object", "properties": {"force": {"type": "boolean", "default": False}}},
        timeout=600.0,
        llm_result_fields=["summary"],
    )
    async def study_download_rapidocr_models(self, force: bool = False, **kwargs):
        if not self._rapidocr_models_lock.acquire(blocking=False):
            return Err(SdkError("RapidOCR model download is already running"))
        run_id = self._resolve_current_run_id(kwargs)
        try:
            from plugin.plugins.galgame_plugin.rapidocr_support import download_rapidocr_models

            result = await download_rapidocr_models(
                logger=self.logger,
                install_target_dir_raw=self._cfg.rapidocr_install_target_dir,
                ocr_version=self._cfg.rapidocr_ocr_version,
                lang_type=self._cfg.rapidocr_lang_type,
                timeout_seconds=float(self._cfg.ocr_install_timeout_seconds or 180.0),
                force=bool(force),
                task_id=run_id or None,
                plugin_id=self.plugin_id,
                progress_callback=self._resolve_install_progress_callback(run_id),
                before_completed_callback=lambda: None,
            )
            self._refresh_dependency_status()
            await self._persist_state()
            downloaded = result.get("downloaded") or []
            return Ok(
                {
                    "summary": (
                        f"RapidOCR models ready ({len(downloaded)} file(s) downloaded)"
                        if downloaded
                        else "RapidOCR models already present"
                    ),
                    "download_result": result,
                }
            )
        except Exception as exc:
            return Err(SdkError(f"RapidOCR model download failed: {exc}"))
        finally:
            self._rapidocr_models_lock.release()


StudyCompanionBridgePlugin = StudyCompanionPlugin

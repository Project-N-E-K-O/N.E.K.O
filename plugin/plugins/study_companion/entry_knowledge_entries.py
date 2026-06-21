from __future__ import annotations

from .entry_common import (
    asyncio,
    Ok,
    _entry_exception_error,
    plugin_entry,
    tr,
    ui,
    StudyConfig,
    PublicGraphContributionBuilder,
    build_contribution_settings_payload,
    build_knowledge_map_payload,
)


class _KnowledgeEntriesMixin:
    @plugin_entry(
        id="study_knowledge_quality_status",
        name=tr(
            "entries.knowledge_quality_status.name",
            default="Study Knowledge Quality Status",
        ),
        description=tr(
            "entries.knowledge_quality_status.description",
            default="Return candidate knowledge quality counts and recent evidence.",
        ),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
        llm_result_fields=["total", "by_status", "recent_evidence"],
    )
    async def study_knowledge_quality_status(self, limit: int = 20, **_):
        try:
            safe_limit = max(1, int(limit or 20))
            payload = await asyncio.to_thread(
                self._knowledge_tracker.quality.status_summary,
                limit=safe_limit,
            )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(
                self, exc, operation="study_knowledge_quality_status"
            )

    @ui.action()
    @plugin_entry(
        id="study_anonymous_knowledge_preview",
        name=tr(
            "entries.anonymous_knowledge_preview.name",
            default="Study Anonymous Knowledge Preview",
        ),
        description=tr(
            "entries.anonymous_knowledge_preview.description",
            default="Build and return a local anonymized knowledge contribution preview. Phase 4 does not upload it.",
        ),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
        },
        llm_result_fields=["summary", "stats", "opt_in"],
    )
    async def study_anonymous_knowledge_preview(self, limit: int = 100, **_):
        try:
            builder = PublicGraphContributionBuilder(self._store, self._cfg)
            payload = await asyncio.to_thread(
                builder.preview, limit=max(1, int(limit or 100))
            )
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_anonymous_knowledge_preview")

    @ui.action()
    @plugin_entry(
        id="study_knowledge_map",
        name=tr("entries.knowledge_map.name", default="Study Knowledge Map"),
        description=tr(
            "entries.knowledge_map.description",
            default="Return topics, relationships, mastery, weak topics, and wrong-question summaries for the study knowledge map.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 200},
                "stage": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["summary", "nodes", "edges"],
    )
    async def study_knowledge_map(self, limit: int = 200, stage: str = "", **_):
        try:
            safe_limit = max(1, min(1000, int(limit or 200)))
            stage_key = str(stage or "").strip()
            topics, mastery, weak_topics, wrong_questions = await asyncio.gather(
                asyncio.to_thread(
                    self._store.list_topics,
                    safe_limit,
                    None,
                    stage_key or None,
                ),
                asyncio.to_thread(self._store.list_mastery_overview, safe_limit),
                asyncio.to_thread(
                    self._knowledge_tracker.get_weak_topics, limit=min(50, safe_limit)
                ),
                asyncio.to_thread(
                    self._store.list_wrong_questions, limit=min(50, safe_limit)
                ),
            )
            return Ok(
                build_knowledge_map_payload(
                    topics=topics,
                    mastery_overview=mastery,
                    weak_topics=weak_topics,
                    wrong_questions=wrong_questions,
                )
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_knowledge_map")

    @ui.action()
    @plugin_entry(
        id="study_set_knowledge_contribution_opt_in",
        name=tr(
            "entries.set_knowledge_contribution_opt_in.name",
            default="Set Study Knowledge Contribution Opt-In",
        ),
        description=tr(
            "entries.set_knowledge_contribution_opt_in.description",
            default="Enable or disable local opt-in for anonymous study knowledge contribution queueing.",
        ),
        input_schema={
            "type": "object",
            "properties": {"opt_in": {"type": "boolean", "default": False}},
            "required": ["opt_in"],
        },
        llm_result_fields=["opt_in", "summary", "queue"],
    )
    async def study_set_knowledge_contribution_opt_in(self, opt_in: bool = False, **_):
        try:
            desired_opt_in = bool(opt_in)
            preview_config = StudyConfig(**self._cfg.to_dict())
            preview_config.knowledge_contribution_opt_in = desired_opt_in
            builder = PublicGraphContributionBuilder(self._store, preview_config)
            preview = await asyncio.to_thread(builder.preview, limit=100)
            self._cfg.knowledge_contribution_opt_in = desired_opt_in
            await self._persist_state()
            return Ok(
                build_contribution_settings_payload(
                    opt_in=desired_opt_in, preview=preview
                )
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_set_knowledge_contribution_opt_in")

    @plugin_entry(
        id="study_clear_knowledge_contribution_queue",
        name=tr(
            "entries.clear_knowledge_contribution_queue.name",
            default="Clear Study Knowledge Contribution Queue",
        ),
        description=tr(
            "entries.clear_knowledge_contribution_queue.description",
            default="Clear the local anonymous knowledge contribution queue.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["cleared_count"],
    )
    async def study_clear_knowledge_contribution_queue(self, **_):
        try:
            builder = PublicGraphContributionBuilder(self._store, self._cfg)
            cleared = await asyncio.to_thread(builder.clear_queue)
            return Ok({"cleared_count": cleared})
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_clear_knowledge_contribution_queue")

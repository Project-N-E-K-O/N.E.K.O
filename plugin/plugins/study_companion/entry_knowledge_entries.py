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
from .knowledge_quality import (
    KnowledgeCandidateStatus,
    KnowledgeCandidateType,
    KnowledgeEvidenceType,
)
from .knowledge_graph_guidance import build_knowledge_guidance_payload


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
        id="study_review_knowledge_candidate",
        name=tr(
            "entries.review_knowledge_candidate.name",
            default="Review Study Knowledge Candidate",
        ),
        description=tr(
            "entries.review_knowledge_candidate.description",
            default="Approve or reject a candidate knowledge topic before it is merged into the base knowledge library.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "decision": {
                    "type": "string",
                    "enum": ["approve", "reject"],
                    "default": "approve",
                },
            },
            "required": ["candidate_id", "decision"],
        },
        llm_result_fields=["decision", "candidate", "topic"],
    )
    async def study_review_knowledge_candidate(
        self, candidate_id: str = "", decision: str = "approve", **_
    ):
        try:
            candidate_key = str(candidate_id or "").strip()
            if not candidate_key:
                raise ValueError("candidate_id is required")
            normalized_decision = str(decision or "").strip().lower()
            if normalized_decision not in {"approve", "reject"}:
                raise ValueError("decision must be approve or reject")

            def _review() -> dict[str, object]:
                candidate = self._store.get_candidate_item(candidate_key)
                if not candidate:
                    raise KeyError(f"knowledge candidate not found: {candidate_key}")

                if normalized_decision == "reject":
                    self._knowledge_tracker.quality.add_evidence(
                        candidate_key,
                        KnowledgeEvidenceType.USER_REJECTED.value,
                        -1.0,
                        {"source": "candidate_review"},
                    )
                    refreshed = self._store.get_candidate_item(candidate_key) or candidate
                    self._store.update_candidate_score_status(
                        item_id=candidate_key,
                        score=float(refreshed.get("score") or -1.0),
                        status=KnowledgeCandidateStatus.DEPRECATED.value,
                        evidence_count=int(refreshed.get("evidence_count") or 0),
                        positive_count=int(refreshed.get("positive_count") or 0),
                        negative_count=int(refreshed.get("negative_count") or 0),
                        conflict_count=int(refreshed.get("conflict_count") or 0),
                    )
                    return {
                        "decision": normalized_decision,
                        "candidate": self._store.get_candidate_item(candidate_key) or refreshed,
                        "topic": {},
                    }

                if candidate.get("item_type") != KnowledgeCandidateType.TOPIC.value:
                    raise ValueError("only topic candidates can be approved into the base library")
                payload = dict(candidate.get("payload") or {})
                topic_id = str(payload.get("id") or payload.get("topic_id") or "").strip()
                name = str(payload.get("name") or payload.get("topic") or topic_id).strip()
                if not topic_id or not name:
                    raise ValueError("topic candidate requires id/topic_id and name/topic")
                self._store.upsert_topic(
                    {
                        "id": topic_id,
                        "name": name,
                        "subject": payload.get("subject") or "math",
                        "chapter": payload.get("chapter") or "",
                        "stage": payload.get("stage")
                        or payload.get("grade_level")
                        or payload.get("education_level")
                        or payload.get("course_level")
                        or "",
                        "unit": payload.get("unit") or payload.get("chapter") or "",
                        "depth": payload.get("depth") or 1,
                        "difficulty": payload.get("difficulty") or 0.5,
                        "prerequisites": payload.get("prerequisites")
                        if isinstance(payload.get("prerequisites"), list)
                        else [],
                        "related": payload.get("related")
                        if isinstance(payload.get("related"), list)
                        else [],
                        "typical_misconceptions": payload.get("typical_misconceptions")
                        if isinstance(payload.get("typical_misconceptions"), list)
                        else [],
                        "skills": payload.get("skills")
                        if isinstance(payload.get("skills"), list)
                        else [],
                        "question_types": payload.get("question_types")
                        if isinstance(payload.get("question_types"), list)
                        else [],
                        "examples": payload.get("examples")
                        if isinstance(payload.get("examples"), list)
                        else [],
                        "course_family": str(payload.get("course_family") or "").strip(),
                        "aliases": payload.get("aliases")
                        if isinstance(payload.get("aliases"), list)
                        else [],
                        "source": "seed",
                    }
                )
                invalidate_guidance_cache = getattr(
                    self, "_invalidate_knowledge_guidance_cache", None
                )
                if callable(invalidate_guidance_cache):
                    invalidate_guidance_cache()
                self._knowledge_tracker.quality.add_evidence(
                    candidate_key,
                    KnowledgeEvidenceType.USER_CONFIRMED.value,
                    1.0,
                    {"source": "candidate_review", "topic_id": topic_id},
                )
                refreshed = self._store.get_candidate_item(candidate_key) or candidate
                self._store.update_candidate_score_status(
                    item_id=candidate_key,
                    score=max(float(refreshed.get("score") or 0.0), 1.0),
                    status=KnowledgeCandidateStatus.TRUSTED.value,
                    evidence_count=int(refreshed.get("evidence_count") or 0),
                    positive_count=int(refreshed.get("positive_count") or 0),
                    negative_count=int(refreshed.get("negative_count") or 0),
                    conflict_count=int(refreshed.get("conflict_count") or 0),
                )
                return {
                    "decision": normalized_decision,
                    "candidate": self._store.get_candidate_item(candidate_key) or refreshed,
                    "topic": self._store.get_topic(topic_id) or {},
                }

            return Ok(await asyncio.to_thread(_review))
        except Exception as exc:
            return _entry_exception_error(
                self, exc, operation="study_review_knowledge_candidate"
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
                "subject": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["summary", "nodes", "edges"],
    )
    async def study_knowledge_map(
        self, limit: int = 200, stage: str = "", subject: str = "", **_
    ):
        try:
            safe_limit = max(1, min(1000, int(limit or 200)))
            stage_key = str(stage or "").strip()
            subject_key = str(subject or "").strip()
            topics, mastery, weak_topics, wrong_questions = await asyncio.gather(
                asyncio.to_thread(
                    self._store.list_topics,
                    safe_limit,
                    subject_key or None,
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

    @plugin_entry(
        id="study_knowledge_guidance",
        name=tr(
            "entries.knowledge_guidance.name",
            default="Study Knowledge Guidance",
        ),
        description=tr(
            "entries.knowledge_guidance.description",
            default="Use the typed knowledge graph to return prerequisites, applications, confusions, and next practice topics for a topic or query.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "topic_id": {"type": "string", "default": ""},
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 500},
                "stage": {"type": "string", "default": ""},
                "course_family": {"type": "string", "default": ""},
                "max_depth": {"type": "integer", "default": 3},
            },
        },
        llm_result_fields=[
            "topic",
            "matches",
            "learning_path",
            "applications",
            "confusions",
            "next_practice_topics",
            "diagnosis_questions",
            "summary",
        ],
    )
    async def study_knowledge_guidance(
        self,
        topic_id: str = "",
        query: str = "",
        limit: int = 500,
        stage: str = "",
        course_family: str = "",
        max_depth: int = 3,
        **_,
    ):
        try:
            safe_limit = max(1, min(5000, int(limit or 500)))
            stage_key = str(stage or "").strip()
            course_family_key = str(course_family or "").strip()
            topics = await asyncio.to_thread(
                self._store.list_topics,
                safe_limit,
                None,
                stage_key or None,
            )
            if course_family_key:
                topics = [
                    topic
                    for topic in topics
                    if str(topic.get("course_family") or "").strip() == course_family_key
                ]
            return Ok(
                build_knowledge_guidance_payload(
                    topics=topics,
                    topic_id=str(topic_id or ""),
                    query=str(query or ""),
                    max_depth=max(1, min(5, int(max_depth or 3))),
                )
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_knowledge_guidance")

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

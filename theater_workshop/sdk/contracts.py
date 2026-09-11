"""Transport independent workshop input contracts."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class StrictPayload(BaseModel):
    """Reject undeclared fields instead of silently rewriting author requests."""

    model_config = ConfigDict(extra="forbid")


class RevisionPayload(StrictPayload):
    base_revision: int = Field(ge=1)


class ProjectUpdatePayload(RevisionPayload):
    changes: dict[str, Any]


class NumericV2ImportPayload(StrictPayload):
    story: dict[str, Any]


class NumericV2AllocateIdPayload(StrictPayload):
    kind: Literal["node", "route", "ending"]


class AuthorProjectPayload(StrictPayload):
    """Persisted author data, including the checkpoint omitted from public views.

    Drafts need not pass the published Story Package contract. Validate the
    storage envelope without repairing or discarding unfinished author content.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(pattern=r"^project_[A-Za-z0-9_-]+$")
    revision: int = Field(ge=1)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    title: str
    stage: Literal["setup", "story", "publish"]
    setup: dict[str, Any]
    editor: dict[str, Any]
    authoring: dict[str, Any]
    story: dict[str, Any] | None
    generation_state: Literal["running", "succeeded", "failed", "interrupted"] | None
    generation_error: dict[str, Any] | None
    checkpoint: dict[str, Any] | None = Field(alias="_generation_checkpoint")
    compile_result: dict[str, Any] | None
    neko_validation: dict[str, Any] | None
    install_result: dict[str, Any] | None
    imported_publish_receipts: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_containers(self):
        for field in ("brief", "genre", "relationship", "length_preset"):
            if not isinstance(self.setup.get(field), str):
                raise ValueError("author_project_invalid")
        for field in ("tone", "content_boundaries", "metrics"):
            if not isinstance(self.setup.get(field), list):
                raise ValueError("author_project_invalid")
        for field in ("mainline_node_ids", "key_props"):
            if not isinstance(self.authoring.get(field), list):
                raise ValueError("author_project_invalid")
        for field in ("route_semantics", "branch_drafts", "relationship_arc", "character_state_arc"):
            if not isinstance(self.authoring.get(field), dict):
                raise ValueError("author_project_invalid")
        for field in ("route_semantics", "branch_drafts"):
            if any(not isinstance(row, dict) for row in self.authoring[field].values()):
                raise ValueError("author_project_invalid")
        if any(not isinstance(node, str) for node in self.authoring["mainline_node_ids"]):
            raise ValueError("author_project_invalid")
        if any(not isinstance(prop, dict) for prop in self.authoring["key_props"]):
            raise ValueError("author_project_invalid")
        for field in ("quality_assessment", "pacing_diagnostics"):
            if field not in self.authoring or not isinstance(self.authoring[field], (dict, type(None))):
                raise ValueError("author_project_invalid")
        if not isinstance(self.editor.get("node_positions"), dict):
            raise ValueError("author_project_invalid")
        if self.story is not None and not isinstance(self.story.get("nodes"), list):
            raise ValueError("author_project_invalid")
        if self.checkpoint is not None and (
            not isinstance(self.checkpoint.get("candidate"), dict)
            or not isinstance(self.checkpoint.get("issues"), list)
        ):
            raise ValueError("author_project_invalid")
        return self


class BranchConditionSelectionPayload(StrictPayload):
    mode: Literal["fixed", "recommend"]
    key: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_fixed_key(self):
        if self.mode == "fixed" and not str(self.key or "").strip():
            raise ValueError("branch_condition_required")
        if self.mode == "recommend":
            self.key = None
        return self


class BranchGoalContractPayload(StrictPayload):
    owner: Literal["catgirl", "player", "shared", "environment"]
    delivery_type: Literal[
        "catgirl_dialogue",
        "catgirl_action",
        "environment_fact",
        "player_action",
        "shared_agreement",
        "semantic_state",
    ]
    description: str = Field(min_length=1, max_length=2000)
    evidence_mode: Literal["exact", "semantic"]
    anchors: list[str] = Field(max_length=8)
    sources: list[Literal["opening", "player_input", "previous_goal"]] = Field(
        min_length=1,
        max_length=3,
    )
    timing: Literal["opening", "turn"] = "turn"
    dialogue_policy_after: Literal["required", "optional", "forbidden", "unchanged"] = "unchanged"


class BranchActingContractPayload(StrictPayload):
    cognition_state: Literal["fresh_boot", "limited", "normal"]
    memory_state: Literal["empty", "partial", "available"]
    self_reference_mode: Literal["system_neutral", "persona_allowed"]
    persona_scope: Literal["style_only", "full"]
    dialogue_policy: Literal["required", "optional", "forbidden"]
    assertable_self_facts: list[str] = Field(default_factory=list, max_length=8)
    allowed_behaviors: list[str] = Field(default_factory=list, max_length=4)
    forbidden_behaviors: list[str] = Field(default_factory=list, max_length=4)


class BranchCharacterStatePayload(StrictPayload):
    catgirl_state: str = Field(min_length=1, max_length=1000)
    player_state: str = Field(min_length=1, max_length=1000)
    environment_state: str = Field(min_length=1, max_length=1000)
    acting_contract: BranchActingContractPayload
    continuity_from_previous: list[str] = Field(min_length=1, max_length=4)
    # 与生成器及 N.E.K.O 编译合同一致：空边界有效，字段仍必填且最多四条。
    scene_boundaries: list[str] = Field(max_length=4)


class BranchEndingContractPayload(StrictPayload):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    opening_scene: str = Field(min_length=1, max_length=4000)
    ordered_goals: list[BranchGoalContractPayload] = Field(min_length=1, max_length=6)
    irreversible_facts: list[str] = Field(min_length=1, max_length=20)
    character_state: BranchCharacterStatePayload
    catgirl_situation: str = Field(min_length=1, max_length=2000)
    tone: str = Field(min_length=1, max_length=500)


class BranchEndingDraftPayload(RevisionPayload):
    source_node_id: str = Field(min_length=1, max_length=160)
    ending_direction: str = Field(min_length=1, max_length=4000)
    condition_selection: BranchConditionSelectionPayload


class BranchPathDraftPayload(RevisionPayload):
    source_node_id: str | None = Field(default=None, max_length=160)
    endpoint_mode: Literal["mainline", "existing_ending", "new_ending"]
    endpoint_node_id: str | None = Field(default=None, max_length=160)
    direction: str = Field(default="", max_length=4000)
    length: int = Field(ge=1, le=3)
    condition_selection: BranchConditionSelectionPayload | None = None
    ending_draft_id: str | None = Field(default=None, max_length=160)
    ending: BranchEndingContractPayload | None = None

    @model_validator(mode="after")
    def validate_endpoint_fields(self):
        if self.endpoint_mode == "new_ending":
            if not self.ending_draft_id or self.ending is None:
                raise ValueError("branch_ending_draft_required")
        elif not self.source_node_id or not self.endpoint_node_id or self.condition_selection is None:
            raise ValueError("branch_endpoint_required")
        return self


class BranchApplyPayload(RevisionPayload):
    draft_id: str = Field(min_length=1, max_length=160)


class MainlineOrderPayload(RevisionPayload):
    node_ids: list[str] = Field(min_length=1, max_length=200)


class MetricBandPayload(StrictPayload):
    min: int
    max: int
    label: str = Field(min_length=1, max_length=120)


class MetricPayload(StrictPayload):
    id: str = Field(min_length=1, max_length=128)
    preset: str | None = None
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    relationship_effect: Literal["positive", "negative", "none"] = "none"
    min: int
    max: int
    initial: int
    # v2.2 的单回合限幅统一限制在 1—5，和 N.E.K.O 运行时合同保持一致。
    increase_limit: int = Field(ge=1, le=5)
    decrease_limit: int = Field(ge=1, le=5)
    increase_criteria: list[str] = Field(min_length=1)
    decrease_criteria: list[str] = Field(min_length=1)
    visibility: Literal["hidden"]
    bands: list[MetricBandPayload] = Field(min_length=1)

    @field_validator("increase_criteria", "decrease_criteria")
    @classmethod
    def validate_criteria(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("metric_criteria_required")
        forbidden = {"根据剧情判断", "由 AI 决定", "由AI决定"}
        if any(value in forbidden for value in normalized):
            raise ValueError("metric_criteria_not_actionable")
        return normalized

    @model_validator(mode="after")
    def validate_range(self):
        if self.min >= self.max:
            raise ValueError("invalid_metric_range")
        if not self.min <= self.initial <= self.max:
            raise ValueError("metric_initial_out_of_range")
        if set(self.increase_criteria) == set(self.decrease_criteria):
            raise ValueError("metric_criteria_overlap")
        cursor = self.min
        for band in sorted(self.bands, key=lambda item: (item.min, item.max)):
            if band.min != cursor or band.max < band.min:
                raise ValueError("metric_bands_not_contiguous")
            cursor = band.max + 1
        if cursor != self.max + 1:
            raise ValueError("metric_bands_not_contiguous")
        return self


class NumericV2SetupPayload(StrictPayload):
    brief: str = Field(min_length=1, max_length=12000)
    genre: str = Field(default="", max_length=160)
    tone: list[str] = Field(default_factory=list, max_length=12)
    relationship: str = Field(default="", max_length=2000)
    content_boundaries: list[str] = Field(default_factory=list, max_length=30)
    length_preset: Literal["short", "standard", "long"]
    # 首次只生成主线和结局，数值允许作者进入故事地图后再配置。
    metrics: list[MetricPayload] = Field(default_factory=list, max_length=4)

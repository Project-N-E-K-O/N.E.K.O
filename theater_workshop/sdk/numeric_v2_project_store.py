"""Manage Numeric v2 author-project revisions, derived state and atomic persistence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
import re
from contextlib import contextmanager
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Mapping
import uuid

from .numeric_v2 import (
    DEFAULT_METRIC_INITIAL,
    DEFAULT_METRIC_MAX,
    DEFAULT_METRIC_MIN,
    metrics_to_package,
    normalize_metric_drafts,
)
from .numeric_v2_branch import NumericV2BranchService


class NumericV2ProjectError(ValueError):
    """An author project could not be located, read or saved."""


class NumericV2ProjectNotFoundError(NumericV2ProjectError):
    pass


class NumericV2RevisionConflictError(NumericV2ProjectError):
    def __init__(self, project: Mapping[str, Any]):
        super().__init__("project_revision_conflict")
        self.project = deepcopy(dict(project))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_editor(value: Any) -> dict[str, Any]:
    """Store only author canvas coordinates, keeping editor state out of Story Package."""

    source = value if isinstance(value, Mapping) else {}
    raw_positions = source.get("node_positions")
    positions: dict[str, dict[str, float]] = {}
    if isinstance(raw_positions, Mapping):
        for node_id, position in raw_positions.items():
            if not isinstance(node_id, str) or not isinstance(position, Mapping):
                continue
            x = position.get("x")
            y = position.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            if not math.isfinite(x) or not math.isfinite(y):
                continue
            positions[node_id] = {"x": float(x), "y": float(y)}
    return {"node_positions": positions}


def _infer_unambiguous_mainline(story: Any) -> list[str]:
    """Restore imported mainline order only when the path remains unique from opening to ending."""

    if not isinstance(story, Mapping):
        return []
    nodes = {
        str(node.get("id") or ""): node
        for node in story.get("nodes") or []
        if isinstance(node, Mapping) and node.get("id")
    }
    current_id = str(story.get("start_node_id") or "")
    ordered: list[str] = []
    visited: set[str] = set()
    while current_id:
        if current_id in visited:
            return []
        visited.add(current_id)
        node = nodes.get(current_id)
        if not isinstance(node, Mapping):
            return []
        if node.get("type") == "ending":
            return ordered
        ordered.append(current_id)
        routes = [route for route in node.get("route_gates") or [] if isinstance(route, Mapping)]
        if len(routes) != 1:
            return []
        target_id = str(routes[0].get("target_node_id") or "")
        target = nodes.get(target_id)
        if not isinstance(target, Mapping):
            return []
        if target.get("type") == "ending":
            return ordered
        current_id = target_id
    return []


def _normalize_authoring(value: Any, story: Any) -> dict[str, Any]:
    """Keep author metadata outside Story Package while ensuring references to current nodes and routes remain valid."""

    source = value if isinstance(value, Mapping) else {}
    nodes = {
        str(node.get("id") or ""): node
        for node in (story.get("nodes") or [] if isinstance(story, Mapping) else [])
        if isinstance(node, Mapping) and node.get("id")
    }
    raw_order = source.get("mainline_node_ids")
    ordered: list[str] = []
    if isinstance(raw_order, list):
        for node_id in raw_order:
            if (
                isinstance(node_id, str)
                and node_id not in ordered
                and node_id in nodes
                and nodes[node_id].get("type") != "ending"
            ):
                ordered.append(node_id)
    if not ordered:
        ordered = _infer_unambiguous_mainline(story)

    route_ids = {
        str(route.get("id") or "")
        for node in nodes.values()
        for route in node.get("route_gates") or []
        if isinstance(route, Mapping) and route.get("id")
    }
    semantics: dict[str, dict[str, Any]] = {}
    raw_semantics = source.get("route_semantics")
    if isinstance(raw_semantics, Mapping):
        for route_id, row in raw_semantics.items():
            if isinstance(route_id, str) and route_id in route_ids and isinstance(row, Mapping):
                semantics[route_id] = deepcopy(dict(row))

    drafts: dict[str, dict[str, Any]] = {}
    raw_drafts = source.get("branch_drafts")
    if isinstance(raw_drafts, Mapping):
        for draft_id, draft in raw_drafts.items():
            if isinstance(draft_id, str) and isinstance(draft, Mapping):
                drafts[draft_id] = deepcopy(dict(draft))

    relationship_arc: dict[str, Any] = {}
    raw_arc = source.get("relationship_arc")
    if isinstance(raw_arc, Mapping):
        stages: list[dict[str, Any]] = []
        for raw_stage in raw_arc.get("stages") or []:
            if not isinstance(raw_stage, Mapping):
                continue
            node_id = str(raw_stage.get("node_id") or "")
            if node_id not in ordered:
                continue
            stages.append(deepcopy(dict(raw_stage)))
        relationship_arc = {
            "opening_relationship": str(raw_arc.get("opening_relationship") or "").strip(),
            "long_term_direction": str(raw_arc.get("long_term_direction") or "").strip(),
            "stages": stages,
        }
    character_state_arc: dict[str, Any] = {}
    raw_state_arc = source.get("character_state_arc")
    if isinstance(raw_state_arc, Mapping):
        state_stages: list[dict[str, Any]] = []
        for raw_stage in raw_state_arc.get("stages") or []:
            if not isinstance(raw_stage, Mapping):
                continue
            node_id = str(raw_stage.get("node_id") or "")
            if node_id not in ordered:
                continue
            state_stages.append(deepcopy(dict(raw_stage)))
        ending_stage = raw_state_arc.get("ending_stage")
        character_state_arc = {
            "stages": state_stages,
            "ending_stage": deepcopy(dict(ending_stage)) if isinstance(ending_stage, Mapping) else {},
        }
    key_props: list[dict[str, Any]] = []
    for raw_prop in source.get("key_props") or []:
        if not isinstance(raw_prop, Mapping):
            continue
        prop = deepcopy(dict(raw_prop))
        states: list[dict[str, Any]] = []
        for raw_state in prop.get("states") or []:
            if not isinstance(raw_state, Mapping):
                continue
            state = deepcopy(dict(raw_state))
            node_id = str(state.get("node_id") or "")
            chapter_index = state.pop("chapter_index", None)
            if (
                not node_id
                and isinstance(chapter_index, int)
                and not isinstance(chapter_index, bool)
                and 1 <= chapter_index <= len(ordered)
            ):
                node_id = ordered[chapter_index - 1]
            if node_id not in nodes:
                continue
            state["node_id"] = node_id
            states.append(state)
        if states:
            prop["states"] = states
            key_props.append(prop)
    quality_assessment = None
    raw_assessment = source.get("quality_assessment")
    if isinstance(raw_assessment, Mapping) and raw_assessment.get("scope") in {
        "mainline_normal",
        "full_story_simple",
    }:
        quality_assessment = deepcopy(dict(raw_assessment))
        quality_assessment["stale"] = bool(raw_assessment.get("stale", False))
    pacing_diagnostics = None
    raw_pacing = source.get("pacing_diagnostics")
    if isinstance(raw_pacing, Mapping):
        # 节奏检查是作者侧派生信息；只保留结构化结果，不让它进入 Story Package。
        pacing_diagnostics = deepcopy(dict(raw_pacing))
    return {
        "mainline_node_ids": ordered,
        "route_semantics": semantics,
        "branch_drafts": drafts,
        "relationship_arc": relationship_arc,
        "character_state_arc": character_state_arc,
        "key_props": key_props,
        "quality_assessment": quality_assessment,
        "pacing_diagnostics": pacing_diagnostics,
    }


def _route_signatures(story: Any) -> dict[str, tuple[Any, Any, Any]]:
    if not isinstance(story, Mapping):
        return {}
    return {
        str(route.get("id")): (
            route.get("target_node_id"),
            route.get("priority"),
            json.dumps(route.get("conditions"), ensure_ascii=False, sort_keys=True),
        )
        for node in story.get("nodes") or []
        if isinstance(node, Mapping)
        for route in node.get("route_gates") or []
        if isinstance(route, Mapping) and route.get("id")
    }


def derive_project_status(project: Mapping[str, Any]) -> str:
    """Derive state only from project content, revision and package hash."""

    if project.get("generation_state") == "running":
        return "generating"
    story = project.get("story")
    if not isinstance(story, Mapping):
        return "setup"
    revision = project.get("revision")
    compiled = project.get("compile_result")
    if not isinstance(compiled, Mapping) or compiled.get("revision") != revision:
        return "editing"
    if not compiled.get("success"):
        return "invalid"
    verified = project.get("neko_validation")
    if (
        isinstance(verified, Mapping)
        and bool(compiled.get("package_hash"))
        and verified.get("revision") == revision
        and verified.get("package_hash") == compiled.get("package_hash")
        and verified.get("success") is True
    ):
        return "verified"
    return "compiled"


def _advance_branch_drafts(
    project: dict[str, Any], *, base_revision: int, previous_fingerprint: str | None,
) -> None:
    """Carry valid drafts through a content-neutral revision; otherwise invalidate them."""

    next_fingerprint = NumericV2BranchService._fingerprint(project) if previous_fingerprint else None
    for draft in project["authoring"]["branch_drafts"].values():
        if (
            previous_fingerprint
            and draft.get("status") in {"preview", "ending_review"}
            and draft.get("base_revision") == base_revision
            and draft.get("context_fingerprint") == previous_fingerprint
        ):
            draft["base_revision"] = project["revision"]
            draft["context_fingerprint"] = next_fingerprint
        elif draft.get("status") not in {"applied", "failed"}:
            draft["status"] = "stale"


class NumericV2ProjectStore:
    """Store one JSON file per project without overwriting newer revisions from concurrent edits."""

    def __init__(self, root: Path, *, transaction, compiler):
        self.root = Path(root)
        self._lock = RLock()
        self._transaction = transaction
        self._compiler = compiler

    @contextmanager
    def transaction(self):
        # The host fence must precede the Store lock, including nested publish
        # operations; the injected transaction is reentrant on the same thread.
        with self._transaction(), self._lock:
            yield

    def _carry_publish_receipts(self, project):
        compiled = project.get("compile_result") or {}
        if not compiled.get("success") or not compiled.get("package_hash"):
            return
        previous_revision = compiled.get("revision")
        try:
            current = self._compiler.compile(project["story"])
        except (ValueError, RuntimeError):
            current = None
        if current is None or current.package_hash != compiled["package_hash"]:
            for key in ("compile_result", "neko_validation", "install_result"):
                project[key] = None
            return
        for key in ("compile_result", "neko_validation", "install_result"):
            receipt = project.get(key)
            if (isinstance(receipt, dict)
                    and receipt.get("revision") == previous_revision
                    and receipt.get("package_hash") == current.package_hash):
                receipt["revision"] = project["revision"]

    def recover_interrupted(self):
        """Explicit open repairs interrupted state without resending a model call."""
        with self.transaction():
            for row in self.list():
                if row.get("generation_state") == "running":
                    project = self._read_path(self._path(row["project_id"]))
                    project["generation_state"] = "interrupted"
                    project["generation_error"] = {"code": "generation_interrupted"}
                    self._write(project)

    def _path(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not re.fullmatch(r"project_[A-Za-z0-9_-]+", project_id):
            raise NumericV2ProjectNotFoundError("project_not_found")
        return self.root / f"{project_id}.json"

    def create(self) -> dict[str, Any]:
        with self.transaction():
            project_id = f"project_{uuid.uuid4().hex[:12]}"
            timestamp = _now()
            project = {
                "project_id": project_id,
                "revision": 1,
                "created_at": timestamp,
                "updated_at": timestamp,
                "title": "未命名剧本",
                "stage": "setup",
                "setup": {
                    "brief": "",
                    "genre": "",
                    "tone": [],
                    "relationship": "",
                    "content_boundaries": [],
                    "length_preset": "standard",
                    "metrics": [],
                },
                "editor": {"node_positions": {}},
                "authoring": {
                    "mainline_node_ids": [],
                    "route_semantics": {},
                    "branch_drafts": {},
                    "relationship_arc": {},
                    "character_state_arc": {},
                    "key_props": [],
                    "quality_assessment": None,
                    "pacing_diagnostics": None,
                },
                "story": None,
                "generation_state": None,
                "generation_error": None,
                "_generation_checkpoint": None,
                "compile_result": None,
                "neko_validation": None,
                "install_result": None,
            }
            self._write(project)
            return self._view(project)


    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        projects: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("project_*.json")):
            try:
                projects.append(self._view(self._read_path(path)))
            except (OSError, UnicodeError, json.JSONDecodeError, NumericV2ProjectError):
                continue
        projects.sort(key=lambda item: item["updated_at"], reverse=True)
        return projects

    def get(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            return self._view(self._read_path(self._path(project_id)))

    def update(self, project_id: str, *, base_revision: int, changes: Mapping[str, Any]) -> dict[str, Any]:
        return self._update(project_id, base_revision=base_revision, changes=changes)

    def _update(self, project_id: str, *, base_revision: int, changes: Mapping[str, Any],
                preserve_imported_story: bool = False) -> dict[str, Any]:
        with self.transaction():
            project = self._read_path(self._path(project_id))
            if project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            allowed = {"title", "stage", "setup", "story", "editor"}
            unknown = set(changes).difference(allowed)
            if unknown:
                raise NumericV2ProjectError("unsupported_project_change")
            content_neutral = set(changes) <= {"editor", "stage"}
            previous_fingerprint = NumericV2BranchService._fingerprint(project) if content_neutral else None
            package_changed = "story" in changes
            if "title" in changes:
                project["title"] = str(changes["title"] or "").strip()
                project["_generation_checkpoint"] = None
                if isinstance(project.get("story"), dict):
                    project["story"].setdefault("meta", {})["title"] = project["title"]
                    package_changed = True
            if "stage" in changes and changes["stage"] in {"setup", "story", "publish"}:
                project["stage"] = changes["stage"]
            if "setup" in changes:
                old_setup = deepcopy(dict(project.get("setup") or {}))
                setup = deepcopy(dict(changes["setup"] or {}))
                setup["metrics"] = normalize_metric_drafts(list(setup.get("metrics") or []))
                old_metrics = (project.get("setup") or {}).get("metrics") or []
                project["setup"] = setup
                project["_generation_checkpoint"] = None
                # Metric definitions also affect quality advice and pacing.
                if old_setup != setup:
                    authoring = _normalize_authoring(project.get("authoring"), project.get("story"))
                    if isinstance(authoring.get("quality_assessment"), dict):
                        authoring["quality_assessment"]["stale"] = True
                    authoring["pacing_diagnostics"] = None
                    project["authoring"] = authoring
                if isinstance(project.get("story"), dict) and setup["metrics"] != old_metrics:
                    metric_schema, initial_metrics = metrics_to_package(setup["metrics"])
                    project["story"]["metric_schema"] = metric_schema
                    # 数值编辑不能清空姓名披露等既有初始状态。
                    project["story"].setdefault("initial_state", {})["metrics"] = initial_metrics
                    package_changed = True
            if "story" in changes:
                story = changes["story"]
                next_story = deepcopy(dict(story)) if isinstance(story, Mapping) else None
                # Combined edits describe one author revision; apply them to the
                # replacement instead of losing them with the previous story.
                if next_story is not None:
                    if "title" in changes:
                        next_story.setdefault("meta", {})["title"] = project["title"]
                    if "setup" in changes and not preserve_imported_story:
                        metric_schema, initial_metrics = metrics_to_package(project["setup"]["metrics"])
                        next_story["metric_schema"] = metric_schema
                        next_story.setdefault("initial_state", {})["metrics"] = initial_metrics
                authoring = _normalize_authoring(project.get("authoring"), project.get("story"))
                old_signatures = _route_signatures(project.get("story"))
                new_signatures = _route_signatures(next_story)
                authoring["route_semantics"] = {
                    route_id: row
                    for route_id, row in authoring["route_semantics"].items()
                    if old_signatures.get(route_id) == new_signatures.get(route_id)
                }
                project["story"] = next_story
                project["_generation_checkpoint"] = None
                if isinstance(authoring.get("quality_assessment"), dict):
                    authoring["quality_assessment"]["stale"] = True
                authoring["pacing_diagnostics"] = None
                project["authoring"] = _normalize_authoring(authoring, next_story)
            if "editor" in changes:
                project["editor"] = _normalize_editor(changes["editor"])
            if project.get("generation_state") == "running":
                # An allowed edit invalidates the in-flight candidate. Do not
                # leave the project presenting an eternal running operation.
                project["generation_state"] = "interrupted"
                project["generation_error"] = {"code": "generation_revision_changed"}
            project["revision"] += 1
            project["updated_at"] = _now()
            project["authoring"] = _normalize_authoring(project.get("authoring"), project.get("story"))
            _advance_branch_drafts(project, base_revision=base_revision, previous_fingerprint=previous_fingerprint)
            if package_changed:
                project["compile_result"] = None
                project["neko_validation"] = None
                project["install_result"] = None
            else:
                self._carry_publish_receipts(project)
            self._write(project)
            return self._view(project)


    def begin_generation(self, project_id: str, *, base_revision: int) -> dict[str, Any]:
        """Record an explicit generation attempt without changing the content revision."""

        with self.transaction():
            project = self._read_path(self._path(project_id))
            if project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            project["generation_state"] = "running"
            project["generation_error"] = None
            self._write(project)
            return self._view(project)


    def generation_checkpoint(self, project_id: str) -> dict[str, Any] | None:
        """Expose internal continuation checkpoints only to the generation service, not public project DTOs."""

        with self._lock:
            project = self._read_path(self._path(project_id))
            checkpoint = project.get("_generation_checkpoint")
            return deepcopy(dict(checkpoint)) if isinstance(checkpoint, Mapping) else None

    def finish_generation(
        self,
        project_id: str,
        *,
        base_revision: int,
        story: Mapping[str, Any],
        setup: Mapping[str, Any] | None = None,
        mainline_node_ids: list[str] | None = None,
        relationship_arc: Mapping[str, Any] | None = None,
        character_state_arc: Mapping[str, Any] | None = None,
        key_props: list[Mapping[str, Any]] | None = None,
        pacing_diagnostics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically replace the map and model-derived setup fields after the mainline candidate is complete."""

        with self.transaction():
            project = self._read_path(self._path(project_id))
            if project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            project["story"] = deepcopy(dict(story))
            if setup is not None:
                normalized_setup = deepcopy(dict(setup))
                normalized_setup["metrics"] = normalize_metric_drafts(
                    list(normalized_setup.get("metrics") or [])
                )
                project["setup"] = normalized_setup
            project["editor"] = {"node_positions": {}}
            project["authoring"] = _normalize_authoring(
                {
                    "mainline_node_ids": list(mainline_node_ids or []),
                    "route_semantics": {},
                    "branch_drafts": {},
                    "relationship_arc": deepcopy(dict(relationship_arc or {})),
                    "character_state_arc": deepcopy(dict(character_state_arc or {})),
                    "key_props": deepcopy(list(key_props or [])),
                    "quality_assessment": None,
                    "pacing_diagnostics": (
                        deepcopy(dict(pacing_diagnostics))
                        if isinstance(pacing_diagnostics, Mapping)
                        else None
                    ),
                },
                project["story"],
            )
            project["stage"] = "story"
            project["revision"] += 1
            project["updated_at"] = _now()
            project["generation_state"] = "succeeded"
            project["generation_error"] = None
            project["_generation_checkpoint"] = None
            project["compile_result"] = None
            project["neko_validation"] = None
            project["install_result"] = None
            self._write(project)
            return self._view(project)


    def record_quality_assessment(
        self,
        project_id: str,
        assessment: Mapping[str, Any],
        *,
        base_revision: int,
    ) -> dict[str, Any]:
        """Explicit assessment updates only author metadata, without changing Story Package revision."""

        with self.transaction():
            project = self._read_path(self._path(project_id))
            if project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            if not isinstance(project.get("story"), Mapping):
                raise NumericV2ProjectError("project_story_required")
            authoring = _normalize_authoring(project.get("authoring"), project.get("story"))
            authoring["quality_assessment"] = {
                **deepcopy(dict(assessment)),
                "assessed_revision": project["revision"],
                "stale": False,
            }
            project["authoring"] = authoring
            project["updated_at"] = _now()
            self._write(project)
            return self._view(project)


    def set_mainline_order(
        self,
        project_id: str,
        *,
        base_revision: int,
        node_ids: list[str],
    ) -> dict[str, Any]:
        """Save the author-confirmed mainline path without modifying Story Package."""

        with self.transaction():
            project = self._read_path(self._path(project_id))
            if project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            story = project.get("story")
            if not isinstance(story, Mapping):
                raise NumericV2ProjectError("project_story_required")
            ordered = [str(node_id or "").strip() for node_id in node_ids]
            nodes = {
                str(node.get("id") or ""): node
                for node in story.get("nodes") or []
                if isinstance(node, Mapping) and node.get("id")
            }
            if (
                not ordered
                or any(not node_id for node_id in ordered)
                or len(set(ordered)) != len(ordered)
                or ordered[0] != story.get("start_node_id")
                or any(node_id not in nodes or nodes[node_id].get("type") == "ending" for node_id in ordered)
            ):
                raise NumericV2ProjectError("mainline_order_invalid")
            for source_id, target_id in zip(ordered, ordered[1:]):
                direct = [
                    route
                    for route in nodes[source_id].get("route_gates") or []
                    if isinstance(route, Mapping) and route.get("target_node_id") == target_id
                ]
                if len(direct) != 1:
                    raise NumericV2ProjectError("mainline_order_invalid")
            ending_targets = {
                node_id for node_id, node in nodes.items() if node.get("type") == "ending"
            }
            if not any(
                isinstance(route, Mapping) and route.get("target_node_id") in ending_targets
                for route in nodes[ordered[-1]].get("route_gates") or []
            ):
                raise NumericV2ProjectError("mainline_order_invalid")

            authoring = _normalize_authoring(project.get("authoring"), story)
            order_changed = authoring.get("mainline_node_ids") != ordered
            previous_fingerprint = None if order_changed else NumericV2BranchService._fingerprint(project)
            if order_changed:
                if isinstance(authoring.get("quality_assessment"), dict):
                    authoring["quality_assessment"]["stale"] = True
                authoring["pacing_diagnostics"] = None
            authoring["mainline_node_ids"] = ordered
            project["authoring"] = authoring
            project["revision"] += 1
            _advance_branch_drafts(project, base_revision=base_revision, previous_fingerprint=previous_fingerprint)
            project["updated_at"] = _now()
            self._carry_publish_receipts(project)
            self._write(project)
            return self._view(project)


    def save_branch_draft(
        self,
        project_id: str,
        *,
        base_revision: int,
        draft: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist author drafts without changing the published-content revision."""

        with self.transaction():
            project = self._read_path(self._path(project_id))
            if project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            draft_id = str(draft.get("draft_id") or "")
            if not draft_id.startswith("branch_draft_"):
                raise NumericV2ProjectError("branch_draft_invalid")
            authoring = _normalize_authoring(project.get("authoring"), project.get("story"))
            authoring["branch_drafts"][draft_id] = deepcopy(dict(draft))
            project["authoring"] = authoring
            self._write(project)
            return deepcopy(authoring["branch_drafts"][draft_id])


    def get_branch_draft(self, project_id: str, draft_id: str) -> dict[str, Any]:
        with self._lock:
            project = self._read_path(self._path(project_id))
            authoring = _normalize_authoring(project.get("authoring"), project.get("story"))
            draft = authoring["branch_drafts"].get(draft_id)
            if not isinstance(draft, Mapping):
                raise NumericV2ProjectError("branch_draft_not_found")
            return deepcopy(dict(draft))

    def commit_branch_draft(
        self,
        project_id: str,
        *,
        draft_id: str,
        base_revision: int,
        story: Mapping[str, Any],
        route_semantics: Mapping[str, Any],
        key_props: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Write a complete branch once; reapplying the same draft must not create nodes again."""

        with self.transaction():
            project = self._read_path(self._path(project_id))
            authoring = _normalize_authoring(project.get("authoring"), project.get("story"))
            draft = authoring["branch_drafts"].get(draft_id)
            if not isinstance(draft, dict):
                raise NumericV2ProjectError("branch_draft_not_found")
            if draft.get("status") == "applied":
                return self._view(project)
            if project["revision"] != base_revision or draft.get("base_revision") != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))

            project["story"] = deepcopy(dict(story))
            authoring["route_semantics"].update(deepcopy(dict(route_semantics)))
            authoring["key_props"] = deepcopy(list(key_props))
            if isinstance(authoring.get("quality_assessment"), dict):
                authoring["quality_assessment"]["stale"] = True
            authoring["pacing_diagnostics"] = None
            draft["status"] = "applied"
            project["revision"] += 1
            draft["applied_revision"] = project["revision"]
            for other_id, other in authoring["branch_drafts"].items():
                if other_id != draft_id and other.get("status") not in {"applied", "failed"}:
                    other["status"] = "stale"
            project["authoring"] = _normalize_authoring(authoring, project["story"])
            project["updated_at"] = _now()
            project["generation_state"] = "succeeded"
            project["generation_error"] = None
            project["compile_result"] = None
            project["neko_validation"] = None
            project["install_result"] = None
            self._write(project)
            return self._view(project)


    def fail_generation(
        self,
        project_id: str,
        *,
        base_revision: int,
        error: Mapping[str, Any],
        checkpoint: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record failure diagnostics without overwriting author setup, existing story content or revision."""

        with self.transaction():
            project = self._read_path(self._path(project_id))
            if project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            project["generation_state"] = "failed"
            project["generation_error"] = deepcopy(dict(error))
            project["_generation_checkpoint"] = (
                deepcopy(dict(checkpoint)) if isinstance(checkpoint, Mapping) else None
            )
            self._write(project)
            return self._view(project)


    def record_compile(self, project_id: str, result: Mapping[str, Any], *, base_revision: int | None = None) -> dict[str, Any]:
        with self.transaction():
            project = self._read_path(self._path(project_id))
            if base_revision is not None and project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            project["compile_result"] = {**deepcopy(dict(result)), "revision": project["revision"]}
            project["neko_validation"] = None
            project["install_result"] = None
            self._write(project)
            return self._view(project)


    def record_validation(self, project_id: str, result: Mapping[str, Any], *, base_revision: int | None = None) -> dict[str, Any]:
        with self.transaction():
            project = self._read_path(self._path(project_id))
            if base_revision is not None and project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            compiled = project.get("compile_result") or {}
            if not compiled.get("success") or not compiled.get("package_hash") or compiled.get("revision") != project["revision"]:
                raise NumericV2ProjectError("current_compile_required")
            if result.get("package_hash") != compiled.get("package_hash"):
                raise NumericV2ProjectError("neko_validation_hash_mismatch")
            project["neko_validation"] = {**deepcopy(dict(result)), "success": True, "revision": project["revision"], "validated_at": _now()}
            self._write(project)
            return self._view(project)


    def record_install(self, project_id: str, result: Mapping[str, Any], *, base_revision: int | None = None) -> dict[str, Any]:
        with self.transaction():
            project = self._read_path(self._path(project_id))
            if base_revision is not None and project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            project["install_result"] = {**deepcopy(dict(result)), "revision": project["revision"], "installed_at": _now()}
            self._write(project)
            return self._view(project)


    def delete(self, project_id: str, *, base_revision: int) -> None:
        with self.transaction():
            path = self._path(project_id)
            project = self._read_path(path)
            if project["revision"] != base_revision:
                raise NumericV2RevisionConflictError(self._view(project))
            if not path.is_file():
                raise NumericV2ProjectNotFoundError("project_not_found")
            path.unlink()


    def import_story(self, story: Mapping[str, Any]) -> dict[str, Any]:
        project = self.create()
        setup_metrics = []
        for metric_id, definition in story.get("metric_schema", {}).items():
            setup_metrics.append({
                "id": metric_id,
                "preset": None,
                "name": definition.get("name", ""),
                "description": definition.get("description", ""),
                "relationship_effect": definition.get("relationship_effect", "none"),
                "min": definition.get("min", DEFAULT_METRIC_MIN),
                "max": definition.get("max", DEFAULT_METRIC_MAX),
                "initial": definition.get("initial", DEFAULT_METRIC_INITIAL),
                "increase_limit": (definition.get("per_turn_limit") or {}).get("increase", 5),
                "decrease_limit": (definition.get("per_turn_limit") or {}).get("decrease", 5),
                "increase_criteria": definition.get("increase_criteria", []),
                "decrease_criteria": definition.get("decrease_criteria", []),
                "visibility": "hidden",
                "bands": definition.get("bands", []),
            })
        setup = deepcopy(project["setup"])
        setup["metrics"] = setup_metrics
        # 导入的 setup 只是旧包的编辑投影，不是作者要求改写正文；保留编译过的原包及 hash。
        return self._update(
            project["project_id"],
            base_revision=project["revision"],
            preserve_imported_story=True,
            changes={
                "title": story.get("meta", {}).get("title", "未命名剧本"),
                "stage": "story",
                "setup": setup,
                "story": story,
            },
        )

    def import_project(self, source: Mapping[str, Any]) -> dict[str, Any]:
        with self.transaction():
            path = self._path(source["project_id"])
            if path.exists() or path.is_symlink():
                raise NumericV2ProjectError("project_already_exists")
            try:
                # Validate serializability before opening any destination file.
                # JSON also detaches nested containers from the caller.
                encoded = json.dumps(source, ensure_ascii=False, allow_nan=False).encode("utf-8")
                project = json.loads(encoded)
                history = project.setdefault("imported_publish_receipts", {})
                for key in ("compile_result", "neko_validation", "install_result"):
                    if key not in history or project[key] is not None:
                        history[key] = project[key]
                    project[key] = None
                if project["generation_state"] == "running":
                    project["generation_state"] = "interrupted"
                # Exercise the same reader before commit. Malformed draft
                # containers must not leave a file that get/list cannot read.
                view = self._view(project)
            except (TypeError, ValueError, KeyError, AttributeError) as error:
                raise NumericV2ProjectError("author_project_invalid") from error
            self._write(project)
            return view

    def _read_path(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise NumericV2ProjectNotFoundError("project_not_found")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise NumericV2ProjectError("project_invalid")
        return payload

    def _write(self, project: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(str(project["project_id"]))
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root,
                prefix=f".{target.stem}-",
                suffix=".tmp",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as temporary:
                # Track the temporary file before serialization/fsync so every
                # failed import or ordinary save cleans up its partial file.
                temporary_path = Path(temporary.name)
                json.dump(project, temporary, ensure_ascii=False, indent=2)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _view(project: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(project))
        checkpoint = result.pop("_generation_checkpoint", None)
        if isinstance(checkpoint, Mapping) and isinstance(checkpoint.get("candidate"), Mapping):
            issues = checkpoint.get("issues") if isinstance(checkpoint.get("issues"), list) else []
            result["generation_checkpoint"] = {
                "available": True,
                "remaining_issue_count": len(issues),
            }
        else:
            result["generation_checkpoint"] = None
        result["editor"] = _normalize_editor(project.get("editor"))
        result["authoring"] = _normalize_authoring(project.get("authoring"), project.get("story"))
        result["status"] = derive_project_status(project)
        return result


__all__ = [
    "NumericV2ProjectError",
    "NumericV2ProjectNotFoundError",
    "NumericV2ProjectStore",
    "NumericV2RevisionConflictError",
    "derive_project_status",
]

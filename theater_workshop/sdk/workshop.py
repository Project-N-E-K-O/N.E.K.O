"""Programmatic authoring workflows extracted from InkAI's API handlers."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
from threading import Condition, RLock, local
import uuid

from . import contracts as C
from .generation.numeric_v2 import NumericV2Generator, NumericV2GenerationError
from .generation.quality import NumericV2QualityAssessor
from .model import capture_usage
from .numeric_v2 import NumericV2Compiler, normalize_metric_drafts, preset_metric_catalog
from .numeric_v2_branch import NumericV2BranchService
from .numeric_v2_project_store import NumericV2ProjectStore, NumericV2RevisionConflictError
from .packages import PackageError, PublishCandidate


class WorkshopError(RuntimeError):
    def __init__(self, code: str, details: dict | None = None):
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})


def canonical_root(path) -> Path:
    return Path(os.path.normcase(str(Path(path).expanduser().resolve())))


def _exclusive(method):
    @wraps(method)
    def call(self, project_id, *args, **kwargs):
        with self.operation(project_id), capture_usage() as usage:
            # Reject maintenance/root changes before any model request, then
            # release all file locks for the potentially long computation.
            with self._store.transaction():
                pass
            try:
                result = method(self, project_id, *args, **kwargs)
            except Exception as error:
                # Failed phases may already have spent tokens. Keep their
                # detached usage on the error, rather than reporting zero cost.
                error.usage = deepcopy(usage)
                raise
            if isinstance(result, dict) and usage:
                result["usage"] = usage
            return result
    return call


class TheaterWorkshop:
    """Use open()/close(); one writer owns each canonical project directory."""

    _instances: dict[Path, "TheaterWorkshop"] = {}
    _instances_lock = RLock()

    @classmethod
    def open(cls, *, project_root, control_root, gateway, model_call,
             write_transaction, authoring_names, owner=None):
        root = canonical_root(project_root)
        with cls._instances_lock:
            existing = cls._instances.get(root)
            if existing is None and root.exists():
                existing = next((item for path, item in cls._instances.items()
                                 if path.exists() and path.samefile(root)), None)
            if existing is not None:
                if existing._closing:
                    raise WorkshopError("workshop_closing")
                if existing._owner is not owner:
                    raise WorkshopError("workshop_owner_mismatch")
                if existing._generator._model_call != model_call:
                    raise WorkshopError("workshop_model_mismatch")
                return existing
            instance = cls(root, canonical_root(control_root), gateway, model_call,
                           write_transaction, authoring_names, owner)
            cls._instances[root] = instance
            return instance

    def __init__(self, root, control_root, gateway, model_call,
                 write_transaction, authoring_names, owner):
        import portalocker

        self.root = root
        self._owner = owner
        self._condition = Condition(RLock())
        self._closing = self._closed = False
        self._active = 0
        self._busy: set[str] = set()
        self._local = local()
        self._host_transaction = write_transaction
        self._names = authoring_names
        self._gateway = gateway
        self._compiler = NumericV2Compiler(gateway)
        self._generator = NumericV2Generator(model_call)
        self._quality = NumericV2QualityAssessor(model_call)
        self._branch = NumericV2BranchService()
        # Identity also collapses case aliases on case-insensitive filesystems.
        # Directory creation is an explicit, fenced open action, never import.
        with self._host_transaction():
            root.mkdir(parents=True, exist_ok=True)
            stat = root.stat()
            self._root_identity = (stat.st_dev, stat.st_ino)
        # The OS lock lives in local control state, outside projects/cloud data.
        control_root.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(repr(self._root_identity).encode()).hexdigest()
        self._ownership = portalocker.Lock(
            str(control_root / f"workshop-{key}.lock"), mode="a+b", timeout=0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        try:
            self._ownership.acquire()
        except portalocker.exceptions.LockException as error:
            raise WorkshopError("workshop_root_in_use") from error
        self._store = NumericV2ProjectStore(root, transaction=self._write_transaction,
                                          compiler=self._compiler)
        try:
            self._store.recover_interrupted()
        except BaseException:
            self._ownership.release()
            raise

    @contextmanager
    def _write_transaction(self):
        # Nested Store calls must not reacquire/release the process-wide cloud
        # apply handle. Enter and exit on the same worker, never across await.
        if getattr(self._local, "writing", False):
            yield
            return
        if self._closed:
            raise WorkshopError("workshop_closed")
        with self._host_transaction():
            stat = self.root.stat()
            if (stat.st_dev, stat.st_ino) != self._root_identity:
                raise WorkshopError("workshop_storage_root_changed")
            self._local.writing = True
            try:
                yield
            finally:
                self._local.writing = False

    @contextmanager
    def operation(self, project_id=None, *, exclusive=True):
        with self._condition:
            if self._closing or self._closed:
                raise WorkshopError("workshop_closed")
            if exclusive and project_id in self._busy:
                raise WorkshopError("project_busy")
            if exclusive and project_id is not None:
                self._busy.add(project_id)
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                if exclusive and project_id is not None:
                    self._busy.discard(project_id)
                self._active -= 1
                self._condition.notify_all()

    def close(self):
        # Call from a worker when used by an async host. A late model response
        # remains owned until its commit/failure has settled.
        with self._condition:
            self._closing = True
            while self._active:
                self._condition.wait()
        with self._instances_lock:
            if not self._closed:
                self._closed = True
                self._ownership.release()
                if self._instances.get(self.root) is self:
                    del self._instances[self.root]

    def _project(self, project_id, base_revision, *, story=False):
        revision = C.RevisionPayload(base_revision=base_revision).base_revision
        project = self._store.get(project_id)
        if project["revision"] != revision:
            raise NumericV2RevisionConflictError(project)
        if story and not isinstance(project.get("story"), dict):
            raise WorkshopError("project_story_required")
        return project

    @staticmethod
    def metric_presets():
        return preset_metric_catalog()

    def create_project(self):
        with self.operation(exclusive=False):
            return self._store.create()

    def list_projects(self):
        with self.operation(exclusive=False):
            return self._store.list()

    def get_project(self, project_id):
        with self.operation(exclusive=False):
            return self._store.get(project_id)

    def allocate_id(self, project_id, *, kind):
        payload = C.NumericV2AllocateIdPayload(kind=kind)
        with self.operation(exclusive=False):
            self._store.get(project_id)
            return {"id": f"{payload.kind}_{uuid.uuid4().hex[:10]}"}

    def import_project(self, project):
        # Pass a complete source JSON snapshot; never read an adjacent checkout
        # or a live author directory implicitly. The caller controls the source.
        payload = C.AuthorProjectPayload.model_validate(project)
        with self.operation(exclusive=False):
            return self._store.import_project(payload.model_dump(by_alias=True))

    def update_project(self, project_id, *, base_revision, changes):
        payload = C.ProjectUpdatePayload(base_revision=base_revision, changes=changes)
        with self.operation(exclusive=False):
            return self._store.update(project_id, **payload.model_dump())

    def delete_project(self, project_id, *, base_revision):
        revision = C.RevisionPayload(base_revision=base_revision).base_revision
        with self.operation(exclusive=False):
            self._store.delete(project_id, base_revision=revision)

    def import_story(self, story):
        payload = C.NumericV2ImportPayload(story=story)
        with self.operation(exclusive=False), self._store.transaction():
            compiled = self._compiler.compile(payload.story)
            project = self._store.import_story(compiled.story)
            return self._store.record_compile(project["project_id"], self._compile_receipt(compiled),
                                              base_revision=project["revision"])

    @_exclusive
    def generate(self, project_id, *, base_revision):
        project = self._project(project_id, base_revision)
        if not project["title"].strip():
            raise WorkshopError("generation_setup_invalid")
        setup = dict(project["setup"])
        setup["metrics"] = normalize_metric_drafts(list(setup.get("metrics") or []))
        setup = C.NumericV2SetupPayload.model_validate(setup).model_dump()
        checkpoint = self._store.generation_checkpoint(project_id)
        names = checkpoint.get("cast_names") if checkpoint is not None else self._names()
        self._store.begin_generation(project_id, base_revision=base_revision)
        try:
            result = self._generator.generate(title=project["title"], setup=setup,
                                              checkpoint=checkpoint, cast_names=names)
        except NumericV2GenerationError as error:
            self._store.fail_generation(project_id, base_revision=base_revision,
                error={"code": error.code, "details": {
                    "issues": error.issues, "provider": error.provider_details,
                    "attempts": error.attempts}},
                checkpoint=error.checkpoint)
            raise
        except Exception as error:
            self._store.fail_generation(project_id, base_revision=base_revision,
                error={"code": "generation_technical_failed", "exception_type": type(error).__name__},
                checkpoint=checkpoint)
            raise
        for field in ("relationship", "tone"):
            if field in (result.get("setup_updates") or {}):
                setup[field] = result["setup_updates"][field]
        updated = self._store.finish_generation(project_id, base_revision=base_revision,
            story=result["story"], setup=setup,
            mainline_node_ids=list(result.get("mainline_node_ids") or []),
            relationship_arc=result.get("relationship_arc") or {},
            character_state_arc=result.get("character_state_arc") or {},
            key_props=list(result.get("key_props") or []),
            pacing_diagnostics=result.get("pacing_diagnostics"))
        return {"project": updated}

    @_exclusive
    def assess_quality(self, project_id, *, base_revision):
        project = self._project(project_id, base_revision, story=True)
        report = self._quality.assess(story=project["story"], setup=project["setup"],
                                      authoring=project["authoring"])
        return {"project": self._store.record_quality_assessment(
            project_id, report, base_revision=base_revision)}

    @_exclusive
    def optimize_node(self, project_id, node_id, *, base_revision):
        project = self._project(project_id, base_revision, story=True)
        assessment = project["authoring"].get("quality_assessment")
        if not isinstance(assessment, dict):
            raise WorkshopError("quality_assessment_required")
        if assessment.get("stale"):
            raise WorkshopError("quality_assessment_stale")
        if assessment.get("repair_plan_version") != 3:
            raise WorkshopError("quality_reassessment_required")
        story = self._quality.optimize_node(story=project["story"], setup=project["setup"],
            authoring=project["authoring"], assessment=assessment, node_id=node_id)
        self._compiler.compile(story)
        return {"project": self._store.update(project_id, base_revision=base_revision,
                changes={"story": story}), "optimized_node_id": node_id}

    @_exclusive
    def enhance_node(self, project_id, node_id, *, base_revision):
        project = self._project(project_id, base_revision, story=True)
        enhancement = self._generator.enhance_node(story=project["story"], node_id=node_id,
            key_props=list(project["authoring"].get("key_props") or []))
        story = deepcopy(project["story"])
        node = next((n for n in story["nodes"] if n["id"] == node_id), None)
        if node is None:
            raise WorkshopError("node_not_found")
        beat = node.setdefault("story_beat", {})
        for field in ("opening_scene", "narrative_focus", "goals", "must_not_happen",
                      "catgirl_situation", "transition_goal", "character_state", "acting_contract"):
            if field in enhancement:
                beat[field] = deepcopy(enhancement[field])
        beat.pop("must_happen", None)
        return {"project": self._store.update(project_id, base_revision=base_revision,
                changes={"story": story}), "node_id": node_id}

    def set_mainline_order(self, project_id, *, base_revision, node_ids):
        payload = C.MainlineOrderPayload(base_revision=base_revision, node_ids=node_ids)
        with self.operation(exclusive=False):
            return self._store.set_mainline_order(project_id, **payload.model_dump())

    def branch_options(self, project_id, node_id):
        with self.operation(exclusive=False):
            return self._branch.options(self._store.get(project_id), node_id)

    def get_branch_draft(self, project_id, draft_id):
        with self.operation(exclusive=False):
            return self._store.get_branch_draft(project_id, draft_id)

    @_exclusive
    def draft_branch_ending(self, project_id, **payload):
        request = C.BranchEndingDraftPayload.model_validate(payload)
        project = self._project(project_id, request.base_revision, story=True)
        plan = self._branch.prepare_ending(project, **request.model_dump(exclude={"base_revision"}))
        generated = self._generator.generate_branch_ending(context=plan["context"])
        draft = self._branch.finish_ending(plan, generated)
        return {"draft": self._store.save_branch_draft(project_id,
            base_revision=request.base_revision, draft=draft)}

    @_exclusive
    def draft_branch_path(self, project_id, **payload):
        request = C.BranchPathDraftPayload.model_validate(payload)
        project = self._project(project_id, request.base_revision, story=True)
        ending = (self._store.get_branch_draft(project_id, request.ending_draft_id)
                  if request.endpoint_mode == "new_ending" else None)
        arguments = request.model_dump(exclude={"base_revision", "ending_draft_id", "ending"})
        plan = self._branch.prepare_path(project, **arguments,
                                        ending_draft=ending, confirmed_ending=request.ending.model_dump(exclude_unset=True)
                                        if request.ending else None)
        generated = self._generator.generate_branch_path(context=plan["context"])
        draft = self._branch.finish_path(plan, generated)
        return {"draft": self._store.save_branch_draft(project_id,
            base_revision=request.base_revision, draft=draft)}

    @_exclusive
    def apply_branch(self, project_id, draft_id, *, base_revision):
        C.BranchApplyPayload(base_revision=base_revision, draft_id=draft_id)
        project = self._store.get(project_id)
        draft = self._store.get_branch_draft(project_id, draft_id)
        if draft.get("status") == "applied":
            return {"project": project, "draft_id": draft_id,
                    "created_node_ids": draft.get("created_node_ids") or []}
        self._project(project_id, base_revision)
        story, semantics, props = self._branch.build_story(project, draft)
        self._compiler.compile(story)
        updated = self._store.commit_branch_draft(project_id, draft_id=draft_id,
            base_revision=base_revision, story=story, route_semantics=semantics, key_props=props)
        return {"project": updated, "draft_id": draft_id,
                "created_node_ids": draft.get("created_node_ids") or []}

    @staticmethod
    def _compile_receipt(compiled):
        return {"success": True, "package_hash": compiled.package_hash,
                "warnings": [asdict(w) for w in compiled.warnings]}

    @_exclusive
    def compile(self, project_id, *, base_revision):
        project = self._project(project_id, base_revision, story=True)
        try:
            compiled = self._compiler.compile(project["story"])
        except PackageError as error:
            self._store.record_compile(project_id,
                {"success": False, "issues": list(error.details.get("issues") or [])},
                base_revision=base_revision)
            raise
        project = self._store.record_compile(project_id, self._compile_receipt(compiled),
                                            base_revision=base_revision)
        return {"project": project, "package_hash": compiled.package_hash,
                "json_bytes": compiled.json_bytes}

    def _compiled_current(self, project):
        receipt = project.get("compile_result") or {}
        if (receipt.get("success") is not True or not receipt.get("package_hash")
                or receipt.get("revision") != project["revision"]):
            raise WorkshopError("current_compile_required")
        compiled = self._compiler.compile(project["story"])
        if compiled.package_hash != receipt["package_hash"]:
            raise WorkshopError("current_compile_required")
        return compiled

    @_exclusive
    def validate(self, project_id, *, base_revision):
        project = self._project(project_id, base_revision, story=True)
        compiled = self._compiled_current(project)
        verified = self._gateway.validate(compiled.json_bytes)
        if (verified.json_bytes != compiled.json_bytes
                or verified.package_hash != compiled.package_hash):
            raise WorkshopError("neko_validation_hash_mismatch")
        return self._store.record_validation(project_id,
            {"success": True, "package_hash": verified.package_hash,
             "story_id": verified.story_id}, base_revision=base_revision)

    def _publish_candidate(self, project_id, base_revision):
        project = self._project(project_id, base_revision, story=True)
        compiled = self._compiled_current(project)
        receipt = project.get("neko_validation") or {}
        if (receipt.get("success") is not True or receipt.get("revision") != project["revision"]
                or receipt.get("package_hash") != compiled.package_hash):
            raise WorkshopError("current_neko_validation_required")
        return PublishCandidate(project_id, base_revision, compiled.story_id,
                                compiled.package_hash, compiled.json_bytes)

    @_exclusive
    def export(self, project_id, *, base_revision):
        with self._store.transaction():
            return self._publish_candidate(project_id, base_revision)

    def _install_candidate(self, candidate, install):
        """Host already owns the story lifecycle lock on its serving event loop."""
        with self._store.transaction():
            current = self._publish_candidate(candidate.project_id, candidate.revision)
            if current != candidate:
                raise WorkshopError("neko_validation_hash_mismatch")
            result = install(json.loads(candidate.json_bytes))
            try:
                return self._store.record_install(candidate.project_id, result,
                                                   base_revision=candidate.revision)
            except Exception as error:
                raise WorkshopError("package_installed_receipt_failed", {
                    "installed": True, "story_id": candidate.story_id,
                    "package_hash": candidate.package_hash,
                    "exception_type": type(error).__name__,
                }) from error

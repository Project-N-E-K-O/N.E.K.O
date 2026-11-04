"""Exercise the installed host with an explicit, non-private model fixture."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from .host import open_workshop
from .sdk import LLMCallFailure, WorkshopError
from .sdk.generation.numeric_v2 import NumericV2GenerationError


def _require(condition, message):
    # Release builds may disable assertions.
    if not condition:
        raise RuntimeError(message)


class _IsolatedConfig:
    def __init__(self, root, names):
        self.app_docs_dir = root / "data"
        self.local_state_dir = root / "control"
        self.names = names
        self.mode = "normal"

    def load_root_state(self):
        return {"mode": self.mode}

    def ensure_local_state_directory(self):
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True

    def load_characters(self):
        return {"当前猫娘": self.names["catgirl_name"], "猫娘": {
            self.names["catgirl_name"]: {"_reserved": {"character_id": "character_" + "1" * 32}}},
            "主人": {"昵称": self.names["player_name"]}}


async def run(fixture):
    from services.theater.numeric_v2_registry import NumericV2PackageRegistry
    from utils.cloudsave_runtime import MaintenanceModeError

    calls = []
    chapter_calls = []
    resuming = False
    def model(messages, **options):
        calls.append(options["operation"])
        context = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_opening_state_extraction":
            index = next((i for i, chapter in enumerate(fixture["outline"]["mainline_chapters"])
                          if chapter["opening_scene"] == context["opening_scene"]), None)
            stage = (fixture["outline"]["character_state_arc"]["ending_stage"] if index is None else
                     fixture["outline"]["character_state_arc"]["stages"][index])
            result = {key: stage[key] for key in ("catgirl_state", "player_state", "environment_state")}
            if "catgirl_situation" in context["output_contract"]:
                result["catgirl_situation"] = fixture["outline"]["mainline_chapters"][index]["catgirl_situation"]
            return json.dumps(result, ensure_ascii=False)
        if options["operation"] == "numeric_v2_story_frame":
            candidate = deepcopy(fixture["outline"])
            candidate.pop("character_state_arc")
            candidate["relationship_arc"].pop("stages")
            candidate["mainline_chapters"] = [
                {key: chapter[key] for key in ("title", "narrative")}
                for chapter in candidate["mainline_chapters"]
            ]
            candidate["ending"] = {key: candidate["ending"][key] for key in ("type", "title", "summary")}
            return json.dumps(candidate, ensure_ascii=False)
        if options["operation"] == "numeric_v2_chapter_generation":
            index = context["chapter_index"] - 1
            chapter_calls.append(index)
            if index == 1 and not resuming:
                return LLMCallFailure("injected timeout", error_code="model_timeout", exception_type="TimeoutError")
            return json.dumps({"chapter": fixture["outline"]["mainline_chapters"][index],
                "relationship_stage": fixture["outline"]["relationship_arc"]["stages"][index],
                "character_state_stage": fixture["outline"]["character_state_arc"]["stages"][index]}, ensure_ascii=False)
        if options["operation"] != "numeric_v2_ending_generation":
            return LLMCallFailure("injected timeout", error_code="model_timeout", exception_type="TimeoutError")
        return json.dumps({"ending": fixture["outline"]["ending"],
            "ending_stage": fixture["outline"]["character_state_arc"]["ending_stage"]}, ensure_ascii=False)

    with tempfile.TemporaryDirectory(prefix="neko-workshop-smoke-") as temporary:
        config = _IsolatedConfig(Path(temporary), fixture["names"])
        host = await asyncio.to_thread(open_workshop, config, model_call=model)
        try:
            project = await host.call("create_project")
            allocated = await host.call("allocate_id", project["project_id"], kind="node")
            _require(allocated["id"].startswith("node_"), "allocate_id_failed")
            project = await host.call("update_project", project["project_id"], base_revision=project["revision"],
                                      changes={"title": fixture["title"], "setup": fixture["setup"]})
            try:
                await host.call("generate", project["project_id"], base_revision=project["revision"])
            except NumericV2GenerationError:
                pass
            else:
                raise RuntimeError("fixture_failure_not_exercised")
            failed = await host.call("get_project", project["project_id"])
            _require(failed["generation_checkpoint"] is not None, "checkpoint_missing")
            await host.close()
            resuming = True
            host = await asyncio.to_thread(open_workshop, config, model_call=model)
            result = await host.call("generate", project["project_id"], base_revision=project["revision"])
            generated = result["project"]
            _require(calls.count("numeric_v2_story_frame") == 1, "resume_restarted_outline")
            _require(chapter_calls.count(0) == 1, "resume_restarted_completed_chapter")
            compiled = await host.call("compile", project["project_id"], base_revision=generated["revision"])
            await host.call("validate", project["project_id"], base_revision=generated["revision"])
            exported = await host.call("export", project["project_id"], base_revision=generated["revision"])
            _require(exported.json_bytes == compiled["json_bytes"], "export_bytes_changed")
            installed = await host.install(project["project_id"], base_revision=generated["revision"])
            registry = NumericV2PackageRegistry(config.app_docs_dir / "theater/numeric_v2/packages")
            _require(len(registry.list_packages()) == 1, "installed_package_missing")
            _require(registry.load_engine(exported.story_id).compiled.package_hash == exported.package_hash,
                     "installed_package_not_playable")
            await host.close()
            host = await asyncio.to_thread(open_workshop, config, model_call=model)
            reopened = await host.call("get_project", project["project_id"])
            _require(reopened == installed, "reopen_project_changed")
            config.mode = "maintenance_readonly"
            try:
                await host.call("update_project", project["project_id"], base_revision=reopened["revision"],
                                changes={"title": "must not be written"})
            except MaintenanceModeError:
                pass
            else:
                raise RuntimeError("maintenance_write_was_allowed")
            _require(await host.call("get_project", project["project_id"]) == reopened,
                     "maintenance_mutated_project")
            snapshot = json.loads((host.sdk.root / f'{project["project_id"]}.json').read_bytes())
            await host.close()
            imported_config = _IsolatedConfig(Path(temporary) / "imported", fixture["names"])
            host = await asyncio.to_thread(open_workshop, imported_config, model_call=model)
            imported = await host.call("import_project", snapshot)
            _require(imported["revision"] == reopened["revision"]
                     and imported["authoring"] == reopened["authoring"], "author_import_changed")
            try:
                await host.call("export", imported["project_id"], base_revision=imported["revision"])
            except WorkshopError as error:
                _require(error.code == "current_compile_required", "unexpected_import_publish_error")
            else:
                raise RuntimeError("import_trusted_old_publish_receipts")
            await host.call("compile", imported["project_id"], base_revision=imported["revision"])
            await host.call("validate", imported["project_id"], base_revision=imported["revision"])
            imported_package = await host.call("export", imported["project_id"], base_revision=imported["revision"])
            _require(imported_package.json_bytes == exported.json_bytes, "import_changed_package")
            return {"success": True, "marker": "NEKO_THEATER_WORKSHOP_SMOKE_OK",
                    "package_hash": exported.package_hash, "model_operations": calls,
                    "checks": ["generate_failure", "resume", "compile", "validate", "export",
                               "install", "load_engine", "reopen", "maintenance", "allocate_id",
                               "import_project", "import_revalidation"]}
        finally:
            await host.close()


def main(fixture_path):
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    print(json.dumps(asyncio.run(run(fixture)), ensure_ascii=False))
    return 0

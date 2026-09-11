"""SDK/real host boundaries; every project/package lives under tmp_path."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event

import pytest

from theater_workshop.host import open_workshop, InProcessPackageGateway, NekoWorkshopModel
from theater_workshop.sdk import WorkshopError, NumericV2RevisionConflictError, ModelReply
from theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError
from services.theater.numeric_v2_registry import NumericV2PackageRegistry, NumericV2PackageExistsError
from services.theater.numeric_v2_store import numeric_v2_story_session_guard
from utils.cloudsave_runtime import MaintenanceModeError

from .numeric_v2_fixture import numeric_v2_story
from .test_numeric_v2_generation import _generation_setup, _quality_payload, _quality_reply
from .test_numeric_v2_names import NAMES, named_outline


class TestConfig:
    __test__ = False

    def __init__(self, root):
        self.app_docs_dir = Path(root) / "data"
        self.local_state_dir = Path(root) / "control"
        self.mode = "normal"
        self.names = dict(NAMES)

    def load_root_state(self):
        return {"mode": self.mode}

    def ensure_local_state_directory(self):
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True

    def load_characters(self):
        return {"当前猫娘": self.names["catgirl_name"],
                "猫娘": {self.names["catgirl_name"]: {"_reserved": {
                    "character_id": "character_" + "1" * 32}}},
                "主人": {"昵称": self.names["player_name"]}}


def fixed_model(messages, **options):
    return ModelReply(json.dumps(named_outline(), ensure_ascii=False), "fixture-model",
                      {"prompt_tokens": 17, "completion_tokens": 23, "total_tokens": 40})


@pytest.fixture
def opened(tmp_path):
    config = TestConfig(tmp_path)
    host = open_workshop(config, model_call=fixed_model)
    yield host, config
    host.sdk.close()


def setup_project(sdk):
    project = sdk.create_project()
    return sdk.update_project(project["project_id"], base_revision=project["revision"],
        changes={"title": "旧信", "setup": _generation_setup()})


def ready_to_publish(sdk):
    project = sdk.import_story(numeric_v2_story())
    return sdk.validate(project["project_id"], base_revision=project["revision"])


def test_import_has_no_host_config_server_io_or_model_side_effects(tmp_path):
    root = Path(__file__).resolve().parents[3]
    script = """
import builtins, sys
old_import = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if level == 0 and name.split('.')[0] in {'flask','flask_cors','config','base_agent','services','utils','theater_generator'}:
        raise AssertionError(name)
    return old_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
import theater_workshop.sdk
assert theater_workshop.sdk.TheaterWorkshop._instances == {}
print('pure-sdk-import')
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "pure-sdk-import" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_same_root_reuses_instance_and_explicit_model_change_requires_reopen(opened):
    host, config = opened
    assert open_workshop(config, model_call=fixed_model) is host
    assert open_workshop(config) is host
    with pytest.raises(WorkshopError, match="workshop_model_mismatch"):
        open_workshop(config, model_config={"model": "different"})


def test_concurrent_edits_only_one_accepts_old_revision(opened):
    sdk = opened[0].sdk
    project = sdk.create_project()
    barrier = Barrier(2)
    def edit(title):
        barrier.wait(timeout=5)
        try:
            return sdk.update_project(project["project_id"], base_revision=1, changes={"title": title})
        except NumericV2RevisionConflictError:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(edit, ["first", "second"]))
    assert sum(result is not None for result in results) == 1
    assert sdk.get_project(project["project_id"])["revision"] == 2


def test_generation_names_usage_and_explicit_resume_are_preserved(opened):
    host, config = opened
    project = setup_project(host.sdk)
    result = host.sdk.generate(project["project_id"], base_revision=project["revision"])
    assert result["project"]["story"]["intro"]["player_name"] == NAMES["player_name"]
    assert result["project"]["story"]["intro"]["catgirl_name"] == NAMES["catgirl_name"]
    assert result["project"]["story"]["initial_state"]["player_address_known"] is False
    assert result["usage"][0]["total_tokens"] == 40
    assert result["usage"][0]["operation"] == "numeric_v2_mainline_generation"
    assert result["project"]["authoring"]["quality_assessment"] is None


def test_busy_late_generation_cannot_overwrite_an_edit(tmp_path):
    entered, release = Event(), Event()
    def model(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return fixed_model(*args, **kwargs)
    host = open_workshop(TestConfig(tmp_path), model_call=model)
    project = setup_project(host.sdk)
    try:
        with ThreadPoolExecutor(1) as pool:
            job = pool.submit(host.sdk.generate, project["project_id"], base_revision=project["revision"])
            assert entered.wait(5)
            try:
                with pytest.raises(WorkshopError, match="project_busy"):
                    host.sdk.generate(project["project_id"], base_revision=project["revision"])
                edited = host.sdk.update_project(project["project_id"], base_revision=project["revision"],
                                                   changes={"title": "保留用户新稿"})
            finally:
                release.set()
            with pytest.raises(NumericV2RevisionConflictError):
                job.result(timeout=5)
        current = host.sdk.get_project(project["project_id"])
        assert current == edited
        assert current["status"] != "generating"
    finally:
        host.sdk.close()


@pytest.mark.parametrize("change", ["maintenance", "root"])
def test_model_wait_does_not_hold_fence_and_late_commit_checks_root(tmp_path, change):
    entered, release = Event(), Event()
    config = TestConfig(tmp_path)
    def model(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return fixed_model(*args, **kwargs)
    host = open_workshop(config, model_call=model)
    project = setup_project(host.sdk)
    try:
        with ThreadPoolExecutor(1) as pool:
            job = pool.submit(host.sdk.generate, project["project_id"], base_revision=project["revision"])
            assert entered.wait(5)
            before = (host.sdk.root / f'{project["project_id"]}.json').read_bytes()
            try:
                if change == "maintenance":
                    config.mode = "maintenance_readonly"
                    expected = MaintenanceModeError
                else:
                    config.app_docs_dir = tmp_path / "new-root"
                    expected = WorkshopError
            finally:
                release.set()
            with pytest.raises(expected):
                job.result(timeout=5)
        assert (host.sdk.root / f'{project["project_id"]}.json').read_bytes() == before
        assert not (tmp_path / "new-root").exists()
    finally:
        host.sdk.close()


def test_maintenance_blocks_all_author_mutations_before_model(opened):
    host, config = opened
    project = ready_to_publish(host.sdk)
    before = {p.name: p.read_bytes() for p in host.sdk.root.glob("*.json")}
    config.mode = "maintenance_readonly"
    operations = [lambda: host.sdk.create_project(),
        lambda: host.sdk.update_project(project["project_id"], base_revision=project["revision"], changes={"title":"x"}),
        lambda: host.sdk.delete_project(project["project_id"], base_revision=project["revision"]),
        lambda: host.sdk.compile(project["project_id"], base_revision=project["revision"]),
        lambda: host.sdk.assess_quality(project["project_id"], base_revision=project["revision"]),
        lambda: host.sdk.import_story(numeric_v2_story())]
    for operation in operations:
        with pytest.raises(MaintenanceModeError):
            operation()
    assert {p.name: p.read_bytes() for p in host.sdk.root.glob("*.json")} == before
    assert len(host.sdk.list_projects()) == 1


def test_reopen_interrupts_running_without_losing_checkpoint(opened):
    host, config = opened
    project = setup_project(host.sdk)
    checkpoint = {"candidate": named_outline(), "cast_names": NAMES, "issues": [{"path":"ending.title"}]}
    host.sdk._store.fail_generation(project["project_id"], base_revision=project["revision"],
        error={"code":"fixture"}, checkpoint=checkpoint)
    host.sdk._store.begin_generation(project["project_id"], base_revision=project["revision"])
    host.sdk.close()
    reopened = open_workshop(config, model_call=fixed_model)
    try:
        current = reopened.sdk.get_project(project["project_id"])
        assert current["generation_state"] == "interrupted"
        assert reopened.sdk._store.generation_checkpoint(project["project_id"]) == checkpoint
        assert current["story"] is None
    finally:
        reopened.sdk.close()


def test_second_process_rejected_before_data_or_model_and_can_reopen(opened):
    host, config = opened
    project = setup_project(host.sdk)
    before = (host.sdk.root / f'{project["project_id"]}.json').read_bytes()
    script = """
import sys
from tests.unit.theater_workshop.test_sdk_lifecycle import TestConfig, fixed_model
from theater_workshop.host import open_workshop
from theater_workshop.sdk import WorkshopError
try:
    h = open_workshop(TestConfig(sys.argv[1]), model_call=fixed_model)
except WorkshopError as e:
    print(e.code)
else:
    print('opened')
    h.sdk.close()
"""
    def probe():
        return subprocess.run([sys.executable, "-c", script, str(config.app_docs_dir.parent)],
                              capture_output=True, text=True, timeout=20)
    blocked = probe()
    assert blocked.returncode == 0, blocked.stderr
    assert "workshop_root_in_use" in blocked.stdout
    assert (host.sdk.root / f'{project["project_id"]}.json').read_bytes() == before
    host.sdk.close()
    reopened = probe()
    assert reopened.returncode == 0, reopened.stderr
    assert "opened" in reopened.stdout


@pytest.mark.parametrize("damage", ["compile_missing", "compile_failed", "hash_missing", "hash_different",
                                    "validation_missing", "validation_failed", "validation_old", "revision_old"])
def test_publish_rejects_missing_stale_or_mismatched_proof(opened, damage):
    sdk = opened[0].sdk
    project = ready_to_publish(sdk)
    path = sdk.root / f'{project["project_id"]}.json'
    row = json.loads(path.read_bytes())
    if damage == "compile_missing": row["compile_result"] = None
    elif damage == "compile_failed": row["compile_result"]["success"] = False
    elif damage == "hash_missing":
        row["compile_result"]["package_hash"] = None
        row["neko_validation"]["package_hash"] = None
    elif damage == "hash_different": row["neko_validation"]["package_hash"] = "sha256:wrong"
    elif damage == "validation_missing": row["neko_validation"] = None
    elif damage == "validation_failed": row["neko_validation"]["success"] = False
    elif damage == "validation_old": row["neko_validation"]["revision"] -= 1
    else: row["compile_result"]["revision"] -= 1
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(WorkshopError):
        sdk.export(project["project_id"], base_revision=project["revision"])


def test_layout_carries_verified_proof_but_content_invalidates_it(opened):
    sdk = opened[0].sdk
    project = ready_to_publish(sdk)
    first = sdk.export(project["project_id"], base_revision=project["revision"])
    layout = sdk.update_project(project["project_id"], base_revision=project["revision"],
        changes={"editor": {"node_positions": {"opening": {"x": 1, "y": 2}}}})
    second = sdk.export(project["project_id"], base_revision=layout["revision"])
    assert second.json_bytes == first.json_bytes
    assert layout["neko_validation"]["revision"] == layout["revision"]
    changed = sdk.update_project(project["project_id"], base_revision=layout["revision"], changes={"title":"新版"})
    assert changed["compile_result"] is None
    assert changed["neko_validation"] is None


@pytest.mark.asyncio
async def test_install_uses_same_lifecycle_lock_and_produces_playable_package(opened):
    host, config = opened
    project = ready_to_publish(host.sdk)
    story_id = project["story"]["meta"]["story_id"]
    root = config.app_docs_dir / "theater"
    async with numeric_v2_story_session_guard(root, story_id):
        install = asyncio.create_task(host.install(project["project_id"], base_revision=project["revision"]))
        await asyncio.sleep(0.03)
        assert not install.done()
        assert not (root / "numeric_v2/packages").exists()
    result = await install
    registry = NumericV2PackageRegistry(root / "numeric_v2/packages")
    assert registry.list_packages()[0]["package_hash"] == result["install_result"]["package_hash"]
    assert registry.load_engine(story_id).compiled.story_id == story_id
    with pytest.raises(NumericV2PackageExistsError):
        await host.install(project["project_id"], base_revision=project["revision"])


@pytest.mark.asyncio
async def test_installed_receipt_failure_recovers_only_matching_package(opened, monkeypatch):
    host, config = opened
    project = ready_to_publish(host.sdk)
    original = host.sdk._store.record_install
    def fail(*args, **kwargs): raise OSError("injected receipt failure")
    monkeypatch.setattr(host.sdk._store, "record_install", fail)
    with pytest.raises(WorkshopError, match="package_installed_receipt_failed") as caught:
        await host.install(project["project_id"], base_revision=project["revision"])
    assert caught.value.details["installed"] is True
    monkeypatch.setattr(host.sdk._store, "record_install", original)
    recovered = await host.install(project["project_id"], base_revision=project["revision"], recover_receipt=True)
    assert recovered["install_result"]["package_hash"] == project["neko_validation"]["package_hash"]
    path = config.app_docs_dir / "theater/numeric_v2/packages" / f'{project["story"]["meta"]["story_id"]}.json'
    other = deepcopy(project["story"]); other["meta"]["title"] = "其他包"
    path.write_bytes(InProcessPackageGateway().compile(other).json_bytes)
    with pytest.raises(WorkshopError, match="installed_package_hash_mismatch"):
        await host.install(project["project_id"], base_revision=project["revision"], recover_receipt=True)


@pytest.mark.asyncio
async def test_maintenance_install_writes_no_formal_package(opened):
    host, config = opened
    project = ready_to_publish(host.sdk)
    config.mode = "maintenance_readonly"
    with pytest.raises(MaintenanceModeError):
        await host.install(project["project_id"], base_revision=project["revision"])
    assert not (config.app_docs_dir / "theater/numeric_v2/packages").exists()


@pytest.mark.parametrize("api_key", ["test-key", "", None])
def test_model_configuration_is_explicit_and_request_budget_has_no_hidden_retry(monkeypatch, api_key):
    from types import SimpleNamespace
    from utils import llm_client
    calls = []
    class Client:
        def invoke(self, messages, **options):
            calls.append(options)
            return SimpleNamespace(content='{}', response_metadata={"token_usage": {
                "input_tokens": 7, "output_tokens": 5, "cache_read_input_tokens": 3}})
        def close(self): pass
    def create(**kwargs):
        calls.append(kwargs)
        return Client()
    monkeypatch.setattr(llm_client, "create_chat_llm", create)
    options = dict(max_tokens=16000, max_retries=1, response_format={"type":"json_object"},
                   thinking={"type":"disabled"}, operation="numeric_v2_mainline_generation", temperature=.35)
    with pytest.raises(WorkshopError, match="workshop_model_required"):
        NekoWorkshopModel({})([], **options)
    with pytest.raises(WorkshopError, match="workshop_model_endpoint_required"):
        NekoWorkshopModel({"model":"selected-model", "api_key":"test-key"})([], **options)
    reply = NekoWorkshopModel({"model":"selected-model", "api_key":api_key,
                              "max_input_tokens":16000,
                              "base_url":"https://example.invalid/v1"})([], **options)
    assert reply.model == "selected-model"
    assert reply.usage["prompt_tokens"] == 10
    assert reply.usage["total_tokens"] == 15
    assert calls[0]["max_retries"] == 0
    assert calls[0]["max_completion_tokens"] == 16000
    assert calls[0]["timeout"] == 120
    assert calls[0]["api_key"] == (api_key or "")
    assert calls[1] == {"response_format":{"type":"json_object"}}


@pytest.mark.parametrize("budget", [None, True, 0, -1, 1.5, "16000"])
def test_model_requires_explicit_positive_input_budget_before_opening_client(monkeypatch, budget):
    from utils import llm_client

    def unexpected_client(**kwargs):
        pytest.fail("invalid input budget must not open a provider client")
    monkeypatch.setattr(llm_client, "create_chat_llm", unexpected_client)
    model = NekoWorkshopModel({"model": "selected-model", "api_key": "test-key",
        "base_url": "https://example.invalid/v1", "max_input_tokens": budget})
    with pytest.raises(WorkshopError, match="workshop_model_input_budget_required"):
        model([], max_tokens=100, max_retries=1, response_format={"type": "json_object"},
              thinking=None, operation="numeric_v2_mainline_generation")


@pytest.mark.parametrize("operation", ["numeric_v2_mainline_generation", "numeric_v2_quality_assessment"])
@pytest.mark.parametrize("margin", [-1, 0, 1])
def test_model_counts_complete_input_and_never_truncates_it(monkeypatch, operation, margin):
    from types import SimpleNamespace
    from utils import llm_client
    from utils.tokenize import count_tokens

    messages = [{"role": "system", "content": "保留完整作者合同。"},
                {"role": "user", "content": "Long story context. " * 80 + "最终不要离开。"}]
    original = deepcopy(messages)
    size = count_tokens(json.dumps(messages, ensure_ascii=False))
    calls = []
    class Client:
        def invoke(self, actual, **options):
            assert actual == original
            calls.append("invoke")
            return SimpleNamespace(content="{}", response_metadata={})
        def close(self):
            calls.append("close")
    def create(**kwargs):
        calls.append("create")
        return Client()
    monkeypatch.setattr(llm_client, "create_chat_llm", create)
    model = NekoWorkshopModel({"model": "selected-model", "api_key": "test-key",
        "base_url": "https://example.invalid/v1", "max_input_tokens": size + margin})
    options = dict(max_tokens=100, max_retries=1, response_format={"type": "json_object"},
                   thinking=None, operation=operation)
    if margin < 0:
        with pytest.raises(WorkshopError, match="workshop_model_input_budget_exceeded"):
            model(messages, **options)
        assert calls == []
    else:
        assert model(messages, **options).content == "{}"
        assert calls == ["create", "invoke", "close"]
    assert messages == original


def test_over_budget_generation_and_quality_preserve_author_content(tmp_path, monkeypatch):
    from utils import llm_client

    config = TestConfig(tmp_path)
    author = open_workshop(config, model_call=fixed_model)
    try:
        draft = setup_project(author.sdk)
        project = author.sdk.generate(draft["project_id"], base_revision=draft["revision"])["project"]
    finally:
        author.sdk.close()
    def unexpected_client(**kwargs):
        pytest.fail("oversized author input must not reach a provider")
    monkeypatch.setattr(llm_client, "create_chat_llm", unexpected_client)
    host = open_workshop(config, model_config={"model": "selected-model",
        "api_key": "test-key", "base_url": "https://example.invalid/v1", "max_input_tokens": 1})
    try:
        with pytest.raises(WorkshopError, match="workshop_model_input_budget_exceeded"):
            host.sdk.assess_quality(project["project_id"], base_revision=project["revision"])
        assert host.sdk.get_project(project["project_id"]) == project

        with pytest.raises(WorkshopError, match="workshop_model_input_budget_exceeded"):
            host.sdk.generate(project["project_id"], base_revision=project["revision"])
        failed = host.sdk.get_project(project["project_id"])
        for field in ("revision", "story", "setup"):
            assert failed[field] == project[field]
        assert failed["generation_state"] == "failed"
    finally:
        host.sdk.close()


def test_public_quality_revision_and_failure_preserve_previous_report(tmp_path):
    from theater_workshop.sdk.generation.quality import QualityAssessmentError
    operations = []
    broken = False
    replacement = "小岚把旧信放到桌面，说明日期与当年的误会有关。"
    def model(messages, **options):
        operation = options["operation"]
        operations.append(operation)
        if operation == "numeric_v2_mainline_generation":
            return fixed_model(messages, **options)
        if broken:
            return "invalid JSON"
        if operation == "numeric_v2_quality_single_node_optimization":
            return json.dumps({"node_updates": [{"node_id": "mainline_02",
                "story_beat": {"summary": replacement}}]}, ensure_ascii=False)
        return _quality_reply(_quality_payload(weak_dimension="plot", target_node_id="mainline_02"))(messages, **options)
    host = open_workshop(TestConfig(tmp_path), model_call=model)
    try:
        project = setup_project(host.sdk)
        generated = host.sdk.generate(project["project_id"], base_revision=project["revision"])["project"]
        from tests.unit.test_theater_numeric_v2_fixed_narration import _piece, REPORT
        story = deepcopy(generated["story"])
        story["nodes"][1]["story_beat"]["fixed_narrations"] = [_piece("report", REPORT, entry=True)]
        generated = host.sdk.update_project(project["project_id"], base_revision=generated["revision"], changes={"story": story})
        scored = host.sdk.assess_quality(project["project_id"], base_revision=generated["revision"])["project"]
        assert scored["story"] == generated["story"]
        assert scored["revision"] == generated["revision"]
        broken = True
        with pytest.raises(QualityAssessmentError):
            host.sdk.assess_quality(project["project_id"], base_revision=scored["revision"])
        assert host.sdk.get_project(project["project_id"]) == scored
        broken = False
        revised = host.sdk.optimize_node(project["project_id"], "mainline_02", base_revision=scored["revision"])["project"]
        expected = deepcopy(scored["story"])
        next(n for n in expected["nodes"] if n["id"] == "mainline_02")["story_beat"]["summary"] = replacement
        assert revised["story"] == expected
        assert operations.count("numeric_v2_quality_single_node_optimization") == 1
        assert revised["authoring"]["quality_assessment"]["stale"] is True
    finally:
        host.sdk.close()


@pytest.mark.parametrize("new_ending", [False, True])
def test_public_branch_preview_explicit_application_and_idempotency(tmp_path, new_ending):
    from tests.unit.test_theater_numeric_v2_fixed_narration import _piece, REPORT
    fixed = [_piece("report", REPORT, entry=True)]
    from .test_numeric_v2_branch import (
        branchable_project, _path_result_from_context, _ending_goal, _character_state,
    )
    calls = []
    def model(messages, **options):
        calls.append(options["operation"])
        context = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_branch_ending":
            return json.dumps({"condition_key": context["condition_candidates"][0]["key"],
                "title":"没有寄出的信", "summary":"两人公开误会的来源，并决定共同承担后果。",
                "opening_scene":"没有寄出的信静静放在晨光下。",
                "ordered_goals":[_ending_goal("结局开场已经展示旧信公开和两人共同承担后果")],
                "irreversible_facts":["误会来源已经公开"], "character_state":_character_state(),
                "catgirl_situation":"她不再独自保守秘密。", "tone":"释然", "fixed_narrations":fixed}, ensure_ascii=False)
        path_result = _path_result_from_context(context)
        path_result["scenes"][0]["fixed_narrations"] = fixed
        return json.dumps(path_result, ensure_ascii=False)
    host = open_workshop(TestConfig(tmp_path), model_call=model)
    try:
        data = branchable_project()
        project = host.sdk.import_story(data["story"])
        project = host.sdk.set_mainline_order(project["project_id"], base_revision=project["revision"],
                                             node_ids=data["authoring"]["mainline_node_ids"])
        assert project["story"] == data["story"]
        pid, revision = project["project_id"], project["revision"]
        if new_ending:
            ending = host.sdk.draft_branch_ending(pid, base_revision=revision, source_node_id="main_1",
                ending_direction="两人公开真相并共同承担代价。", condition_selection={"mode":"recommend"})["draft"]
            confirmed = deepcopy(ending["ending"])
            confirmed["title"] = "作者确认后的结局"
            draft = host.sdk.draft_branch_path(pid, base_revision=revision,
                endpoint_mode="new_ending", ending_draft_id=ending["draft_id"], ending=confirmed,
                direction="", length=1)["draft"]
            assert draft["new_ending"]["title"] == confirmed["title"]
            assert calls == ["numeric_v2_branch_ending", "numeric_v2_branch_path"]
        else:
            options = host.sdk.branch_options(pid, "main_3")
            draft = host.sdk.draft_branch_path(pid, base_revision=revision, source_node_id="main_3",
                endpoint_mode="mainline", endpoint_node_id="main_4", length=1,
                direction="误会让两人转而调查旧照片。",
                condition_selection={"mode":"fixed", "key":options["condition_candidates"][1]["key"]})["draft"]
            assert calls == ["numeric_v2_branch_path"]
        assert host.sdk.get_project(pid)["revision"] == revision
        applied = host.sdk.apply_branch(pid, draft["draft_id"], base_revision=revision)["project"]
        duplicate = host.sdk.apply_branch(pid, draft["draft_id"], base_revision=revision)["project"]
        assert applied == duplicate
        assert applied["revision"] == revision + 1
        fixed_nodes = [n for n in applied["story"]["nodes"] if "fixed_narrations" in n["story_beat"]]
        assert len(fixed_nodes) == (2 if new_ending else 1)
        assert all(n["story_beat"]["fixed_narrations"] == fixed for n in fixed_nodes)
    finally:
        host.sdk.close()


@pytest.mark.asyncio
async def test_cancelled_install_keeps_story_lock_until_worker_settles(opened, monkeypatch):
    host, config = opened
    project = ready_to_publish(host.sdk)
    entered, release = Event(), Event()
    original = NumericV2PackageRegistry.import_package
    def slow(registry, story):
        entered.set()
        assert release.wait(5)
        return original(registry, story)
    monkeypatch.setattr(NumericV2PackageRegistry, "import_package", slow)
    job = asyncio.create_task(host.install(project["project_id"], base_revision=project["revision"]))
    assert await asyncio.to_thread(entered.wait, 5)
    acquired = Event()
    async def competitor():
        async with numeric_v2_story_session_guard(config.app_docs_dir / "theater",
                                                 project["story"]["meta"]["story_id"]):
            acquired.set()
    other = asyncio.create_task(competitor())
    try:
        job.cancel()
        await asyncio.sleep(.03)
        assert not acquired.is_set()
        assert not job.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await job
    await other
    assert acquired.is_set()
    assert host.sdk.get_project(project["project_id"])["install_result"] is not None


def test_release_smoke_and_build_entries_cover_sdk(tmp_path):
    from theater_workshop.release_smoke import run
    fixture = dict(title="旧信", setup=_generation_setup(), outline=named_outline(), names=NAMES)
    result = asyncio.run(run(fixture))
    assert result["success"] is True
    assert {"resume", "reopen", "maintenance", "load_engine"}.issubset(result["checks"])
    repo = Path(__file__).resolve().parents[3]
    for filename, count in (("build-desktop.yml", 2), ("build-desktop-linux.yml", 1)):
        workflow = (repo / ".github/workflows" / filename).read_text(encoding="utf-8")
        assert workflow.count("--include-package=theater_workshop") == count
        assert "scripts/check_theater_workshop_release.py --binary-dir dist/Xiao8" in workflow


def test_close_waits_for_inflight_generation_before_releasing_writer(tmp_path):
    entered, release, closed = Event(), Event(), Event()
    config = TestConfig(tmp_path)
    def model(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return fixed_model(*args, **kwargs)
    host = open_workshop(config, model_call=model)
    project = setup_project(host.sdk)
    def close():
        host.sdk.close()
        closed.set()
    try:
        with ThreadPoolExecutor(2) as pool:
            job = pool.submit(host.sdk.generate, project["project_id"], base_revision=project["revision"])
            assert entered.wait(5)
            closing = pool.submit(close)
            try:
                assert not closed.wait(.03)
                with pytest.raises(WorkshopError, match="workshop_clos"):
                    open_workshop(config, model_call=model)
            finally:
                release.set()
            generated = job.result(timeout=5)["project"]
            closing.result(timeout=5)
        assert closed.is_set()
        reopened = open_workshop(config, model_call=model)
        try:
            assert reopened.sdk.get_project(project["project_id"]) == generated
        finally:
            reopened.sdk.close()
    finally:
        release.set()
        host.sdk.close()


def test_process_exit_releases_writer_without_clean_close(tmp_path):
    script = """
import json, os, sys
from tests.unit.theater_workshop.test_sdk_lifecycle import TestConfig, fixed_model, setup_project
from theater_workshop.host import open_workshop
h = open_workshop(TestConfig(sys.argv[1]), model_call=fixed_model)
p = setup_project(h.sdk)
h.sdk._store.begin_generation(p['project_id'], base_revision=p['revision'])
print(json.dumps({'project_id': p['project_id']}), flush=True)
os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    project_id = json.loads(result.stdout.splitlines()[-1])["project_id"]
    host = open_workshop(TestConfig(tmp_path), model_call=fixed_model)
    try:
        assert host.sdk.get_project(project_id)["generation_state"] == "interrupted"
    finally:
        host.sdk.close()


@pytest.mark.parametrize("project_id", ["project_../../outside", "project_/../outside", "project_..\\outside"])
def test_project_ids_cannot_escape_author_root(opened, project_id):
    from theater_workshop.sdk import NumericV2ProjectNotFoundError
    with pytest.raises(NumericV2ProjectNotFoundError):
        opened[0].sdk.get_project(project_id)


def test_failed_model_usage_is_available_on_error(opened):
    host, config = opened
    from theater_workshop.sdk import LLMCallFailure
    host.sdk._generator._model_call = lambda *args, **kwargs: ModelReply(
        LLMCallFailure("timeout", error_code="model_timeout", exception_type="TimeoutError"), "test-model")
    project = setup_project(host.sdk)
    with pytest.raises(NumericV2GenerationError) as caught:
        host.sdk.generate(project["project_id"], base_revision=project["revision"])
    assert caught.value.attempts == len(caught.value.usage) == 3
    assert all(record["usage_reported"] is False for record in caught.value.usage)
    assert host.sdk.get_project(project["project_id"])["generation_error"]["details"]["attempts"] == 3


def test_fixed_narration_generation_import_export_and_author_update(tmp_path):
    from tests.unit.test_theater_numeric_v2_fixed_narration import _piece, REPORT
    outline = named_outline()
    fixed = [_piece('report', REPORT, entry=True)]
    outline['mainline_chapters'][0]['fixed_narrations'] = deepcopy(fixed)
    outline['ending']['fixed_narrations'] = deepcopy(fixed)
    host = open_workshop(TestConfig(tmp_path), model_call=lambda *a, **kw: json.dumps(outline, ensure_ascii=False))
    try:
        sdk = host.sdk
        setup = setup_project(sdk)
        project = sdk.generate(setup['project_id'], base_revision=setup['revision'])['project']
        assert project['story']['nodes'][0]['story_beat']['fixed_narrations'] == fixed
        assert project['story']['nodes'][-1]['story_beat']['fixed_narrations'] == fixed
        sdk.compile(project['project_id'], base_revision=project['revision'])
        checked = sdk.validate(project['project_id'], base_revision=project['revision'])
        exported = sdk.export(project['project_id'], base_revision=checked['revision'])
        imported = sdk.import_story(json.loads(exported.json_bytes))
        assert imported['story']['nodes'][0]['story_beat']['fixed_narrations'] == fixed
        edited_story = deepcopy(imported['story'])
        edited_story['nodes'][0]['story_beat']['fixed_narrations'][0]['text'] += '\n作者追加原文。'
        edited = sdk.update_project(imported['project_id'], base_revision=imported['revision'], changes={'story': edited_story})
        sdk.compile(edited['project_id'], base_revision=edited['revision'])
        checked = sdk.validate(edited['project_id'], base_revision=edited['revision'])
        exported = sdk.export(edited['project_id'], base_revision=checked['revision'])
        assert json.loads(exported.json_bytes)['nodes'][0]['story_beat']['fixed_narrations'][0]['text'].endswith('作者追加原文。')
    finally:
        host.sdk.close()

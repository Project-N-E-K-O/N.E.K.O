"""Static profile sync uses synthetic identities/configs and in-memory HTTP only."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.profile_protocol import decode_character_profile, encode_character_profile
from services.chat_sync.profile_sync import DesktopProfileAdapter, ProfileStore, export_profile
from services.chat_sync.ledger import ChatDomain
from tests.unit.test_chat_sync_runtime import DOMAIN, OWNER, OTHER_OWNER, UID, SyntheticService, setup_runtime


PROFILE = {"schemaVersion": 1, "name": "Synthetic", "nickname": "昵称🙂", "systemPrompt": " raw\nsetting ",
           "fields": [{"key": "年龄", "value": "15"}, {"key": "设定", "value": " literal\n字段 "}], "persona": None}


def head(profile=PROFILE, revision=1):
    """Hash the independently canonical profile that a disposable service publishes."""
    return {"ownerId": OWNER, "characterSyncId": UID, "revision": revision, "profile": copy.deepcopy(profile),
            "profileHash": hashlib.sha256(encode_character_profile(profile)).hexdigest() if profile else None}


class Config:
    """Model only raw saved settings; no singleton or real filesystem is accessed."""

    def __init__(self):
        self.data = {"当前猫娘": "Other", "猫娘": {
            "Synthetic": {"年龄": 15, "昵称": "昵称🙂", "设定": " literal\n字段 ", "档案名": "kept legacy name",
                          "_reserved": {"character_uid": UID, "character_id": "character_" + "e" * 32,
                                        "voice_id": "local-voice", "system_prompt": " raw\nsetting ",
                                        "avatar": {"model_type": "vrm", "vrm": {"model_path": "local/model.vrm"}}}},
            "Other": {"昵称": "unchanged", "_reserved": {"character_uid": "b" * 32}}}}
        self.saves = []

    async def aload_characters(self):
        return copy.deepcopy(self.data)

    def save_characters(self, data):
        self.data = copy.deepcopy(data)
        self.saves.append(copy.deepcopy(data))


@pytest.fixture
def config(monkeypatch):
    """Isolate existing runtime refresh and busy-state readers from user processes."""
    from main_routers import shared_state
    config = Config()
    config.init = AsyncMock()
    monkeypatch.setattr(shared_state, "get_init_one_catgirl", lambda: config.init)
    monkeypatch.setattr(shared_state, "get_session_manager", lambda: {})
    return config


class ProfileService(SyntheticService):
    """Exercise real request headers/bytes while keeping both account and data fake."""

    def __init__(self):
        super().__init__()
        self.head = head(None, 0)
        self.profile_requests = []
        self.fail_once = False
        self.reject_detail = None

    async def request(self, request):
        if request.url.path.endswith("/profile"):
            self.requests.append(request)
            self.profile_requests.append(request)
            if self.on_request:
                await self.on_request(request)
            if request.method == "PUT":
                if self.reject_detail:
                    return httpx.Response(409, json={"detail": self.reject_detail})
                body = json.loads(request.content)
                self.head = head(body["profile"], body["baseRevision"] + 1)
                if self.fail_once:
                    self.fail_once = False
                    raise httpx.ReadError("synthetic response loss")
                return httpx.Response(200, json={key: self.head[key] for key in ("ownerId", "characterSyncId", "revision", "profileHash")}
                                      | {"operationId": body["operationId"]})
            if request.url.params.get("metadataOnly") == "true":
                return httpx.Response(200, json={key: self.head[key] for key in ("ownerId", "characterSyncId", "revision", "profileHash")})
            return httpx.Response(200, json=self.head)
        if request.url.path.endswith("/characters"):
            self.requests.append(request)
            return httpx.Response(200, json={"characters": [{"characterSyncId": UID, "displayName": "Synthetic"}]})
        return await super().request(request)


async def bound(tmp_path, config):
    """Use production binding/scheduler admission with an injected raw-config adapter."""
    runtime, service, session = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Synthetic")
    return runtime, service, session


@pytest.mark.asyncio
async def test_unchanged_profile_poll_does_not_download_definition(tmp_path, config):
    """Metadata polling retains the verified cached candidate without fetching its body again."""
    runtime, service, _ = await bound(tmp_path, config)
    try:
        service.head = head()
        await runtime.profiles.step(UID, manual=True)
        service.profile_requests.clear()
        await runtime.profiles.step(UID, manual=True)
        assert len(service.profile_requests) == 1
        assert service.profile_requests[0].url.params.get("metadataOnly") == "true"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_version_only_poll_supersedes_queued_profiles_after_final_definition_converges(tmp_path, config):
    """An ACK matching the latest saved definition must not replay obsolete intermediate profiles."""
    runtime, service, _ = await bound(tmp_path, config)
    try:
        for prompt in ("B", "A", "B", "A"):
            config.data["猫娘"]["Synthetic"]["_reserved"]["system_prompt"] = prompt
            candidate = await runtime.profiles.local(UID)
            await runtime.profiles.approve(UID, candidate["local_hash"], candidate["head_revision"])

        state = await runtime.profiles.step(UID, manual=True)
        assert state["dirty"] and state["pending"] == 3
        state = await runtime.profiles.step(UID, manual=True)
        assert not state["dirty"] and state["pending"] == 0

        # The third poll is metadata-only; the cloud must keep the final saved A.
        await runtime.profiles.step(UID, manual=True)
        published = [json.loads(request.content)["profile"]["systemPrompt"]
                     for request in service.profile_requests if request.method == "PUT"]
        assert published == ["B", "A"]
        assert service.head["profile"]["systemPrompt"] == "A"
        assert all(request.url.params.get("metadataOnly") == "true"
                   for request in service.profile_requests if request.method == "GET")
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_version_only_poll_reconciles_config_saved_before_baseline_commit(tmp_path, config):
    """A crash after applying config does not turn the same cloud definition into a new upload."""
    runtime, service, _ = await bound(tmp_path, config)
    try:
        service.head = head()
        await runtime.profiles.step(UID, manual=True)
        domain = ChatDomain(OWNER, UID).key()
        service.head = head({**copy.deepcopy(PROFILE), 'systemPrompt': 'saved before crash'}, 2)
        runtime.profiles.store.remote(domain, service.head)
        config.data['猫娘']['Synthetic']['_reserved']['system_prompt'] = 'saved before crash'
        service.profile_requests.clear()
        state = await runtime.profiles.step(UID, manual=True)
        assert not state['dirty'] and state['base_revision'] == 2 and state['pending'] == 0
        assert len(service.profile_requests) == 1 and service.profile_requests[0].url.query
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_legacy_new_only_intent_stays_narrow_until_explicit_resave(tmp_path, config):
    """Updating the application/recovering does not grant new access to old originals."""
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    runtime, _, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        source.commit_unbound(UID, 'c' * 32, ChatOriginal('user', 'legacy old'))
        await runtime.request_new_role_sync(UID, 'Synthetic', include_history=False)
        source.commit_unbound(UID, 'd' * 32, ChatOriginal('user', 'approved new'))
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        await runtime._advance_history(UID); await runtime._recover_originals()
        assert len(runtime.ledger.pending(ChatDomain(OWNER, UID))) == 1
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        await runtime._advance_history(UID); await runtime._recover_originals()
        assert len(runtime.ledger.pending(ChatDomain(OWNER, UID))) == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_combined_card_consent_survives_failure_before_profile_approval(tmp_path, config, monkeypatch):
    """An already saved card's approved history must not depend on a later profile transaction succeeding."""
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    runtime, _, _ = await bound(tmp_path, config)
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        source.commit_unbound(UID, 'c' * 32, ChatOriginal('user', 'approved old'))
        approve = runtime.profiles.store.approve
        def fail(*_args, **_kwargs):
            raise RuntimeError('synthetic stage interruption')
        monkeypatch.setattr(runtime.profiles.store, 'approve', fail)
        with pytest.raises(RuntimeError, match='synthetic stage interruption'):
            await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        request = runtime.state.new_role(UID)
        assert request and request['afterSeq'] == 0
        # Even a later unbound row cannot enlarge the old range approved by this save.
        source.commit_unbound(UID, 'd' * 32, ChatOriginal('user', 'later unbound'))
        monkeypatch.setattr(runtime.profiles.store, 'approve', approve)
        await runtime._run_new_role_sync(UID, request, await runtime.session_presence_provider())
        await runtime._advance_history(UID); await runtime._recover_originals()
        assert [json.loads(row[0])['text'] for row in runtime.ledger.pending(ChatDomain(OWNER, UID))] == ['approved old']
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'rollback', 'hash'])
async def test_invalid_profile_metadata_never_replaces_a_cached_definition(tmp_path, config, change):
    """A small response obeys the same identity and monotonicity rules as a full profile."""
    runtime, service, _ = await bound(tmp_path, config)
    try:
        service.head = head()
        await runtime.profiles.step(UID, manual=True)
        before = runtime.profiles.store.status(ChatDomain(OWNER, UID).key())
        service.head = head(None, 0) if change == 'rollback' else {**head(), **({'ownerId': OTHER_OWNER} if change == 'owner' else {'profileHash': '0' * 64})}
        with pytest.raises(ValueError, match='invalid_character_profile_response'):
            await runtime.profiles.step(UID, manual=True)
        assert runtime.profiles.store.status(before['domain'])['head'] == before['head']
    finally:
        await runtime.close()


@pytest.mark.parametrize("case", json.loads((Path(__file__).parents[1] / "fixtures/character_profile_v1.json").read_text(encoding="utf-8"))["valid"])
def test_shared_codec_fixture_matches_exact_wire(case):
    raw = encode_character_profile(case["profile"])
    assert raw.decode() == case["canonical"]
    assert hashlib.sha256(raw).hexdigest() == case["sha256"]
    assert decode_character_profile(raw) == case["profile"]


def test_export_uses_only_raw_portable_fields_and_finite_scalar_spelling(config):
    raw = config.data["猫娘"]["Synthetic"]
    raw["_reserved"]["field_order"] = ["设定", "年龄", "昵称"]
    raw["_reserved"]["persona_override"] = {"preset_id": "portable", "prompt_guidance": " saved\nstyle ",
                                                    "profile": {"性格": " Exact ", "昵称": "style nickname"}, "source": "local"}
    profile = export_profile("Synthetic", raw)
    assert profile["fields"] == [{"key": "设定", "value": " literal\n字段 "}, {"key": "年龄", "value": "15"}]
    assert profile["systemPrompt"] == " raw\nsetting "
    assert profile["persona"] == {"presetId": "portable", "promptGuidance": " saved\nstyle ",
                                 "fields": [{"key": "性格", "value": " Exact "}, {"key": "昵称", "value": "style nickname"}]}
    assert b"local/model" not in encode_character_profile(profile)
    assert b"local-voice" not in encode_character_profile(profile)


@pytest.mark.parametrize("value", [["array"], {"structured": True}, float("nan"), float("inf"), None])
def test_unsupported_raw_fields_are_rejected_without_silent_loss(config, value):
    config.data["猫娘"]["Synthetic"]["设定"] = value
    with pytest.raises(ValueError, match="character_profile_unsupported_value"):
        export_profile("Synthetic", config.data["猫娘"]["Synthetic"])


@pytest.mark.asyncio
async def test_passive_apply_preserves_other_roles_local_assets_identity_and_scalar_type(config, tmp_path):
    adapter = DesktopProfileAdapter(config)
    original = copy.deepcopy(config.data)
    store = ProfileStore(tmp_path / "profiles.db")
    domain = ChatDomain(OWNER, UID).key()
    store.observe(domain, await adapter.read(UID))
    store.remote(domain, head())
    cloud = copy.deepcopy(PROFILE)
    cloud["systemPrompt"] = "new raw setting"
    cloud["persona"] = {"presetId": "portable", "promptGuidance": "actual cloud style", "fields": [{"key": "性格", "value": "portable personality"}]}
    store.remote(domain, head(cloud, 2))

    async def commit(operation, *args, **kwargs):
        return operation(*args, **kwargs)

    assert await adapter.apply(UID, store.status(domain), commit)
    raw = config.data["猫娘"]["Synthetic"]
    assert raw["年龄"] == 15 and type(raw["年龄"]) is int
    assert raw["档案名"] == original["猫娘"]["Synthetic"]["档案名"]
    assert raw["_reserved"]["avatar"] == original["猫娘"]["Synthetic"]["_reserved"]["avatar"]
    assert raw["_reserved"]["character_id"] == original["猫娘"]["Synthetic"]["_reserved"]["character_id"]
    assert raw["_reserved"]["voice_id"] == "local-voice"
    assert config.data["猫娘"]["Other"] == original["猫娘"]["Other"]
    assert config.data["当前猫娘"] == "Other"
    config.init.assert_awaited_once_with("Synthetic", is_new=False)


@pytest.mark.asyncio
async def test_busy_uses_real_response_state_not_sticky_user_owner(config, monkeypatch):
    from main_logic.session_state import SessionEvent, SessionStateMachine, TurnOwner
    from main_routers import shared_state
    state = SessionStateMachine("Synthetic")
    await state.fire(SessionEvent.USER_INPUT, sid="synthetic-turn")
    assert state.owner is TurnOwner.USER
    manager = SimpleNamespace(state=state, is_starting=False, session=SimpleNamespace(_is_responding=False))
    monkeypatch.setattr(shared_state, "get_session_manager", lambda: {"Synthetic": manager})
    adapter = DesktopProfileAdapter(config)
    assert not adapter.busy("Synthetic")
    manager.session._is_responding = True
    assert adapter.busy("Synthetic")
    manager.session._is_responding = False
    manager.session._completion_pending_generation = 2
    assert adapter.busy("Synthetic")
    manager.session._completion_pending_generation = None
    manager._is_voice_playing = lambda: True
    assert adapter.busy("Synthetic")
    manager._is_voice_playing = lambda: False
    assert not adapter.busy("Synthetic")


@pytest.mark.asyncio
async def test_approved_profile_survives_response_loss_without_mixing_new_edit(tmp_path, config):
    runtime, service, _ = await bound(tmp_path, config)
    try:
        local = await runtime.profiles.local(UID)
        await runtime.profiles.approve(UID, local["local_hash"], 0)
        service.fail_once = True
        with pytest.raises(ValueError, match="chat_sync_unavailable"):
            await runtime.profiles.step(UID, manual=True)
        first = service.profile_requests[0].content
        config.data["猫娘"]["Synthetic"]["设定"] = "later declined edit"
        runtime.test_clock[0] += 600_000
        await runtime.profiles.step(UID, manual=True)
        puts = [request for request in service.profile_requests if request.method == "PUT"]
        assert len(puts) == 2 and puts[0].content == puts[1].content == first
        state = await runtime.profiles.local(UID)
        assert state["dirty"] and state["pending"] == 0
        assert state["local"]["fields"][1]["value"] == "later declined edit"
    finally:
        await runtime.close()


@pytest.mark.parametrize("detail,expected_conflict", [("profile_revision_conflict", True), ("prototype_idempotency_conflict", False)])
@pytest.mark.asyncio
async def test_only_exact_profile_cas_error_opens_conflict(tmp_path, config, detail, expected_conflict):
    runtime, service, _ = await bound(tmp_path, config)
    try:
        local = await runtime.profiles.local(UID)
        await runtime.profiles.approve(UID, local["local_hash"], 0)
        cloud = {**copy.deepcopy(PROFILE), "systemPrompt": "third cloud setting"}
        service.head, service.reject_detail = head(cloud), detail
        if expected_conflict:
            state = await runtime.profiles.step(UID, manual=True)
            assert state["conflict"] and state["head"] == cloud
        else:
            with pytest.raises(ValueError, match="chat_sync_service_rejected"):
                await runtime.profiles.step(UID, manual=True)
            assert runtime.profiles.store.next_operation(local["domain"]) is not None
    finally:
        await runtime.close()


@pytest.mark.parametrize("change", ["pause_resume", "logout", "owner"])
@pytest.mark.asyncio
async def test_late_profile_head_cannot_apply_after_consent_or_session_changes(tmp_path, config, change):
    runtime, service, session = await bound(tmp_path, config)
    try:
        initial = await runtime.profiles.local(UID)
        runtime.profiles.store.remote(initial["domain"], head())
        service.head = head({**copy.deepcopy(PROFILE), "systemPrompt": "late cloud edit"}, 2)
        entered, release = asyncio.Event(), asyncio.Event()

        async def block(request):
            if request.method == "GET" and request.url.path.endswith("/profile") and not request.url.query:
                entered.set()
                await release.wait()

        service.on_request = block
        task = asyncio.create_task(runtime.profiles.step(UID, manual=True))
        await asyncio.wait_for(entered.wait(), 2)
        if change == "pause_resume":
            await runtime.pause(UID)
            await runtime.enable(UID, "Synthetic")
        elif change == "logout":
            session["logged_in"] = False
        else:
            session["snapshot"]["access_token"] = "synthetic-other-account"
            service.owner = OTHER_OWNER
        release.set()
        with pytest.raises(ValueError):
            await task
        assert config.saves == []
        assert runtime.profiles.store.status(initial["domain"])["head_revision"] == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_automatic_profile_poll_applies_only_clean_idle_role_on_normal_cadence(tmp_path, config, monkeypatch):
    runtime, service, _ = await bound(tmp_path, config)
    try:
        local = await runtime.profiles.local(UID)
        runtime.profiles.store.remote(local["domain"], head())
        service.head = head({**copy.deepcopy(PROFILE), "systemPrompt": "passive remote edit"}, 2)
        monkeypatch.setattr(runtime.profiles.adapter, "busy", lambda _: True)
        await runtime.profiles.step(UID)
        assert not config.saves
        monkeypatch.setattr(runtime.profiles.adapter, "busy", lambda _: False)
        before = len(service.requests)
        await runtime.profiles.step(UID)
        assert len(service.requests) == before
        runtime.test_clock[0] += 120_000
        await runtime.profiles.step(UID)
        assert config.data["猫娘"]["Synthetic"]["_reserved"]["system_prompt"] == "passive remote edit"
        assert not runtime.profiles.store.status(local["domain"])["dirty"]
        assert len(config.saves) == 1
        await runtime.pause(UID)
        before = len(service.requests)
        with pytest.raises(ValueError, match="chat_sync_not_enabled"):
            await runtime.profiles.step(UID)
        assert len(service.requests) == before
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_remote_rename_waits_for_explicit_confirmation_and_passes_real_commit_guard(tmp_path, config, monkeypatch):
    runtime, service, session = await bound(tmp_path, config)
    try:
        local = await runtime.profiles.local(UID)
        runtime.profiles.store.remote(local["domain"], head())
        cloud = {**copy.deepcopy(PROFILE), "name": "Renamed", "systemPrompt": "rename setting"}
        service.head = head(cloud, 2)
        state = await runtime.profiles.step(UID, manual=True)
        assert state["renameRequired"] and not config.saves
        entered, release = asyncio.Event(), asyncio.Event()

        async def rename(old, new, *, sync_commit):
            entered.set()
            await release.wait()
            def persist():
                updated = copy.deepcopy(config.data)
                updated["猫娘"][new] = updated["猫娘"].pop(old)
                config.save_characters(updated)
            await sync_commit(persist)
            return {"success": True}

        monkeypatch.setattr("main_routers.characters_router.crud._rename_catgirl_serialized", rename)
        task = asyncio.create_task(runtime.profiles.step(UID, manual=True, choose_remote=True,
                                   local_hash=state["local_hash"], revision=2, confirm_rename=True))
        await asyncio.wait_for(entered.wait(), 2)
        session["logged_in"] = False
        release.set()
        with pytest.raises(ValueError, match="chat_sync_session_changed"):
            await task
        assert "Synthetic" in config.data["猫娘"] and "Renamed" not in config.data["猫娘"]
        assert not config.saves
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cloud_import_is_explicit_uid_stable_local_default_and_repeat_is_non_enabling(tmp_path, config, monkeypatch):
    config.data["猫娘"].pop("Synthetic")
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    monkeypatch.setattr("main_routers.characters_router.crud._get_new_catgirl_default_voice_id", lambda: "local-default-voice")
    try:
        await runtime.configure("https://sync.test")
        service.head = head()
        assert (await runtime.profiles.cloud_roles())["characters"][0]["characterSyncId"] == UID
        assert (await runtime.profiles.preview(UID))["profile"] == PROFILE
        assert not runtime.state.bindings() and not config.saves
        result = await runtime.profiles.import_role(UID, 1, service.head["profileHash"])
        assert result["enabled"] and not result["existing"]
        raw = config.data["猫娘"]["Synthetic"]
        assert raw["_reserved"]["character_uid"] == UID
        assert raw["_reserved"]["character_id"].startswith("character_")
        assert raw["_reserved"]["voice_id"] == "local-default-voice"
        assert raw["_reserved"]["avatar"]["model_type"] == "live2d"
        assert config.data["当前猫娘"] == "Other"
        await runtime.pause(UID)
        repeated = await runtime.profiles.import_role(UID, 1, service.head["profileHash"])
        assert repeated["existing"] and not repeated["enabled"]
        assert len(config.saves) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_import_config_binding_gap_recovers_same_reviewed_snapshot_after_cloud_advances(tmp_path, config, monkeypatch):
    config.data["猫娘"].pop("Synthetic")
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    monkeypatch.setattr("main_routers.characters_router.crud._get_new_catgirl_default_voice_id", lambda: "local")
    try:
        await runtime.configure("https://sync.test")
        service.head = head()
        original_enable = runtime.enable
        monkeypatch.setattr(runtime, "enable", AsyncMock(side_effect=ValueError("chat_sync_unavailable")))
        with pytest.raises(ValueError, match="chat_sync_unavailable"):
            await runtime.profiles.import_role(UID, 1, service.head["profileHash"])
        assert len(config.saves) == 1
        service.head = head({**copy.deepcopy(PROFILE), "systemPrompt": "not yet reviewed"}, 2)
        preview = await runtime.profiles.preview(UID)
        assert preview["revision"] == 1 and preview["profile"] == PROFILE
        monkeypatch.setattr(runtime, "enable", original_enable)
        result = await runtime.profiles.import_role(UID, 1, preview["profileHash"])
        assert result["enabled"] and len(config.saves) == 1
        assert config.data["猫娘"]["Synthetic"]["_reserved"]["system_prompt"] == PROFILE["systemPrompt"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_import_name_collision_keeps_both_roles_without_overwrite(tmp_path, config):
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    try:
        await runtime.configure("https://sync.test")
        config.data["猫娘"]["Synthetic"]["_reserved"]["character_uid"] = "b" * 32
        service.head = head()
        before = copy.deepcopy(config.data)
        with pytest.raises(ValueError, match="character_profile_name_conflict"):
            await runtime.profiles.import_role(UID, 1, service.head["profileHash"])
        assert config.data == before and not config.saves
        assert not runtime.state.binding(UID)["enabled"]
    finally:
        await runtime.close()


def test_synced_preset_uses_reviewed_guidance_instead_of_same_id_local_preset(monkeypatch):
    from utils.config_manager.persona_payload import _append_persona_guidance_to_prompt
    monkeypatch.setattr("utils.persona_presets.get_persona_prompt_guidance", lambda *_: "different local style")
    raw = {"_reserved": {"persona_override": {"preset_id": "same_id", "source": "chat_sync", "prompt_guidance": "actual cloud style"}}}
    prompt = _append_persona_guidance_to_prompt("custom original setting", raw)
    assert "actual cloud style" in prompt and "different local style" not in prompt


@pytest.mark.parametrize("change", ["pause", "logout"])
@pytest.mark.asyncio
async def test_real_rename_transaction_rolls_back_memory_and_config_if_guard_changes_during_reload(tmp_path, monkeypatch, change):
    """The sync hook extends existing rename rollback, not only a preflight check."""
    from main_routers.characters_router import crud
    from tests.unit.test_character_memory_regression import _make_config_manager
    from utils.character_memory import character_config_mutation_lock
    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    cm = _make_config_manager(tmp_path / "config-root")
    bootstrap_local_cloudsave_environment(cm)
    characters = cm.load_characters()
    characters["猫娘"]["Synthetic"] = copy.deepcopy(Config().data["猫娘"]["Synthetic"])
    cm.save_characters(characters)
    before = cm.load_characters()
    old_memory = Path(cm.memory_dir) / "Synthetic"
    old_memory.mkdir(parents=True, exist_ok=True)
    original_recent = json.dumps([{"speaker": "Synthetic", "data": {"content": "SYNTHETIC ORIGINAL"}}]).encode()
    (old_memory / "recent.json").write_bytes(original_recent)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def reload(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return True

    monkeypatch.setattr(crud, "get_config_manager", lambda: cm)
    monkeypatch.setattr(crud, "get_session_manager", lambda: {})
    monkeypatch.setattr(crud, "get_remove_one_catgirl", lambda: AsyncMock())
    monkeypatch.setattr(crud, "get_init_one_catgirl", lambda: AsyncMock())
    monkeypatch.setattr(crud, "get_initialize_character_data", lambda: AsyncMock())
    monkeypatch.setattr(crud, "release_memory_server_character", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "notify_memory_server_reload", reload)
    monkeypatch.setattr(crud, "rename_new_character_greeting_pending", AsyncMock())
    runtime, _, session = setup_runtime(tmp_path / "runtime")
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Synthetic")
        _, _, commit = await runtime._bound_transport(UID)

        async def rename():
            async with character_config_mutation_lock:
                return await crud._rename_catgirl_serialized("Synthetic", "Renamed", sync_commit=commit)

        task = asyncio.create_task(rename())
        await asyncio.wait_for(entered.wait(), 3)
        if change == "pause":
            await runtime.pause(UID)
        else:
            session["logged_in"] = False
        release.set()
        result = await task
        assert result.status_code == 500
        assert cm.load_characters() == before
        assert (old_memory / "recent.json").read_bytes() == original_recent
        assert not (Path(cm.memory_dir) / "Renamed").exists()
        crud.rename_new_character_greeting_pending.assert_not_awaited()
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_confirmed_remote_rename_reuses_local_name_validation_before_any_mutation(tmp_path, config, monkeypatch):
    adapter = DesktopProfileAdapter(config)
    state = {"local_hash": hashlib.sha256(encode_character_profile(PROFILE)).hexdigest(), "head": {**copy.deepcopy(PROFILE), "name": "../unsafe"}}
    rename = AsyncMock()
    monkeypatch.setattr("main_routers.characters_router.crud._rename_catgirl_serialized", rename)
    with pytest.raises(ValueError, match="character_profile_name_invalid"):
        await adapter.apply(UID, state, AsyncMock(), confirm_rename=True)
    rename.assert_not_awaited()
    assert not config.saves


@pytest.mark.asyncio
async def test_explicit_remote_choice_stays_unresolved_while_conversation_is_busy(tmp_path, config, monkeypatch):
    runtime, service, _ = await bound(tmp_path, config)
    try:
        state = await runtime.profiles.local(UID)
        service.head = head({**copy.deepcopy(PROFILE), "systemPrompt": "remote setting"})
        monkeypatch.setattr(runtime.profiles.adapter, "busy", lambda _: True)
        with pytest.raises(ValueError, match="chat_sync_retry_later"):
            await runtime.profiles.step(UID, manual=True, choose_remote=True, local_hash=state["local_hash"], revision=1)
        retained = runtime.profiles.store.status(state["domain"])
        assert retained["local"]["systemPrompt"] == PROFILE["systemPrompt"]
        assert retained["head"]["systemPrompt"] == "remote setting" and retained["apply_pending"]
        assert not config.saves
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unbound_profile_import_cannot_commit_after_writer_recovery_epoch(tmp_path, config, monkeypatch):
    """A still-current login cannot lend new installation authority to an old import."""
    config.data['猫娘'].pop('Synthetic')
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    monkeypatch.setattr('main_routers.characters_router.crud._get_new_catgirl_default_voice_id', lambda: 'local')
    await runtime.configure('https://sync.test')
    service.head = head()
    entered, release = asyncio.Event(), asyncio.Event()
    real_import = runtime.profiles.adapter.import_new
    async def delayed_import(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real_import(*args, **kwargs)
    monkeypatch.setattr(runtime.profiles.adapter, 'import_new', delayed_import)
    task = asyncio.create_task(runtime.profiles.import_role(UID, 1, service.head['profileHash']))
    await entered.wait()
    runtime.prepare_writer_recovery()
    release.set()
    try:
        with pytest.raises(ValueError, match='chat_sync_writer_recovery_required'):
            await task
        assert not config.saves
        assert not runtime.state.binding(UID)['enabled']
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_uid", [None, "b" * 32])
async def test_current_role_automatic_profile_keeps_inactive_approved_bytes(tmp_path, config, selected_uid):
    """An approved profile waits locally while its role is not currently used."""
    from tests.unit.test_chat_sync_runtime import select_runtime_current
    runtime, service, _ = await bound(tmp_path, config)
    try:
        config.data["当前猫娘"] = "Synthetic"
        local = await runtime.profiles.local(UID)
        await runtime.profiles.approve(UID, local["local_hash"], 0)
        operation = runtime.profiles.store.next_operation(local["domain"])
        select_runtime_current(runtime, selected_uid)
        service.requests.clear()
        await runtime._automatic_profiles(runtime.state.bindings(), await runtime.session_presence_provider())
        assert service.requests == []
        assert runtime.profiles.store.next_operation(local["domain"]) == operation
        assert config.saves == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_profile_put_response_acks_after_switch_without_next_get(tmp_path, config):
    """A selected role's sent immutable publication ACKs after switching away."""
    from tests.unit.test_chat_sync_runtime import select_runtime_current
    runtime, service, _ = await bound(tmp_path, config)
    try:
        config.data["当前猫娘"] = "Synthetic"
        selected = select_runtime_current(runtime)
        local = await runtime.profiles.local(UID)
        await runtime.profiles.approve(UID, local["local_hash"], 0)
        operation = runtime.profiles.store.next_operation(local["domain"])

        async def switch(request):
            if request.method == "PUT" and request.url.path.endswith("/profile"):
                selected[0] = "b" * 32
                config.data["当前猫娘"] = "Other"

        service.on_request = switch
        service.profile_requests.clear()
        await runtime._automatic_profiles(runtime.state.bindings(), await runtime.session_presence_provider())
        assert [request.method for request in service.profile_requests] == ["PUT"]
        assert service.profile_requests[0].content == bytes(operation["body"])
        assert runtime.profiles.store.next_operation(local["domain"]) is None
        assert runtime.profiles.store.status(local["domain"])["base_revision"] == 1
        assert runtime.scheduler.last_error(local["domain"]) is None
        assert config.saves == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_profile_get_response_is_retained_after_switch_without_apply(tmp_path, config):
    """Received cloud settings remain pending until the role is used again."""
    from tests.unit.test_chat_sync_runtime import select_runtime_current
    runtime, service, _ = await bound(tmp_path, config)
    try:
        config.data["当前猫娘"] = "Synthetic"
        selected = select_runtime_current(runtime)
        local = await runtime.profiles.local(UID)
        runtime.profiles.store.remote(local["domain"], head())
        service.head = head({**copy.deepcopy(PROFILE), "systemPrompt": "pending after switch"}, 2)

        async def switch(request):
            if request.method == "GET" and request.url.path.endswith("/profile") and not request.url.query:
                selected[0] = "b" * 32
                config.data["当前猫娘"] = "Other"

        service.on_request = switch
        await runtime._automatic_profiles(runtime.state.bindings(), await runtime.session_presence_provider())
        state = runtime.profiles.store.status(local["domain"])
        assert state["head_revision"] == 2 and state["head"] == service.head["profile"]
        assert state["apply_pending"] and config.saves == []
        assert config.data["猫娘"]["Synthetic"]["_reserved"]["system_prompt"] == PROFILE["systemPrompt"]
        assert runtime.scheduler.last_error(local["domain"]) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_profile_waiting_lock_rechecks_selection(tmp_path, config):
    """Waiting for another profile operation cannot authorize an inactive next send."""
    from tests.unit.test_chat_sync_runtime import select_runtime_current
    runtime, service, _ = await bound(tmp_path, config)
    try:
        config.data["当前猫娘"] = "Synthetic"
        selected = select_runtime_current(runtime)
        await runtime.profiles.lock.acquire()
        service.requests.clear()
        task = asyncio.create_task(runtime._automatic_profiles(runtime.state.bindings(), await runtime.session_presence_provider()))
        try:
            await asyncio.sleep(.01)
            assert not task.done()
            selected[0] = "b" * 32
            config.data["当前猫娘"] = "Other"
        finally:
            runtime.profiles.lock.release()
        await asyncio.wait_for(task, 2)
        assert service.requests == [] and config.saves == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_profile_waiting_config_lock_defers_automatic_apply(tmp_path, config):
    """The character selection and automatic settings apply share the existing lock."""
    from tests.unit.test_chat_sync_runtime import select_runtime_current, wait_notification
    from utils.character_memory import character_config_mutation_lock
    runtime, service, _ = await bound(tmp_path, config)
    try:
        config.data["当前猫娘"] = "Synthetic"
        selected = select_runtime_current(runtime)
        local = await runtime.profiles.local(UID)
        runtime.profiles.store.remote(local["domain"], head())
        service.head = head({**copy.deepcopy(PROFILE), "systemPrompt": "wait for current role"}, 2)
        await character_config_mutation_lock.acquire()
        task = asyncio.create_task(runtime._automatic_profiles(runtime.state.bindings(), await runtime.session_presence_provider()))
        try:
            await wait_notification(lambda: runtime.profiles.store.status(local["domain"])["head_revision"] == 2)
            await asyncio.sleep(.01)
            assert not task.done()
            selected[0] = "b" * 32
            config.data["当前猫娘"] = "Other"
        finally:
            character_config_mutation_lock.release()
        await asyncio.wait_for(task, 2)
        assert runtime.profiles.store.status(local["domain"])["apply_pending"]
        assert config.saves == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_explicit_inactive_profile_apply_remains_authorized(tmp_path, config):
    """Manual profile resolution still uses its reviewed identity and consent."""
    from tests.unit.test_chat_sync_runtime import select_runtime_current
    runtime, service, _ = await bound(tmp_path, config)
    try:
        select_runtime_current(runtime, "b" * 32)
        local = await runtime.profiles.local(UID)
        runtime.profiles.store.remote(local["domain"], head())
        service.head = head({**copy.deepcopy(PROFILE), "systemPrompt": "explicit inactive apply"}, 2)
        result = await runtime.profiles.step(UID, manual=True)
        assert result["head_revision"] == 2 and not result["apply_pending"]
        assert config.data["猫娘"]["Synthetic"]["_reserved"]["system_prompt"] == "explicit inactive apply"
        assert len(config.saves) == 1 and config.data["当前猫娘"] == "Other"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_new_role_choice_is_local_durable_and_current_role_only(tmp_path, config):
    from tests.unit.test_chat_sync_runtime import select_runtime_current
    runtime, service, session = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    try:
        session['logged_in'] = False
        from services.chat_sync.source import ChatOriginalSource
        from services.chat_sync.ledger import ChatOriginal
        source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
        source.commit_unbound(UID, 'd' * 32, ChatOriginal('user', 'saved before login'))
        assert await runtime.request_new_role_sync(UID, 'Synthetic') == {'pending': True, 'enabled': False}
        request = runtime.state.new_role(UID)
        assert not service.requests and not runtime.state.bindings()
        assert type(runtime.state)(runtime.state.path).new_role(UID) == request
        select_runtime_current(runtime, 'b' * 32)
        await runtime._start_new_role_sync('synthetic-presence')
        assert 'new-role' not in runtime._flights
        select_runtime_current(runtime)
        session['logged_in'] = True
        presence = await runtime.session_presence_provider()
        await runtime._run_new_role_sync(UID, request, presence)
        assert runtime.state.binding(UID)['enabled']
        assert runtime.state.new_role(UID) is None
        await runtime._advance_history(UID)
        await runtime._recover_originals()
        assert json.loads(runtime.ledger.pending(ChatDomain(OWNER, UID))[0][0])['text'] == 'saved before login'
        operation = runtime.profiles.store.next_operation(ChatDomain(OWNER, UID).key())
        assert operation['operation_id'] == request['requestId']
        assert json.loads(operation['body'])['profile'] == decode_character_profile(request['canonical'].encode())
        assert all(str(item.url).startswith('https://sync.nekotech.cn/') for item in service.requests)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['profile-edit', 'pause', 'writer-epoch'])
async def test_new_role_choice_cannot_override_later_local_decisions(tmp_path, config, reason):
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    try:
        await runtime.request_new_role_sync(UID, 'Synthetic')
        request = runtime.state.new_role(UID)
        if reason == 'profile-edit':
            config.data['猫娘']['Synthetic']['昵称'] = 'unapproved edit'
        elif reason == 'pause':
            await runtime.enable(UID, 'Synthetic')
            await runtime.pause(UID)
        else:
            request['epoch'] = ['copied-installation', 'stale-lineage']
        service.requests.clear()
        await runtime._run_new_role_sync(UID, request, await runtime.session_presence_provider())
        assert not service.requests
        assert (runtime.state.new_role(UID) is None) == (reason == 'pause')
        if reason == 'pause':
            assert not runtime.state.binding(UID)['enabled']
    finally:
        await runtime.close()


def test_fixed_new_role_profile_approval_is_idempotent_after_restart(tmp_path):
    store = ProfileStore(tmp_path / 'profiles.db')
    domain = ChatDomain(OWNER, UID).key()
    store.observe(domain, PROFILE)
    expected = hashlib.sha256(encode_character_profile(PROFILE)).hexdigest()
    operation = 'c' * 32
    assert store.approve(domain, expected, 0, operation_id=operation) == operation
    reopened = ProfileStore(tmp_path / 'profiles.db')
    assert reopened.approve(domain, expected, 0, operation_id=operation) == operation
    assert reopened.next_operation(domain)['base_revision'] == 0
    with pytest.raises(ValueError, match='character_profile_changed'):
        reopened.approve(domain, 'd' * 64, 0, operation_id=operation)


@pytest.mark.asyncio
async def test_new_role_setup_stops_before_registration_when_current_role_changes(tmp_path, config):
    from tests.unit.test_chat_sync_runtime import select_runtime_current
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    selected = select_runtime_current(runtime)
    try:
        await runtime.request_new_role_sync(UID, 'Synthetic')
        async def switch_after_identity(request):
            if request.url.path.endswith('/identity'):
                selected['uid'] = 'b' * 32
        service.on_request = switch_after_identity
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        assert [item.url.path for item in service.requests] == ['/v1/chat-sync/identity']
        binding = runtime.state.binding(UID)
        assert binding is None or not binding['enabled']
        assert runtime.state.new_role(UID) is not None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_old_card_choice_is_durable_and_approves_trusted_preexisting_chats(tmp_path, config):
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    runtime, service, session = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        source.commit_unbound(UID, 'c' * 32, ChatOriginal('user', 'old history'))
        session['logged_in'] = False
        await runtime.save_character_sync_choice(UID, 'Synthetic', False)
        assert runtime.state.choices()[UID] is False
        assert type(runtime.state)(runtime.state.path).choices()[UID] is False
        assert not runtime.state.new_role(UID) and service.requests == []
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        assert service.requests == []
        source.commit_unbound(UID, 'd' * 32, ChatOriginal('user', 'new after choice'))
        session['logged_in'] = True
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        await runtime._advance_history(UID); await runtime._recover_originals()
        texts = [json.loads(row[0])['text'] for row in runtime.ledger.pending(ChatDomain(OWNER, UID))]
        assert texts == ['old history', 'new after choice']
        import sqlite3
        with sqlite3.connect(source.path) as conn:
            assert conn.execute('SELECT original FROM source_unbound WHERE record_id=?', ('c' * 32,)).fetchone()[0] is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_paused_card_save_stays_off_and_explicit_on_approves_profile_without_http(tmp_path, config):
    runtime, service, session = await bound(tmp_path, config)
    try:
        await runtime.pause(UID)
        config.data['猫娘']['Synthetic']['昵称'] = 'saved nickname'
        session['logged_in'] = False; service.requests.clear()
        await runtime.save_character_sync_choice(UID, 'Synthetic', False)
        assert not runtime.state.binding(UID)['enabled']
        assert runtime.profiles.store.next_operation(ChatDomain(OWNER, UID).key()) is None
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        assert not runtime.state.binding(UID)['enabled']
        assert runtime.state.new_role(UID) is not None
        assert service.requests == []
        session['logged_in'] = True
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        assert runtime.state.binding(UID)['enabled']
        operation = runtime.profiles.store.next_operation(ChatDomain(OWNER, UID).key())
        assert json.loads(operation['body'])['profile']['nickname'] == 'saved nickname'
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_card_off_during_first_grant_prevents_late_activation(tmp_path, config):
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    try:
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        async def turn_off(request):
            if request.url.path.endswith('/bindings'):
                await runtime.save_character_sync_choice(UID, 'Synthetic', False)
        service.on_request = turn_off
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        assert not runtime.state.binding(UID)['enabled']
        assert runtime.state.choices()[UID] is False
        assert runtime.state.new_role(UID) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_explicit_on_can_resume_a_paused_unfinished_grant_but_a_later_pause_wins(tmp_path, config):
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    try:
        runtime.state.prepare_binding(ChatDomain(OWNER, UID))
        runtime.state.pause(UID)
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        request = runtime.state.new_role(UID)
        assert request['resumeGeneration'] == runtime.state.binding(UID)['generation']
        await runtime._run_new_role_sync(UID, request, await runtime.session_presence_provider())
        assert runtime.state.binding(UID)['enabled']
        await runtime.save_character_sync_choice(UID, 'Synthetic', False)
        assert not runtime.state.binding(UID)['enabled']
        service.requests.clear()
        await runtime._run_new_role_sync(UID, request, await runtime.session_presence_provider())
        assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_card_save_does_not_publish_an_identical_profile_again_after_setup_retry(tmp_path, config):
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    try:
        await runtime.request_new_role_sync(UID, 'Synthetic')
        await runtime.enable(UID, 'Synthetic')
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        request = runtime.state.new_role(UID)
        await runtime.profiles.step(UID, manual=True)
        domain = ChatDomain(OWNER, UID).key()
        assert runtime.profiles.store.next_operation(domain) is None
        await runtime._run_new_role_sync(UID, request, await runtime.session_presence_provider())
        assert runtime.profiles.store.next_operation(domain) is None
        assert runtime.state.new_role(UID) is None
        assert len([item for item in service.profile_requests if item.method == 'PUT']) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_repeated_offline_card_save_retains_the_original_approved_chat_cutoff(tmp_path, config):
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    runtime, service, session = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    session['logged_in'] = False
    try:
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        first = runtime.state.new_role(UID)
        source = ChatOriginalSource(tmp_path / 'sources.sqlite3')
        source.commit_unbound(UID, 'f' * 32, ChatOriginal('user', 'approved offline chat'))
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        assert runtime.state.new_role(UID)['afterSeq'] == first['afterSeq']
        assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_changed_new_role_profile_blocks_retry_until_explicit_save(tmp_path, config):
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    try:
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        config.data['猫娘']['Synthetic']['昵称'] = 'unapproved change'
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        assert runtime.state.new_role(UID)['error'] == 'character_profile_changed'
        assert runtime.state.new_role_errors() == {UID: 'character_profile_changed'}
        assert service.requests == []
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        assert 'error' not in runtime.state.new_role(UID)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_sync_never_depends_on_an_account_character_policy_route(tmp_path, config):
    """A service without any character policy route still enables, uploads, pulls and syncs profiles."""
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    from tests.unit.test_chat_sync_runtime import capture, finish_automatic_flights

    class NoPolicyService(ProfileService):
        """Model a service deployed before any account policy endpoint existed."""

        def __init__(self):
            super().__init__()
            self.policy_requests = []

        async def request(self, request):
            if 'character-policy' in request.url.path:
                self.policy_requests.append(request)
                return httpx.Response(404, json={'detail': 'Not Found'})
            return await super().request(request)

    runtime, service, _ = setup_runtime(tmp_path, NoPolicyService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        await runtime.configure('https://sync.test')
        source.commit_unbound(UID, 'd' * 32, ChatOriginal('user', 'trusted earlier chat'))
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        # The automatic scheduler consumes the saved On without any policy admission.
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        assert runtime.state.new_role(UID) is None and runtime.state.new_role_errors() == {}
        assert runtime.state.binding(UID)['enabled']
        # A later automatic pass sends the approved definition for the now-bound role.
        runtime.test_clock[0] += 60_000
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        assert service.head['profile'] == PROFILE

        capture(runtime)
        result = await runtime.sync(UID, byte_budget=1_000_000, max_pull_pages=1)
        assert result['uploaded'] >= 1 and result['pullPages'] == 1
        # Approved history registers in its own lane; ordinary later passes drain it.
        for _ in range(3):
            if not runtime.ledger.pending(DOMAIN):
                break
            runtime.test_clock[0] += 60_000
            await runtime.sync(UID, byte_budget=1_000_000, max_pull_pages=1)
        # Both the approved history and the newly captured original left the outbox.
        assert runtime.ledger.pending(DOMAIN) == []
        assert {json.loads(raw)['text'] for raw in runtime.ledger.visible_originals(DOMAIN)} >= {'trusted earlier chat'}

        config.data['猫娘']['Synthetic']['_reserved']['system_prompt'] = 'updated raw setting'
        candidate = await runtime.profiles.local(UID)
        await runtime.profiles.approve(UID, candidate['local_hash'], candidate['head_revision'])
        await runtime.profiles.step(UID, manual=True)
        assert service.head['profile']['systemPrompt'] == 'updated raw setting'

        status = await runtime.status()
        assert status['bindings'][0]['enabled'] and status['bindings'][0]['lastSyncError'] is None
        assert 'characterPolicy' not in status and 'cloudPolicyError' not in status['bindings'][0]
        assert service.policy_requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_card_resume_revalidates_owner_before_reenabling_a_paused_binding(tmp_path, config):
    from tests.unit.test_chat_sync_runtime import OTHER_OWNER
    runtime, service, _ = await bound(tmp_path, config)
    try:
        await runtime.pause(UID)
        before = runtime.state.binding(UID)
        service.owner = OTHER_OWNER
        service.requests.clear()
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        assert runtime.state.binding(UID) == before
        assert not runtime.state.binding(UID)['enabled']
        assert [request.url.path for request in service.requests] == ['/v1/chat-sync/identity']
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['pause', 'epoch', 'owner'])
async def test_card_pending_on_is_visible_only_for_its_pinned_authority(tmp_path, config, reason):
    """Status preserves a saved resume, but never revives a revoked or copied grant."""
    runtime, _, _ = await bound(tmp_path, config)
    try:
        await runtime.pause(UID)
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        assert (await runtime.status())['syncPendingChoices'] == {UID: True}
        request = runtime.state.new_role(UID)
        with runtime.writer.locked():
            if reason == 'pause':
                runtime.state.pause(UID)
            else:
                if reason == 'epoch':
                    request['epoch'] = ['copied-device', 'stale-lineage']
                else:
                    request['historyBinding']['owner_id'] = OTHER_OWNER
                runtime.state.queue_new_role(UID, request, replace=True)
        assert (await runtime.status())['syncPendingChoices'] == {}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_manual_choice_waits_for_the_background_consumer_without_a_false_failure(tmp_path, config):
    """The scheduler may finish the same saved grant before the UI acquires its setup lock."""
    from contextlib import asynccontextmanager
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    held = asyncio.Lock()
    attempting = asyncio.Event()
    @asynccontextmanager
    async def observed_lock():
        attempting.set()
        async with held:
            yield
    runtime._sync_choice_lock = observed_lock()
    try:
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        request = runtime.state.new_role(UID)
        async with held:
            task = asyncio.create_task(runtime.run_character_sync_choice(UID))
            await asyncio.wait_for(attempting.wait(), 2)
            await runtime._consume_sync_choice(UID, request, await runtime.session_presence_provider(), manual=False)
        assert await asyncio.wait_for(task, 2) == {'completed': True, 'pending': False}
        assert service.head['profile'] == PROFILE
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_manual_combined_choice_runs_for_the_selected_inactive_role(tmp_path, config):
    """An explicit settings action advances its target without changing the current role."""
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    from tests.unit.test_chat_sync_runtime import select_runtime_current
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        source.commit_unbound(UID, 'd' * 32, ChatOriginal('user', 'explicit inactive history'))
        select_runtime_current(runtime, 'b' * 32)
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        receipt = await runtime.run_character_sync_choice(UID)
        assert receipt == {'completed': True, 'pending': False}
        assert runtime.state.binding(UID)['enabled']
        assert runtime.state.new_role(UID) is None
        assert service.head['profile'] == PROFILE
        assert [json.loads(raw)['text'] for raw in service.records] == ['explicit inactive history']
        assert config.data['当前猫娘'] == 'Other'
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['logout', 'network', 'owner', 'retry'])
async def test_manual_combined_choice_exposes_failures_and_retains_the_grant(tmp_path, config, reason):
    """The manual UI receives real setup errors while its fixed local grant remains durable."""
    from services.chat_sync.runtime import ChatSyncServiceError
    runtime, service, session = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(config)
    try:
        if reason == 'owner':
            await runtime.enable(UID, 'Synthetic')
            await runtime.pause(UID)
            service.owner = OTHER_OWNER
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        request = runtime.state.new_role(UID)
        service.requests.clear()
        expected = {'logout': 'chat_sync_login_required', 'network': 'chat_sync_unavailable',
                    'owner': 'chat_sync_account_mismatch', 'retry': 'chat_sync_retry_later'}[reason]
        if reason == 'logout':
            session['logged_in'] = False
            session.pop('snapshot')
        elif reason == 'network':
            async def unavailable(_request):
                raise httpx.ReadError('synthetic transport interruption')
            service.on_request = unavailable
        elif reason == 'retry':
            runtime.scheduler.failure('control', 'control', 0, None, ChatSyncServiceError(429, 240000))
        with pytest.raises(ValueError, match='^' + expected + '$'):
            await runtime.run_character_sync_choice(UID)
        assert runtime.state.new_role(UID)['requestId'] == request['requestId']
        assert runtime.profiles.store.next_operation(ChatDomain(OWNER, UID).key()) is None
        if reason in {'logout', 'retry'}:
            assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('binding_removed', [False, True])
async def test_combined_choice_blocks_a_changed_history_binding(tmp_path, config, binding_removed):
    """A stale pinned domain is a terminal setup error rather than an endless retry or TypeError."""
    import sqlite3
    runtime, service, _ = await bound(tmp_path, config)
    try:
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        request = runtime.state.new_role(UID)
        if binding_removed:
            original_approve = runtime.profiles.store.approve
            def remove_binding(*args, **kwargs):
                result = original_approve(*args, **kwargs)
                with sqlite3.connect(runtime.state.path) as connection:
                    connection.execute('DELETE FROM sync_bindings WHERE character_uid=?', (UID,))
                return result
            runtime.profiles.store.approve = remove_binding
        else:
            with sqlite3.connect(runtime.state.path) as connection:
                connection.execute('UPDATE sync_bindings SET request_id=? WHERE character_uid=?', ('f' * 32, UID))
        service.requests.clear()
        await runtime._run_new_role_sync(UID, request, await runtime.session_presence_provider())
        assert runtime.state.new_role_errors() == {UID: 'chat_sync_binding_changed'}
        assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_newer_card_save_fences_the_older_history_approval_commit(tmp_path, config):
    """A superseded request cannot approve history while waiting for the local commit gate."""
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    runtime, _, _ = await bound(tmp_path, config)
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        source.commit_unbound(UID, 'd' * 32, ChatOriginal('user', 'fixed old original'))
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        old_request = runtime.state.new_role(UID)
        original_approve = runtime.approve_history
        async def save_newer_before_commit(uid, task_id, **kwargs):
            await runtime.save_character_sync_choice(uid, 'Synthetic', True)
            return await original_approve(uid, task_id, **kwargs)
        runtime.approve_history = save_newer_before_commit
        await runtime._run_new_role_sync(UID, old_request, await runtime.session_presence_provider())
        assert runtime.state.new_role(UID)['requestId'] != old_request['requestId']
        assert source.task(UID)['status'] == 'cancelled'
        assert source.approved_tasks(UID) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_combined_choice_does_not_cancel_an_open_manual_history_preview(tmp_path, config):
    """Background consent keeps the user's reviewed task usable without duplicate registration."""
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    runtime, _, _ = await bound(tmp_path, config)
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        source.commit_unbound(UID, 'd' * 32, ChatOriginal('user', 'same trusted old original'))
        preview = await runtime.preview_history(UID, 'Synthetic')
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        assert source.task(UID, preview['taskId'])['status'] == 'preview'
        await runtime._advance_history(UID)
        approved = await runtime.approve_history(UID, preview['taskId'])
        assert approved['status'] == 'registered' and approved['cancelledCount'] == 1
        assert await runtime._advance_history(UID) == 0
        await runtime._recover_originals()
        assert len(runtime.ledger.pending(ChatDomain(OWNER, UID))) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_explicit_history_save_includes_only_same_owner_trusted_recovery_originals(tmp_path, config):
    """Combined history consent covers verified recovery bytes without crossing account or delete pins."""
    from contextlib import closing
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatOriginal
    runtime, _, _ = await bound(tmp_path, config)
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        for identity, text in [('d' * 32, 'same owner recovered'), ('e' * 32, 'other owner recovered'),
                               ('f' * 32, 'deleted recovered')]:
            source.commit_unbound(UID, identity, ChatOriginal('user', text))
        with closing(source._connect()) as connection, connection:
            connection.execute('UPDATE source_unbound SET verified_owner=?', (OWNER,))
            connection.execute('UPDATE source_unbound SET verified_owner=? WHERE record_id=?', (OTHER_OWNER, 'e' * 32))
        await runtime.delete(UID, ['f' * 32])
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        await runtime._advance_history(UID)
        await runtime._recover_originals()
        assert [json.loads(row[0])['text'] for row in runtime.ledger.pending(ChatDomain(OWNER, UID))] == ['same owner recovered']
        with closing(source._connect()) as connection:
            assert connection.execute('SELECT original FROM source_unbound WHERE record_id=?', ('e' * 32,)).fetchone()[0]
            assert connection.execute('SELECT original FROM source_unbound WHERE record_id=?', ('f' * 32,)).fetchone()[0] is None
        assert runtime.ledger.pending_deletions(ChatDomain(OWNER, UID))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_combined_choice_with_no_history_does_not_leave_an_approved_empty_task(tmp_path, config):
    """Repeated card saves cannot retain empty approved history jobs forever."""
    from services.chat_sync.source import ChatOriginalSource
    runtime, _, _ = await bound(tmp_path, config)
    try:
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
        assert source.approved_tasks(UID) == []
        assert source.task(UID)['status'] == 'registered'
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cloud_directory_pages_every_role_without_an_account_quota(tmp_path):
    """Directory page size bounds transport, not the number of roles a beta account may own."""
    roles = [{"characterSyncId": f"{index:032x}", "displayName": f"Role {index}"} for index in range(1, 206)]

    class PagedDirectoryService(ProfileService):
        async def request(self, request):
            if request.url.path.endswith('/characters'):
                self.requests.append(request)
                assert request.url.params.get('limit') == '100'
                after = request.url.params.get('after')
                remaining = [role for role in roles if after is None or role['characterSyncId'] > after]
                page = remaining[:100]
                return httpx.Response(200, json={'characters': page,
                    'nextCursor': page[-1]['characterSyncId'] if len(remaining) > 100 else None})
            return await super().request(request)

    runtime, service, _ = setup_runtime(tmp_path, PagedDirectoryService())
    try:
        await runtime.configure('https://sync.test')
        assert await runtime.profiles.cloud_roles() == {'characters': roles}
        pages = [request for request in service.requests if request.url.path.endswith('/characters')]
        assert [request.url.params.get('after') for request in pages] == [None, roles[99]['characterSyncId'], roles[199]['characterSyncId']]
        assert runtime.state.bindings() == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['empty-next', 'wrong-cursor', 'order', 'duplicate', 'oversized',
                                    'bad-uid', 'empty-name', 'extra-role-field', 'legacy-second'])
async def test_cloud_directory_rejects_invalid_or_nonprogressing_pages(invalid):
    """A truncated or changed pagination contract must fail instead of losing cloud characters."""
    from services.chat_sync.profile_sync import read_character_directory
    first = {'characterSyncId': 'a' * 32, 'displayName': 'A'}
    second = {'characterSyncId': 'b' * 32, 'displayName': 'B'}
    pages = [{'characters': [first], 'nextCursor': first['characterSyncId']},
             {'characters': [second], 'nextCursor': None}]
    if invalid == 'empty-next':
        pages[0]['characters'] = []
    elif invalid == 'wrong-cursor':
        pages[0]['nextCursor'] = second['characterSyncId']
    elif invalid == 'order':
        pages[0] = {'characters': [second, first], 'nextCursor': None}
    elif invalid == 'duplicate':
        pages[1]['characters'] = [first]
    elif invalid == 'oversized':
        pages[0] = {'characters': [{'characterSyncId': f'{index:032x}', 'displayName': 'A'} for index in range(1, 102)], 'nextCursor': None}
    elif invalid == 'bad-uid':
        pages[0]['characters'] = [{**first, 'characterSyncId': 'invalid'}]
    elif invalid == 'empty-name':
        pages[0]['characters'] = [{**first, 'displayName': ''}]
    elif invalid == 'extra-role-field':
        pages[0]['characters'] = [{**first, 'ownerId': OWNER}]
    elif invalid == 'legacy-second':
        pages[1] = {'characters': [second]}

    async def request(method, path, body):
        assert method == 'GET' and body is None
        return pages.pop(0)

    with pytest.raises(ValueError, match='invalid_character_profile_response'):
        await read_character_directory(request, OWNER)


@pytest.mark.asyncio
async def test_cloud_directory_accepts_one_complete_legacy_response():
    """Old services may ignore the query, keeping their original complete ordering."""
    from services.chat_sync.profile_sync import read_character_directory
    roles = [{'characterSyncId': 'b' * 32, 'displayName': 'B'}, {'characterSyncId': 'a' * 32, 'displayName': 'A'}]
    calls = []

    async def request(method, path, body):
        calls.append(path)
        return {'characters': roles}

    assert await read_character_directory(request, OWNER) == {'characters': roles}
    assert calls == ['/prototype/chat-sync/characters?limit=100']

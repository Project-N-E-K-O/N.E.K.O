# -*- coding: utf-8 -*-
"""Follow-ups to the Memory Browser (PR #3918).

Covers: the Star Map background naming gate and failure cooldown, the
marker pairs around the memory blocks of three browser prompts, text
rewrite replay that writes lost rewrites back instead of pausing, snapshot
restore journaled as one event (replay, full-replay start, compaction),
and compressed vector-free snapshots with separate automatic quotas.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

NAME = "跟进猫"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _read_json(path: Path):
    return json.loads(path.read_bytes().decode("utf-8"))


def _cm(root: Path):
    cm = MagicMock()
    cm.memory_dir = str(root)
    cm.load_root_state.return_value = {"mode": "normal"}
    return cm


def _event_log(root: Path):
    from memory.event_log import EventLog

    cm = _cm(root)
    with patch("memory.event_log.get_config_manager", return_value=cm):
        log = EventLog()
    log._config_manager = cm
    return log


def _journal(root: Path) -> list[dict]:
    path = root / NAME / "events.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── ① Star Map naming ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("powerful", [False, True])
async def test_star_map_background_refresh_runs_only_with_powerful_memory(monkeypatch, powerful):
    from app.memory_server import evidence_loops as loops

    label = AsyncMock(return_value={"skipped": True, "labels": {}})
    monkeypatch.setattr(loops.gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=powerful))
    monkeypatch.setattr("memory.analytics.alabel_star_map_clusters", label)

    async def _direct(name, op, *args):
        return await op(*args)

    monkeypatch.setattr(loops, "run_with_character_prompt_locale", _direct)
    loops._star_map_label_last_probe.pop(NAME, None)
    try:
        await loops._amaybe_refresh_star_map_labels(NAME, datetime(2026, 11, 22, 12, 0))
    finally:
        loops._star_map_label_last_probe.pop(NAME, None)
    assert label.await_count == (1 if powerful else 0)


@pytest.mark.asyncio
async def test_star_map_label_failure_cools_down_like_success(tmp_path):
    from memory.analytics import star_map

    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    char_dir = tmp_path / NAME
    char_dir.mkdir()
    ready = {"ready": True, "clusters": [{"id": 0, "samples": ["morning coffee"]}], "corpus_hash": "h1"}
    t0 = datetime(2026, 11, 1, 9, 0)
    request = AsyncMock(side_effect=RuntimeError("upstream 400"))
    with patch.object(star_map, "abuild_star_map", AsyncMock(return_value=ready)), \
            patch.object(star_map, "assert_cloudsave_writable"), \
            patch.object(star_map, "_arequest_cluster_labels", request):
        with pytest.raises(RuntimeError):
            await star_map.alabel_star_map_clusters(NAME, config_manager=cm, language="en", force=False, now=t0)
        cache = _read_json(char_dir / "star_map_clusters_cache.json")
        assert cache["last_failed_at"] == t0.isoformat()

        # Background retries the next day are skipped without a model call.
        res = await star_map.alabel_star_map_clusters(
            NAME, config_manager=cm, language="en", force=False, now=t0 + timedelta(days=1),
        )
        assert res["skipped"] and res["reason"] == "failure_cooldown"
        assert request.await_count == 1

        # The manual button ignores the cooldown.
        with pytest.raises(RuntimeError):
            await star_map.alabel_star_map_clusters(
                NAME, config_manager=cm, language="en", force=True, now=t0 + timedelta(days=1),
            )
        assert request.await_count == 2

        # A week after the last failure the background may try again; success clears the mark.
        request.side_effect = None
        request.return_value = {"0": "Coffee"}
        res = await star_map.alabel_star_map_clusters(
            NAME, config_manager=cm, language="en", force=False, now=t0 + timedelta(days=9),
        )
        assert res["skipped"] is False and res["labels"] == {"0": "Coffee"}
        cache = _read_json(char_dir / "star_map_clusters_cache.json")
        assert "last_failed_at" not in cache and cache["labels"] == {"0": "Coffee"}


_OPEN = re.compile(r"^======(以下为[^=\n]*)======$", re.M)
_CLOSE = re.compile(r"^======(以上为[^=\n]*)======$", re.M)


@pytest.mark.parametrize("table_name,placeholder", [
    ("IMPRINT_NOTE_PROMPT", "{MEMORY_DIGEST}"),
    ("STAR_MAP_CLUSTER_LABEL_PROMPT", "{CLUSTERS}"),
    ("DEEP_CONFLICT_CHECK_PROMPT", "{PAIRS}"),
])
def test_browser_prompts_wrap_memory_blocks(table_name, placeholder):
    from config.prompts import prompts_memory

    table = getattr(prompts_memory, table_name)
    assert set(table) == {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    for lang, text in table.items():
        opens, closes = _OPEN.findall(text), _CLOSE.findall(text)
        assert len(opens) == 1 and len(closes) == 1, (table_name, lang)
        assert text.count("======") == 4, (table_name, lang)
        for body in (*opens, *closes):
            assert not re.search(r"[:：—]", body), (table_name, lang, body)
            assert not body.startswith(("以下为=", "以上为=")), (table_name, lang)
        assert opens[0][3:] == closes[0][3:], (table_name, lang)
        start = _OPEN.search(text).end()
        end = _CLOSE.search(text).start()
        assert text[start:end].strip() == placeholder, (table_name, lang)


# ── ② Text rewrite replay ────────────────────────────────────────────


def _persona_manager(root: Path, event_log=None):
    from memory.persona import PersonaManager

    cm = _cm(root)
    with patch("memory.persona.manager.get_config_manager", return_value=cm):
        pm = PersonaManager(event_log=event_log)
    pm._config_manager = cm
    return pm


def _reflection_engine(root: Path, pm, event_log=None):
    from memory.facts import FactStore
    from memory.reflection import ReflectionEngine

    cm = _cm(root)
    with patch("memory.facts.get_config_manager", return_value=cm), \
            patch("memory.reflection.manager.get_config_manager", return_value=cm):
        fs = FactStore()
        fs._config_manager = cm
        eng = ReflectionEngine(fs, pm, event_log=event_log)
    eng._config_manager = cm
    return eng


def _seed_persona(root: Path, text: str, **extra) -> Path:
    path = root / NAME / "persona.json"
    _write_json(path, {"master": {"facts": [{"id": "p1", "text": text, "source": "manual", **extra}]}})
    return path


def _rewrite_payload(old: str, new: str, **extra) -> dict:
    return {
        "entity_key": "master",
        "entry_id": "p1",
        "rewrite_text_sha256": _sha(new),
        "rewrite_text": new,
        "previous_text_sha256": _sha(old),
        **extra,
    }


def test_persona_rewrite_replay_normal_path_only_applies_snapshot_fields(tmp_path):
    from memory.evidence_handlers import make_persona_entry_handler

    path = _seed_persona(tmp_path, "new words")
    pm = _persona_manager(tmp_path)
    handler = make_persona_entry_handler(pm)
    assert handler(NAME, _rewrite_payload("old words", "new words", reinforcement=2.0)) is True
    entry = _read_json(path)["master"]["facts"][0]
    assert entry["text"] == "new words" and entry["reinforcement"] == 2.0
    assert "version_history" not in entry


def test_persona_rewrite_replay_writes_back_a_lost_edit(tmp_path):
    from memory.evidence_handlers import make_persona_entry_handler

    path = _seed_persona(
        tmp_path, "old words",
        embedding="AAAA", embedding_text_sha256=_sha("old words"), embedding_model_id="m-8d-int8",
        token_count=3,
    )
    pm = _persona_manager(tmp_path)
    handler = make_persona_entry_handler(pm)
    payload = _rewrite_payload(
        "old words", "new words", history_reason="manual_edit", replaced_at="2026-11-22T10:00:00",
    )
    assert handler(NAME, payload) is True
    entry = _read_json(path)["master"]["facts"][0]
    assert entry["text"] == "new words"
    assert entry["version_history"] == [
        {"text": "old words", "replaced_at": "2026-11-22T10:00:00", "reason": "manual_edit"},
    ]
    assert entry["embedding"] is None and entry["embedding_text_sha256"] is None
    assert entry["token_count"] is None
    assert pm._personas[NAME]["master"]["facts"][0]["text"] == "new words"
    # Idempotent: the second replay sees the new text and does nothing more.
    assert handler(NAME, payload) is False
    assert len(_read_json(path)["master"]["facts"][0]["version_history"]) == 1


def test_persona_rewrite_replay_skips_a_later_rewrite(tmp_path):
    from memory.evidence_handlers import make_persona_entry_handler

    path = _seed_persona(tmp_path, "the user's third wording")
    before = path.read_bytes()
    handler = make_persona_entry_handler(_persona_manager(tmp_path))
    assert handler(NAME, _rewrite_payload("old words", "new words", reinforcement=9.0)) is False
    assert path.read_bytes() == before


def test_persona_rewrite_chain_back_to_old_text_is_not_replayed(tmp_path):
    """A -> B -> A: both rewrites landed; replaying either adds no history row."""
    from memory.evidence_handlers import make_persona_entry_handler

    history = [
        {"text": "A", "replaced_at": "t1", "reason": "manual_edit"},
        {"text": "B", "replaced_at": "t2", "reason": "manual_edit"},
    ]
    path = _seed_persona(tmp_path, "A", version_history=history)
    before = path.read_bytes()
    handler = make_persona_entry_handler(_persona_manager(tmp_path))
    first = _rewrite_payload("A", "B", history_reason="manual_edit", replaced_at="t1")
    second = _rewrite_payload("B", "A", history_reason="manual_edit", replaced_at="t2")
    assert handler(NAME, first) is False
    assert handler(NAME, second) is False
    assert path.read_bytes() == before

    # Promotion merge: its reflection id already in merged_from_ids means it landed.
    path = _seed_persona(tmp_path, "A", merged_from_ids=["ref_1"])
    before = path.read_bytes()
    assert handler(NAME, _rewrite_payload("A", "merged", merged_from_ids=["ref_1"])) is False
    assert path.read_bytes() == before


def test_persona_rewrite_replay_skips_an_old_event_without_text(tmp_path):
    from memory.evidence_handlers import make_persona_entry_handler

    path = _seed_persona(tmp_path, "old words")
    before = path.read_bytes()
    handler = make_persona_entry_handler(_persona_manager(tmp_path))
    payload = {"entity_key": "master", "entry_id": "p1", "rewrite_text_sha256": _sha("new words")}
    assert handler(NAME, payload) is False
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_persona_edit_lost_in_a_crash_comes_back_on_replay(tmp_path):
    """End to end: the writer's payload is enough for replay to repair the view."""
    from memory.event_log import Reconciler
    from memory.evidence_handlers import register_evidence_handlers

    log = _event_log(tmp_path)
    path = _seed_persona(tmp_path, "old words")
    pm = _persona_manager(tmp_path, log)
    with patch("memory.persona.persistence.assert_cloudsave_writable"),             patch("memory.persona.facts.assert_cloudsave_writable"):
        before = path.read_bytes()
        await pm.aedit_persona_entry(NAME, "master", "p1", "new words")
        assert _read_json(path)["master"]["facts"][0]["text"] == "new words"
        # Simulate the save never landing: old view on disk, sentinel not advanced.
        path.write_bytes(before)
        (tmp_path / NAME / "events_applied.json").unlink()
        pm._personas.pop(NAME, None)

    reconciler = Reconciler(log)
    eng = _reflection_engine(tmp_path, pm, log)
    register_evidence_handlers(reconciler, pm, eng)
    await reconciler.areconcile(NAME)
    entry = _read_json(path)["master"]["facts"][0]
    assert entry["text"] == "new words"
    assert entry["version_history"][-1]["text"] == "old words"
    assert entry["version_history"][-1]["reason"] == "manual_edit"
    assert log.read_sentinel(NAME) == _journal(tmp_path)[-1]["event_id"]


def _seed_reflections(root: Path, rows: list[dict]) -> Path:
    path = root / NAME / "reflections.json"
    _write_json(path, rows)
    return path


def test_reflection_confirm_replay_writes_back_lost_edit_and_skips_later_one(tmp_path):
    from memory.evidence_handlers import make_reflection_state_changed_handler

    pm = _persona_manager(tmp_path)
    eng = _reflection_engine(tmp_path, pm)
    handler = make_reflection_state_changed_handler(eng)
    payload = {
        "reflection_id": "r1", "from": "pending", "to": "confirmed",
        "rewrite_text_sha256": _sha("edited"), "rewrite_text": "edited",
        "previous_text_sha256": _sha("draft"),
    }

    path = _seed_reflections(tmp_path, [{
        "id": "r1", "text": "draft", "status": "pending",
        "embedding": "AAAA", "embedding_model": "legacy",
    }])
    assert handler(NAME, payload) is True
    row = _read_json(path)[0]
    assert row["text"] == "edited" and row["status"] == "confirmed"
    assert row["embedding"] is None and "embedding_model" not in row

    path = _seed_reflections(tmp_path, [{"id": "r1", "text": "rewritten later", "status": "pending"}])
    before = path.read_bytes()
    assert handler(NAME, payload) is False
    assert path.read_bytes() == before

    # Confirmed with the edit, then edited straight back (no event): the save
    # landed (confirmed_at matches), so the old text is a later change.
    landed = {**payload, "ts": "2026-11-22T10:00:00"}
    path = _seed_reflections(tmp_path, [{
        "id": "r1", "text": "draft", "status": "confirmed", "confirmed_at": "2026-11-22T10:00:00",
    }])
    before = path.read_bytes()
    assert handler(NAME, landed) is False
    assert path.read_bytes() == before


def test_reflection_merge_archive_replay_three_states(tmp_path):
    from memory.evidence_handlers import make_reflection_archive_handler

    pm = _persona_manager(tmp_path)
    eng = _reflection_engine(tmp_path, pm)
    handler = make_reflection_archive_handler(eng)
    payload = {
        "reflection_id": "r_drop", "to": "archived",
        "merged_keep_id": "r_keep",
        "merged_keep_text_sha256": _sha("merged"),
        "merged_keep_text": "merged",
        "merged_keep_previous_text_sha256": _sha("keep before"),
    }

    # Crash: survivor still holds its old text -> merged text written back, drop archived.
    path = _seed_reflections(tmp_path, [
        {"id": "r_keep", "text": "keep before", "embedding": "AAAA"},
        {"id": "r_drop", "text": "drop"},
    ])
    assert handler(NAME, payload) is True
    rows = _read_json(path)
    assert [r["id"] for r in rows] == ["r_keep"]
    assert rows[0]["text"] == "merged" and rows[0]["embedding"] is None

    # Merge landed (drop gone) and the survivor was edited afterwards: survivor untouched.
    path = _seed_reflections(tmp_path, [{"id": "r_keep", "text": "edited afterwards"}])
    before = path.read_bytes()
    assert handler(NAME, payload) is False
    assert path.read_bytes() == before

    # Merge never landed and the user then changed either row: skipped whole.
    for rows in (
        [{"id": "r_keep", "text": "edited afterwards"}, {"id": "r_drop", "text": "drop"}],
        [{"id": "r_keep", "text": "merged"}, {"id": "r_drop", "text": "drop"}],
        [{"id": "r_keep", "text": "keep before"}, {"id": "r_drop", "text": "drop, edited"}],
        [{"id": "r_keep", "text": "keep before"}, {"id": "r_drop", "text": "drop", "status": "confirmed"}],
    ):
        path = _seed_reflections(tmp_path, rows)
        before = path.read_bytes()
        full = {**payload, "from": "pending", "entry_snapshot": {"id": "r_drop", "text": "drop"}}
        assert handler(NAME, full) is False
        assert path.read_bytes() == before

    # Older event without the merged text: skipped whole, nothing archived.
    old_payload = {k: v for k, v in payload.items() if k in (
        "reflection_id", "to", "merged_keep_id", "merged_keep_text_sha256")}
    path = _seed_reflections(tmp_path, [
        {"id": "r_keep", "text": "keep before"},
        {"id": "r_drop", "text": "drop"},
    ])
    before = path.read_bytes()
    assert handler(NAME, old_payload) is False
    assert path.read_bytes() == before


# ── ③ / ④ Snapshots ──────────────────────────────────────────────────


class _Facts:
    def __init__(self):
        import threading
        self._facts = {NAME: [{"stale": True}]}
        self._alock = asyncio.Lock()
        self._tlock = threading.Lock()

    def _get_persist_alock(self, _n):
        return self._alock

    def _get_lock(self, _n):
        return self._tlock


class _Persona:
    def __init__(self):
        self._personas = {NAME: {"stale": True}}
        self._lock = asyncio.Lock()

    def _get_alock(self, _n):
        return self._lock


_VEC = "AAAAAAAAAAA="


def _seed_memory(root: Path, persona_text: str, fact_text: str) -> Path:
    char_dir = root / NAME
    _write_json(char_dir / "persona.json", {"master": {"facts": [{
        "id": "p1", "text": persona_text,
        "embedding": _VEC, "embedding_text_sha256": _sha(persona_text), "embedding_model_id": "m-4d-int8",
    }]}})
    _write_json(char_dir / "facts.json", [{
        "id": "f1", "text": fact_text,
        "embedding": _VEC, "embedding_text_sha256": _sha(fact_text), "embedding_model_id": "m-4d-int8",
    }])
    return char_dir


async def _snapshot(root: Path, **kwargs):
    from memory.analytics.snapshots import acreate_snapshot

    with patch("memory.analytics.snapshots.assert_cloudsave_writable"):
        return await acreate_snapshot(NAME, config_manager=_cm(root), memory_dir=root, **kwargs)


async def _restore(root: Path, snapshot_id: str, *, event_log=None, **kwargs):
    from memory.analytics.snapshots import arestore_snapshot

    with patch("memory.analytics.snapshots.assert_cloudsave_writable"):
        return await arestore_snapshot(
            NAME, snapshot_id, config_manager=_cm(root), memory_dir=root,
            persona_manager=_Persona(), fact_store=_Facts(), event_log=event_log,
            directives_manager=MagicMock(spec=[]), **kwargs,
        )


@pytest.mark.asyncio
async def test_snapshot_is_compressed_and_vector_free(tmp_path):
    char_dir = _seed_memory(tmp_path, "likes jazz", "drinks oolong")
    meta = await _snapshot(tmp_path, label="before")
    snap_dir = char_dir / "browser_snapshots" / meta["snapshot_id"]
    assert meta["format"] == 2 and sorted(meta["files"]) == ["facts.json", "persona.json"]
    assert not (snap_dir / "persona.json").exists()
    stored = json.loads(gzip.decompress((snap_dir / "persona.json.gz").read_bytes()))
    entry = stored["master"]["facts"][0]
    assert entry["text"] == "likes jazz"
    assert entry["embedding"] is None and entry["embedding_text_sha256"] is None
    assert _VEC not in gzip.decompress((snap_dir / "facts.json.gz").read_bytes()).decode("utf-8")


@pytest.mark.asyncio
async def test_restore_takes_vectors_back_only_for_unchanged_text(tmp_path):
    char_dir = _seed_memory(tmp_path, "likes jazz", "drinks oolong")
    meta = await _snapshot(tmp_path)
    # Live persona keeps its text (and vector); live fact is reworded.
    facts = _read_json(char_dir / "facts.json")
    facts[0].update(text="drinks coffee", embedding_text_sha256=_sha("drinks coffee"))
    _write_json(char_dir / "facts.json", facts)

    res = await _restore(tmp_path, meta["snapshot_id"])
    assert set(res["restored_files"]) == {"persona.json", "facts.json"}
    persona = _read_json(char_dir / "persona.json")["master"]["facts"][0]
    assert persona["embedding"] == _VEC and persona["embedding_text_sha256"] == _sha("likes jazz")
    fact = _read_json(char_dir / "facts.json")[0]
    assert fact["text"] == "drinks oolong"
    assert fact["embedding"] is None and fact["embedding_model_id"] is None


@pytest.mark.asyncio
async def test_legacy_plain_snapshot_still_lists_and_restores(tmp_path):
    from memory.analytics.snapshots import alist_snapshots

    char_dir = _seed_memory(tmp_path, "now", "now fact")
    legacy = char_dir / "browser_snapshots" / "snap_20260101_000000_abcdef"
    _write_json(legacy / "persona.json", {"master": {"facts": [{
        "id": "p1", "text": "legacy", "embedding": _VEC, "embedding_text_sha256": _sha("legacy"),
    }]}})
    _write_json(legacy / "meta.json", {
        "snapshot_id": legacy.name, "label": "old", "reason": "manual",
        "created_at": "2026-01-01T00:00:00", "files": ["persona.json"], "stats": {},
    })
    listed = await alist_snapshots(NAME, memory_dir=tmp_path)
    assert [s["snapshot_id"] for s in listed] == [legacy.name]
    await _restore(tmp_path, legacy.name)
    persona = _read_json(char_dir / "persona.json")["master"]["facts"][0]
    assert persona["text"] == "legacy" and persona["embedding"] == _VEC
    assert not (char_dir / "facts.json").exists()


@pytest.mark.asyncio
async def test_auto_snapshots_have_their_own_quota_and_merges_share_one(tmp_path):
    from memory.analytics.snapshots import alist_snapshots

    char_dir = _seed_memory(tmp_path, "a", "b")
    manual = [await _snapshot(tmp_path, label=f"m{i}") for i in range(4)]
    first = await _snapshot(tmp_path, reason="pre_merge")
    again = await _snapshot(tmp_path, reason="pre_merge")
    assert again["snapshot_id"] == first["snapshot_id"]

    # Age the shared backup past the reuse window: the next merge takes a new one.
    meta_path = char_dir / "browser_snapshots" / first["snapshot_id"] / "meta.json"
    meta = _read_json(meta_path)
    meta["created_at"] = (datetime.now() - timedelta(minutes=11)).isoformat()
    _write_json(meta_path, meta)
    later = await _snapshot(tmp_path, reason="pre_merge")
    assert later["snapshot_id"] != first["snapshot_id"]

    for _ in range(3):
        await _snapshot(tmp_path, reason="pre_restore")
    listed = await alist_snapshots(NAME, memory_dir=tmp_path)
    autos = [s for s in listed if s["reason"] in ("pre_merge", "pre_restore")]
    manuals = [s for s in listed if s["reason"] == "manual"]
    assert len(autos) == 3
    assert {s["snapshot_id"] for s in manuals} == {m["snapshot_id"] for m in manual}


@pytest.mark.asyncio
async def test_restoring_the_oldest_auto_snapshot_at_capacity_keeps_it(tmp_path):
    char_dir = _seed_memory(tmp_path, "a", "b")
    oldest = await _snapshot(tmp_path, reason="pre_restore", auto_max_keep=2)
    await _snapshot(tmp_path, reason="pre_restore", auto_max_keep=2)
    await _restore(tmp_path, oldest["snapshot_id"], auto_max_keep=2)
    assert (char_dir / "browser_snapshots" / oldest["snapshot_id"]).is_dir()


@pytest.mark.asyncio
async def test_restore_is_one_journaled_event(tmp_path):
    from memory.analytics.snapshots import content_fingerprint
    from memory.event_log import EVT_MEMORY_SNAPSHOT_RESTORED

    log = _event_log(tmp_path)
    char_dir = _seed_memory(tmp_path, "likes jazz", "drinks oolong")
    meta = await _snapshot(tmp_path)
    _write_json(char_dir / "facts.json", [{"id": "f2", "text": "new"}])
    pre_bytes = (char_dir / "facts.json").read_bytes()

    res = await _restore(tmp_path, meta["snapshot_id"], event_log=log)
    events = _journal(tmp_path)
    assert [e["type"] for e in events] == [EVT_MEMORY_SNAPSHOT_RESTORED]
    payload = events[0]["payload"]
    assert payload["snapshot_id"] == meta["snapshot_id"]
    assert payload["pre_restore_snapshot_id"] == res["pre_restore_snapshot_id"]
    assert payload["pre_restore_files"]["facts.json"] == content_fingerprint(pre_bytes)
    assert payload["target_files"]["facts.json"] == content_fingerprint((char_dir / "facts.json").read_bytes())
    assert log.read_sentinel(NAME) == events[0]["event_id"]
    assert (char_dir / ".long_term_generation").is_file()


def _restore_handler(root: Path):
    from memory.analytics.snapshots import make_snapshot_restore_handler

    return make_snapshot_restore_handler(
        fact_store=_Facts(), persona_manager=_Persona(),
        directives_manager_fn=lambda: None, memory_dir_fn=lambda: root,
    )


async def _restored_with_event(root: Path):
    log = _event_log(root)
    char_dir = _seed_memory(root, "likes jazz", "drinks oolong")
    meta = await _snapshot(root)
    _write_json(char_dir / "persona.json", {"master": {"facts": [{"id": "p1", "text": "likes rock"}]}})
    _write_json(char_dir / "facts.json", [{"id": "f2", "text": "new"}])
    pre = {rel: (char_dir / rel).read_bytes() for rel in ("persona.json", "facts.json")}
    await _restore(root, meta["snapshot_id"], event_log=log)
    target = {rel: (char_dir / rel).read_bytes() for rel in ("persona.json", "facts.json")}
    return log, char_dir, meta, pre, target, _journal(root)[-1]["payload"]


@pytest.mark.asyncio
async def test_restore_replay_finishes_an_interrupted_swap(tmp_path):
    from memory.analytics.snapshots import content_fingerprint

    _log, char_dir, _meta, pre, target, payload = await _restored_with_event(tmp_path)
    # Crash after the first file: facts.json still holds the pre-restore version.
    (char_dir / "facts.json").write_bytes(pre["facts.json"])
    handler = _restore_handler(tmp_path)
    assert handler(NAME, payload) is True
    assert content_fingerprint((char_dir / "facts.json").read_bytes()) == content_fingerprint(target["facts.json"])
    assert _read_json(char_dir / "persona.json")["master"]["facts"][0]["text"] == "likes jazz"
    # Done now: replaying again changes nothing.
    assert handler(NAME, payload) is False


@pytest.mark.asyncio
async def test_restore_replay_leaves_later_changes_and_missing_snapshots_alone(tmp_path):
    import shutil

    _log, char_dir, meta, pre, _target, payload = await _restored_with_event(tmp_path)
    handler = _restore_handler(tmp_path)

    # Completed restore followed by an edit: neither version -> nothing to do.
    _write_json(char_dir / "facts.json", [{"id": "f9", "text": "edited after restore"}])
    edited = (char_dir / "facts.json").read_bytes()
    assert handler(NAME, payload) is False
    assert (char_dir / "facts.json").read_bytes() == edited

    # A vector backfill alone is not a content change.
    persona = _read_json(char_dir / "persona.json")
    (char_dir / "facts.json").write_bytes(pre["facts.json"])
    persona["master"]["facts"][0]["embedding"] = "BBBB"
    _write_json(char_dir / "persona.json", persona)
    shutil.rmtree(char_dir / "browser_snapshots" / meta["snapshot_id"])
    # Interrupted, but the snapshot is gone: logged and skipped, files untouched.
    assert handler(NAME, payload) is False
    assert (char_dir / "facts.json").read_bytes() == pre["facts.json"]


@pytest.mark.asyncio
async def test_registered_restore_replay_drains_and_full_replay_starts_at_restore(tmp_path):
    from memory.analytics.snapshots import register_snapshot_restore_replay
    from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED, Reconciler

    log, char_dir, _meta, pre, _target, _payload = await _restored_with_event(tmp_path)
    before_id = log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"reflection_id": "x"})
    # Move the pre-restore event in front of the restore record in the body.
    lines = (char_dir / "events.ndjson").read_text(encoding="utf-8").splitlines()
    (char_dir / "events.ndjson").write_bytes(("\n".join([lines[1], lines[0]]) + "\n").encode("utf-8"))
    restore_id = json.loads(lines[0])["event_id"]
    after_id = log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"reflection_id": "y"})

    assert [r["event_id"] for r in log.read_since(NAME, None)] == [restore_id, after_id]
    assert [r["event_id"] for r in log.read_since(NAME, "not-in-body")] == [restore_id, after_id]
    assert [r["event_id"] for r in log.read_since(NAME, before_id)] == [restore_id, after_id]
    assert [r["event_id"] for r in log.read_since(NAME, restore_id)] == [after_id]

    (char_dir / "facts.json").write_bytes(pre["facts.json"])
    (char_dir / "events_applied.json").unlink()
    reconciler = Reconciler(log)
    seen = []
    reconciler.register(EVT_REFLECTION_EVIDENCE_UPDATED, lambda n, p: seen.append(p["reflection_id"]) or False)
    register_snapshot_restore_replay(
        reconciler, fact_store=_Facts(), persona_manager=_Persona(),
        directives_manager_fn=lambda: None, memory_dir_fn=lambda: tmp_path,
    )
    applied = await reconciler.areconcile(NAME)
    assert applied == 1 and seen == ["y"]
    assert log.read_sentinel(NAME) == after_id
    assert _read_json(char_dir / "facts.json") == [{
        "id": "f1", "text": "drinks oolong",
        "embedding": None, "embedding_text_sha256": None, "embedding_model_id": None,
    }]


def test_compaction_keeps_only_the_newest_restore_record(tmp_path):
    from memory import event_log as event_log_module
    from memory.event_log import EVT_MEMORY_SNAPSHOT_RESTORED, EVT_REFLECTION_EVIDENCE_UPDATED

    log = _event_log(tmp_path)
    old = (datetime.now() - timedelta(days=40)).isoformat()
    rows = [
        {"event_id": "a", "type": EVT_REFLECTION_EVIDENCE_UPDATED},
        {"event_id": "r1", "type": EVT_MEMORY_SNAPSHOT_RESTORED},
        {"event_id": "b", "type": EVT_REFLECTION_EVIDENCE_UPDATED},
        {"event_id": "r2", "type": EVT_MEMORY_SNAPSHOT_RESTORED},
        {"event_id": "c", "type": EVT_REFLECTION_EVIDENCE_UPDATED},
        {"event_id": "anchor", "type": EVT_REFLECTION_EVIDENCE_UPDATED},
    ]
    char_dir = tmp_path / NAME
    char_dir.mkdir()
    (char_dir / "events.ndjson").write_bytes("".join(
        json.dumps({**row, "ts": old, "payload": {}}) + "\n" for row in rows
    ).encode("utf-8"))
    _write_json(char_dir / "events_applied.json", {"last_applied_event_id": "anchor"})
    with patch.object(event_log_module, "_RETAIN_APPLIED_DAYS", 0), \
            patch.object(event_log_module, "_RETAIN_APPLIED_MAX_BYTES", 0):
        result = log.compact_applied_history(NAME)
    assert result.outcome == "compacted"
    assert "r2" in result.pinned_event_ids
    assert [e["event_id"] for e in _journal(tmp_path)] == ["r2", "anchor"]
    # A later full replay still starts at the restore record.
    assert [r["event_id"] for r in log.read_since(NAME, None)] == ["r2", "anchor"]


@pytest.mark.asyncio
async def test_restore_clears_caches_when_the_step_after_the_swap_fails(tmp_path):
    from memory.analytics.snapshots import arestore_snapshot

    log = _event_log(tmp_path)
    char_dir = _seed_memory(tmp_path, "likes jazz", "drinks oolong")
    meta = await _snapshot(tmp_path)
    _write_json(char_dir / "facts.json", [{"id": "f2", "text": "new"}])
    persona, facts = _Persona(), _Facts()
    facts._time_indexed = MagicMock()
    facts._time_indexed.arebuild_fact_index = AsyncMock()

    def _broken_sentinel(*_args, **_kwargs):
        raise OSError("disk full")

    log._write_sentinel_unlocked = _broken_sentinel
    with patch("memory.analytics.snapshots.assert_cloudsave_writable"), pytest.raises(OSError):
        await arestore_snapshot(
            NAME, meta["snapshot_id"], config_manager=_cm(tmp_path), memory_dir=tmp_path,
            persona_manager=persona, fact_store=facts, event_log=log,
            directives_manager=MagicMock(spec=[]),
        )
    assert _read_json(char_dir / "facts.json")[0]["id"] == "f1"
    assert NAME not in persona._personas and NAME not in facts._facts
    facts._time_indexed.arebuild_fact_index.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_restore_is_finished_ahead_of_startup_rewrites(tmp_path):
    from memory.analytics.snapshots import register_snapshot_restore_replay
    from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED, Reconciler

    log, char_dir, _meta, pre, _target, _payload = await _restored_with_event(tmp_path)
    events = _journal(tmp_path)
    restore_line = (char_dir / "events.ndjson").read_text(encoding="utf-8")
    # Crash during the restore: an unapplied event sits between the sentinel and
    # the restore record, and facts.json is still the pre-restore version.
    anchor = log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"reflection_id": "anchor"})
    stale = log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"reflection_id": "stale"})
    lines = (char_dir / "events.ndjson").read_text(encoding="utf-8").splitlines()
    body = [lines[1], lines[2], restore_line.strip()]
    (char_dir / "events.ndjson").write_bytes(("\n".join(body) + "\n").encode("utf-8"))
    _write_json(char_dir / "events_applied.json", {"last_applied_event_id": anchor})
    (char_dir / "facts.json").write_bytes(pre["facts.json"])

    # read_since hands back the whole tail; the Reconciler cuts it once the restore is in effect.
    assert [r["event_id"] for r in log.read_since(NAME, anchor)] == [stale, events[0]["event_id"]]

    reconciler = Reconciler(log)
    seen = []
    reconciler.register(EVT_REFLECTION_EVIDENCE_UPDATED, lambda n, p: seen.append(p["reflection_id"]) or False)
    register_snapshot_restore_replay(
        reconciler, fact_store=_Facts(), persona_manager=_Persona(),
        directives_manager_fn=lambda: None, memory_dir_fn=lambda: tmp_path,
    )
    assert await reconciler.arun_pending_ahead(NAME, "memory.snapshot_restored") is True
    assert _read_json(char_dir / "facts.json")[0]["id"] == "f1"
    assert log.read_sentinel(NAME) == anchor

    # A startup step rewriting persona.json now no longer hides the restore.
    persona = _read_json(char_dir / "persona.json")
    persona["master"]["facts"].append({"id": "card", "text": "card sync", "source": "character_card"})
    _write_json(char_dir / "persona.json", persona)
    assert await reconciler.areconcile(NAME) == 0
    assert seen == [] and stale not in seen
    assert log.read_sentinel(NAME) == events[0]["event_id"]


@pytest.mark.asyncio
async def test_restore_that_never_took_effect_keeps_earlier_events_replayable(tmp_path):
    import shutil
    from memory.analytics.snapshots import register_snapshot_restore_replay
    from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED, Reconciler

    log, char_dir, meta, pre, _target, _payload = await _restored_with_event(tmp_path)
    restore_line = (char_dir / "events.ndjson").read_text(encoding="utf-8").strip()
    anchor = log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"reflection_id": "anchor"})
    stale = log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"reflection_id": "stale"})
    lines = (char_dir / "events.ndjson").read_text(encoding="utf-8").splitlines()
    body = [lines[1], lines[2], restore_line]
    (char_dir / "events.ndjson").write_bytes("".join(line + chr(10) for line in body).encode("utf-8"))
    _write_json(char_dir / "events_applied.json", {"last_applied_event_id": anchor})
    # Crash before the swap, and the snapshot is gone by the next start.
    for rel, raw in pre.items():
        (char_dir / rel).write_bytes(raw)
    shutil.rmtree(char_dir / "browser_snapshots" / meta["snapshot_id"])

    reconciler = Reconciler(log)
    seen = []
    reconciler.register(EVT_REFLECTION_EVIDENCE_UPDATED, lambda n, p: seen.append(p["reflection_id"]) or False)
    register_snapshot_restore_replay(
        reconciler, fact_store=_Facts(), persona_manager=_Persona(),
        directives_manager_fn=lambda: None, memory_dir_fn=lambda: tmp_path,
    )
    await reconciler.areconcile(NAME)
    assert seen == ["stale"]
    assert {rel: (char_dir / rel).read_bytes() for rel in pre} == pre

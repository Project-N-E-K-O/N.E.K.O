# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for NekoPower disk space management and automatic cleanup."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from config.nekopower_settings import SPACE_UPKEEP_INTERVAL_SECONDS
from nekopower.host.paths import DEFAULT_PROJECT, SYSTEM_SNAPSHOT_NAME, TRANSCRIPT_NAME
from nekopower.host.storage import (
    StorageSettingsStore,
    format_work_dir_id,
    parse_work_dir_id,
    remove_dir_safely,
)
from tests.unit.nekopower.host_fakes import make_host

pytestmark = pytest.mark.unit

DAY = 86400.0


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _set_mtime(path: Path, timestamp: float) -> None:
    os.utime(path, (timestamp, timestamp))


def _set_tree_mtime(root: Path, timestamp: float) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            _set_mtime(Path(dirpath) / name, timestamp)
        _set_mtime(Path(dirpath), timestamp)


# ---------------------------------------------------------------------------
# Pure helpers: settings store, id validation, symlink-safe removal
# ---------------------------------------------------------------------------


async def test_storage_settings_store_defaults_persistence_and_validation(tmp_path: Path):
    path = tmp_path / "nekopower" / "storage.json"
    store = StorageSettingsStore(path)

    initial = await store.describe()
    assert initial["auto_clean_work_dirs"] is True
    assert initial["work_dir_kept_days"] == 30
    assert initial["auto_clean_worktrees"] is True
    assert initial["worktree_kept_days"] == 14
    assert not path.exists()  # reading defaults does not write anything

    updated = await store.put(
        auto_clean_work_dirs=False,
        work_dir_kept_days=45,
        worktree_kept_days=7,
    )
    assert updated["auto_clean_work_dirs"] is False
    assert updated["work_dir_kept_days"] == 45
    assert updated["auto_clean_worktrees"] is True
    assert updated["worktree_kept_days"] == 7
    assert path.is_file()

    # Reload from disk in a fresh store instance.
    reloaded = await StorageSettingsStore(path).get()
    assert reloaded == {
        "auto_clean_work_dirs": False,
        "work_dir_kept_days": 45,
        "auto_clean_worktrees": True,
        "worktree_kept_days": 7,
    }

    with pytest.raises(ValueError, match="bad_flag"):
        await store.put(auto_clean_work_dirs="yes")
    with pytest.raises(ValueError, match="out_of_range"):
        await store.put(work_dir_kept_days=0)
    with pytest.raises(ValueError, match="out_of_range"):
        await store.put(worktree_kept_days=999)

    # Corrupt json fails closed (auto-clean disabled), does NOT cache defaults, and refuses overwrites.
    path.write_text("{not valid json", encoding="utf-8")
    corrupt_store = StorageSettingsStore(path)
    fallback = await corrupt_store.get()
    assert fallback["auto_clean_work_dirs"] is False
    assert fallback["auto_clean_worktrees"] is False
    with pytest.raises(OSError):
        await corrupt_store.put(work_dir_kept_days=60)

    # Once the file recovers, the same store instance re-reads it instead of staying stuck on fallback.
    path.write_text(
        '{"version": 1, "auto_clean_work_dirs": false, "work_dir_kept_days": 45, '
        '"auto_clean_worktrees": true, "worktree_kept_days": 7}',
        encoding="utf-8",
    )
    recovered = await corrupt_store.get()
    assert recovered["auto_clean_work_dirs"] is False
    assert recovered["work_dir_kept_days"] == 45
    assert recovered["auto_clean_worktrees"] is True
    assert recovered["worktree_kept_days"] == 7


def test_parse_work_dir_id_rejects_traversal_and_malformed_ids():
    assert parse_work_dir_id("session:_daily/s1") == ("session", "_daily", "s1")
    assert parse_work_dir_id("project:alpha") == ("project", "alpha", None)
    assert format_work_dir_id("session", "_daily", "s1") == "session:_daily/s1"
    assert format_work_dir_id("project", "alpha") == "project:alpha"

    for bad in (
        "",
        "session:only-one",
        "session:a/b/c",
        "session:../escape/s1",
        "session:a/..",
        "project:../escape",
        "project:a/b",
        "other:a",
        123,
    ):
        with pytest.raises(ValueError):
            parse_work_dir_id(bad)


def test_remove_dir_safely_handles_readonly_outside_root_and_links(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "keep.txt"
    secret.write_text("do not touch", encoding="utf-8")

    # Refuses paths outside allowed root.
    with pytest.raises(ValueError, match="unsafe_path"):
        remove_dir_safely(outside, allowed)
    assert secret.is_file()

    # Refuses deleting allowed root itself.
    with pytest.raises(ValueError, match="unsafe_path"):
        remove_dir_safely(allowed, allowed)

    # Removes nested + read-only files and counts freed bytes.
    target = allowed / "work"
    nested = target / "sub"
    nested.mkdir(parents=True)
    ro = nested / "readonly.bin"
    ro.write_bytes(b"a" * 120)
    os.chmod(ro, stat.S_IREAD)

    # Also place a junction (Windows) or symlink (POSIX) inside target pointing to outside.
    link_inside = target / "ext_link"
    created_link = False
    if sys.platform == "win32":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_inside), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        created_link = proc.returncode == 0
    else:
        os.symlink(outside, link_inside)
        created_link = True

    removed, freed = remove_dir_safely(target, allowed)
    assert removed is True
    assert freed == 120
    assert not target.exists()
    # Target outside the link must remain completely intact!
    assert created_link
    assert secret.is_file()
    assert secret.read_text(encoding="utf-8") == "do not touch"


# ---------------------------------------------------------------------------
# StorageManager overview & manual cleanup
# ---------------------------------------------------------------------------


async def test_storage_overview_is_lazy_on_empty_host(tmp_path: Path):
    host = make_host(tmp_path)
    try:
        view = await host.storage_overview()
        assert view["total_bytes"] == 0
        assert view["reclaimable_bytes"] == 0
        assert view["worktrees"] == []
        assert view["work_dirs"] == []
        assert not host.paths.space().root.exists()
        assert not host.paths.storage_json().exists()
    finally:
        await host.shutdown()


async def test_storage_overview_and_manual_work_dir_cleanup(tmp_path: Path):
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        # 1. Idle session with a stale work/ dir (40 days old) + transcript + outputs.
        idle_sess = await host.ledger.create_session(name="old-job", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(idle_sess.id, status="idle")
        old_ts = clock.now - 40 * DAY
        # Backdate session last_active_at in ledger for test precision.
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, idle_sess.id))
        )
        idle_work = host.paths.work_dir(idle_sess.id, DEFAULT_PROJECT)
        idle_work.mkdir(parents=True)
        (idle_work / "artifact.bin").write_bytes(b"x" * 1000)
        _set_tree_mtime(idle_work, old_ts)

        idle_sdir = host.paths.session_dir(idle_sess.id, DEFAULT_PROJECT)
        (idle_sdir / TRANSCRIPT_NAME).write_bytes(b"t" * 400)
        (idle_sdir / SYSTEM_SNAPSHOT_NAME).write_bytes(b"s" * 100)
        (idle_sdir / "outputs").mkdir(parents=True)
        (idle_sdir / "outputs" / "big.txt").write_bytes(b"o" * 600)

        # 2. Active session (running) with a work/ dir -> must NOT be cleanable.
        live_sess = await host.ledger.create_session(name="live-job", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(live_sess.id, status="running")
        live_work = host.paths.work_dir(live_sess.id, DEFAULT_PROJECT)
        live_work.mkdir(parents=True)
        (live_work / "wip.txt").write_bytes(b"w" * 500)

        # 3. Empty idle session work/ dir -> stale & cleanable.
        empty_sess = await host.ledger.create_session(name="empty-job", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(empty_sess.id, status="idle")
        empty_work = host.paths.work_dir(empty_sess.id, DEFAULT_PROJECT)
        empty_work.mkdir(parents=True)

        # 4. Orphan worktree directory under wt/.
        orphan_wt = host.paths.worktree_dir("orphan01")
        orphan_wt.mkdir(parents=True)
        (orphan_wt / "leftover.bin").write_bytes(b"z" * 2000)

        # 5. Familiar space file (never cleanable).
        space_root = host.paths.space().root
        space_root.mkdir(parents=True)
        (space_root / "AGENTS.md").write_bytes(b"r" * 300)

        view = await host.storage_overview()
        cats = {c["key"]: c for c in view["categories"]}
        assert cats["worktrees"]["bytes"] == 2000
        assert cats["worktrees"]["reclaimable_bytes"] == 2000
        assert cats["work_dirs"]["bytes"] == 1500
        assert cats["work_dirs"]["reclaimable_bytes"] == 1000  # live_work (500) excluded
        # Expired session's transcript (500) + outputs (600) = 1100; must NOT double-count outputs!
        assert cats["transcripts"]["bytes"] == 1100
        assert cats["transcripts"]["reclaimable_bytes"] == 1100
        assert cats["space"]["bytes"] == 300
        assert cats["space"]["cleanable"] is False
        assert view["reclaimable_bytes"] == 2000 + 1000 + 1100

        wd_by_id = {d["id"]: d for d in view["work_dirs"]}
        idle_id = format_work_dir_id("session", DEFAULT_PROJECT, idle_sess.id)
        live_id = format_work_dir_id("session", DEFAULT_PROJECT, live_sess.id)
        empty_id = format_work_dir_id("session", DEFAULT_PROJECT, empty_sess.id)
        assert wd_by_id[idle_id]["can_clean"] is True and wd_by_id[idle_id]["stale"] is True
        assert wd_by_id[live_id]["can_clean"] is False and wd_by_id[live_id]["in_use"] is True
        assert wd_by_id[empty_id]["can_clean"] is True and wd_by_id[empty_id]["empty"] is True and wd_by_id[empty_id]["stale"] is True

        # Manual clean refuses live_id, cleans idle_id and empty_id.
        res = await host.clean_work_dirs([idle_id, live_id, empty_id])
        assert set(res["cleaned"]) == {idle_id, empty_id}
        assert res["freed_bytes"] == 1000
        assert res["failed"] == [{"id": live_id, "code": "in_use"}]
        assert not idle_work.exists()
        assert not empty_work.exists()
        assert live_work.is_dir()

        # Manual orphan worktree cleanup removes orphan01.
        wt_res = await host.clean_worktrees(["orphan01"])
        assert wt_res["cleaned"] == ["orphan01"]
        assert wt_res["freed_bytes"] == 2000
        assert not orphan_wt.exists()

        # Manual inactive outputs cleanup removes outputs/ while leaving transcript intact.
        out_res = await host.clean_transcripts("outputs")
        assert out_res["cleaned"] == 1
        assert out_res["freed_bytes"] == 600
        assert not (idle_sdir / "outputs").exists()
        assert (idle_sdir / TRANSCRIPT_NAME).is_file()

        # Manual expired transcripts cleanup clears old session record.
        exp_res = await host.clean_transcripts("expired")
        assert exp_res["cleaned"] == 1
        assert exp_res["freed_bytes"] == 500
        assert not (idle_sdir / TRANSCRIPT_NAME).exists()
    finally:
        await host.shutdown()


async def test_clean_inactive_outputs_skips_sessions_with_uncleared_forks(tmp_path: Path):
    host = make_host(tmp_path)
    try:
        src = await host.ledger.create_session(name="source", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(src.id, status="idle")
        fork = await host.ledger.create_session(
            name="fork-1", lanlan_name="Lan", project=DEFAULT_PROJECT, kind="fork", forked_from=src.id
        )
        await host.ledger.update_session(fork.id, status="idle")
        out_dir = host.paths.session_dir(src.id, DEFAULT_PROJECT) / "outputs"
        out_dir.mkdir(parents=True)
        (out_dir / "tool.txt").write_bytes(b"q" * 512)

        # Uncleared fork protects source's outputs/.
        res = await host.clean_transcripts("outputs")
        assert res["cleaned"] == 0
        assert out_dir.is_dir()

        # Once the fork is cleared, source's outputs/ can be cleaned.
        await host.ledger.update_session(fork.id, status="cleared")
        res2 = await host.clean_transcripts("outputs")
        assert res2["cleaned"] == 1
        assert res2["freed_bytes"] == 512
        assert not out_dir.exists()
    finally:
        await host.shutdown()


async def test_clean_worktrees_discards_tracked_kept_and_retries_discarding(tmp_path: Path):
    host = make_host(tmp_path)
    try:
        # Prepare a dummy repo dir that is NOT a git repo so _repository_gone returns True
        # and _remove_what_is_left removes the worktree folder cleanly without needing git.
        repo_dir = tmp_path / "gone-repo"
        wt_kept = host.paths.worktree_dir("kept0001")
        wt_kept.mkdir(parents=True)
        (wt_kept / ".git").write_text(f"gitdir: {(repo_dir / '.git' / 'worktrees' / 'kept0001').as_posix()}\n", encoding="utf-8")
        (wt_kept / "notes.txt").write_bytes(b"k" * 700)

        wt_disc = host.paths.worktree_dir("disc0001")
        wt_disc.mkdir(parents=True)
        (wt_disc / ".git").write_text(f"gitdir: {(repo_dir / '.git' / 'worktrees' / 'disc0001').as_posix()}\n", encoding="utf-8")
        (wt_disc / "old.txt").write_bytes(b"d" * 300)

        comm, _ = await host.ledger.create(
            dispatch_text="fix bug", title="fix bug", lanlan_name="Lan", source="dispatch", route="task_agent"
        )
        sess = await host.ledger.create_session(name="worker", lanlan_name="Lan", project="alpha")
        await host.ledger.add_tree(
            id="kept0001",
            session_id=sess.id,
            commission_id=comm.id,
            lanlan_name="Lan",
            project="alpha",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_kept),
            workdir=str(wt_kept),
            state="kept",
        )
        await host.ledger.add_tree(
            id="disc0001",
            session_id=sess.id,
            commission_id=comm.id,
            lanlan_name="Lan",
            project="alpha",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_disc),
            workdir=str(wt_disc),
            state="discarding",
        )

        view = await host.storage_overview()
        by_id = {t["id"]: t for t in view["worktrees"]}
        assert by_id["kept0001"]["can_clean"] is True
        assert by_id["disc0001"]["can_clean"] is True and by_id["disc0001"]["stale"] is True

        res = await host.clean_worktrees(["kept0001", "disc0001"])
        assert set(res["cleaned"]) == {"kept0001", "disc0001"}
        assert res["deferred"] == []
        assert res["failed"] == []
        assert not wt_kept.exists()
        assert not wt_disc.exists()
        assert await host.ledger.get_tree("kept0001") is None
        assert await host.ledger.get_tree("disc0001") is None
    finally:
        await host.shutdown()


# ---------------------------------------------------------------------------
# Automatic cleanup via SpaceKeeper.tidy()
# ---------------------------------------------------------------------------


async def test_auto_cleanup_work_dirs_uses_clock_confirmation_and_spares_recent_or_project_refs(tmp_path: Path):
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        # Configure short retention: 7 days for work dirs.
        await host.set_storage_settings(auto_clean_work_dirs=True, work_dir_kept_days=7)

        old_ts = clock.now - 10 * DAY
        fresh_ts = clock.now - 1 * DAY

        # 1. Truly old session work/ -> should be cleaned on second confirmed tidy.
        old_s = await host.ledger.create_session(name="old", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(old_s.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, old_s.id))
        )
        old_work = host.paths.work_dir(old_s.id, DEFAULT_PROJECT)
        old_work.mkdir(parents=True)
        (old_work / "a.txt").write_text("stale", encoding="utf-8")
        _set_tree_mtime(old_work, old_ts)

        # 2. Old session in ledger, BUT user edited a file inside work/ yesterday -> MUST be spared!
        edited_s = await host.ledger.create_session(name="edited", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(edited_s.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, edited_s.id))
        )
        edited_work = host.paths.work_dir(edited_s.id, DEFAULT_PROJECT)
        edited_work.mkdir(parents=True)
        (edited_work / "user_edit.txt").write_text("fresh", encoding="utf-8")
        _set_tree_mtime(edited_work, fresh_ts)

        # 3. Old session whose work/ is referenced by an active project's workdir -> MUST be spared!
        proj_s = await host.ledger.create_session(name="proj-s", lanlan_name="Lan", project="demo")
        await host.ledger.update_session(proj_s.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, proj_s.id))
        )
        proj_work = host.paths.work_dir(proj_s.id, "demo")
        proj_work.mkdir(parents=True)
        (proj_work / "proj.txt").write_text("project file", encoding="utf-8")
        _set_tree_mtime(proj_work, old_ts)
        await host.space.touch_project("demo", workdir=str(proj_work))

        # First tidy: only notes the clock reading, deletes nothing (clock-jump guard).
        await host.space_keeper.tidy()
        assert old_work.is_dir()
        assert edited_work.is_dir()
        assert proj_work.is_dir()

        # Advance past upkeep interval -> second tidy confirms and reaps ONLY old_work.
        clock.now += SPACE_UPKEEP_INTERVAL_SECONDS + 60.0
        await host.space_keeper.tidy()
        assert not old_work.exists()
        assert edited_work.is_dir()
        assert proj_work.is_dir()
    finally:
        await host.shutdown()


async def test_auto_cleanup_worktrees_expires_old_kept_and_respects_toggle_and_mtime(tmp_path: Path):
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        repo_dir = tmp_path / "missing-repo"
        old_ts = clock.now - 20 * DAY
        fresh_ts = clock.now - 1 * DAY

        sess = await host.ledger.create_session(name="worker", lanlan_name="Lan", project="proj")
        wt_old = host.paths.worktree_dir("auto0001")
        comm_old, _ = await host.ledger.create(
            dispatch_text="old task",
            title="old task",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            workdir=str(wt_old),
        )
        await host.ledger.transition(comm_old.id, "working", reason="start")
        await host.ledger.transition(comm_old.id, "blocked", reason="waiting for user")

        # 1. Old unlanded worktree (20 days old, untouched) -> should auto-expire.
        wt_old.mkdir(parents=True)
        (wt_old / ".git").write_text(f"gitdir: {(repo_dir / '.git' / 'worktrees' / 'auto0001').as_posix()}\n", encoding="utf-8")
        (wt_old / "file.txt").write_text("old", encoding="utf-8")
        _set_tree_mtime(wt_old, old_ts)
        await host.ledger.add_tree(
            id="auto0001",
            session_id=sess.id,
            commission_id=comm_old.id,
            lanlan_name="Lan",
            project="proj",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_old),
            workdir=str(wt_old),
            state="unlanded",
            reason="conflict",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "auto0001"))
        )

        # 2. Old kept worktree, BUT user edited a file yesterday -> MUST be spared.
        wt_edited = host.paths.worktree_dir("keep0002")
        wt_edited.mkdir(parents=True)
        (wt_edited / ".git").write_text(f"gitdir: {(repo_dir / '.git' / 'worktrees' / 'keep0002').as_posix()}\n", encoding="utf-8")
        (wt_edited / "hand_edited.txt").write_text("recent", encoding="utf-8")
        _set_tree_mtime(wt_edited, fresh_ts)
        await host.ledger.add_tree(
            id="keep0002",
            session_id=sess.id,
            commission_id=None,
            lanlan_name="Lan",
            project="proj",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_edited),
            workdir=str(wt_edited),
            state="kept",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "keep0002"))
        )

        # 3. Previously discarded worktree -> should retry even when auto_clean_worktrees is False!
        wt_retry = host.paths.worktree_dir("rtry0003")
        wt_retry.mkdir(parents=True)
        (wt_retry / ".git").write_text(f"gitdir: {(repo_dir / '.git' / 'worktrees' / 'rtry0003').as_posix()}\n", encoding="utf-8")
        await host.ledger.add_tree(
            id="rtry0003",
            session_id=sess.id,
            commission_id=None,
            lanlan_name="Lan",
            project="proj",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_retry),
            workdir=str(wt_retry),
            state="discarding",
        )

        # Disable auto_clean_worktrees first: discarding still retries, but auto0001 is left alone.
        await host.set_storage_settings(auto_clean_worktrees=False, worktree_kept_days=14)
        await host.space_keeper.tidy()
        assert not wt_retry.exists()
        assert await host.ledger.get_tree("rtry0003") is None
        assert wt_old.is_dir()

        # Re-enable auto_clean_worktrees: first tidy notes cutoff, second confirms and expires auto0001.
        await host.set_storage_settings(auto_clean_worktrees=True, worktree_kept_days=14)
        clock.now += SPACE_UPKEEP_INTERVAL_SECONDS + 60.0
        await host.space_keeper.tidy()
        assert wt_old.is_dir()  # first reading under auto_clean_worktrees=True

        clock.now += SPACE_UPKEEP_INTERVAL_SECONDS + 60.0
        await host.space_keeper.tidy()
        assert not wt_old.exists()
        assert await host.ledger.get_tree("auto0001") is None
        assert wt_edited.is_dir()
        assert await host.ledger.get_tree("keep0002") is not None

        # Blocked commission was cancelled and tree_expired event was recorded.
        ended_comm = await host.ledger.get(comm_old.id)
        assert ended_comm is not None and ended_comm.status == "cancelled"
        events = await host.ledger.events(comm_old.id)
        assert any(ev.type == "tree_expired" for ev in events)
    finally:
        await host.shutdown()


async def test_session_with_own_workdir_recorded_is_not_blocked_by_own_hold(tmp_path: Path):
    """Cleaning a session whose own ``session.workdir`` points to ``work/`` must not self-block."""
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        sess = await host.ledger.create_session(name="recorded-wd", lanlan_name="Lan", project=DEFAULT_PROJECT)
        work_dir = host.paths.work_dir(sess.id, DEFAULT_PROJECT)
        work_dir.mkdir(parents=True)
        (work_dir / "out.bin").write_bytes(b"a" * 128)
        await host.ledger.update_session(sess.id, status="idle", workdir=str(work_dir))

        res = await host.clean_work_dirs([format_work_dir_id("session", DEFAULT_PROJECT, sess.id)])
        assert res["cleaned"] == [format_work_dir_id("session", DEFAULT_PROJECT, sess.id)]
        assert res["failed"] == []
        assert not work_dir.exists()
    finally:
        await host.shutdown()


async def test_incomplete_scan_prevents_auto_deletion_and_false_empty_or_stale_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from nekopower.host import storage as storage_mod

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 40 * DAY
        sess = await host.ledger.create_session(name="huge", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(sess.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, sess.id))
        )
        work_dir = host.paths.work_dir(sess.id, DEFAULT_PROJECT)
        work_dir.mkdir(parents=True)
        (work_dir / "nested.txt").write_text("important", encoding="utf-8")
        _set_tree_mtime(work_dir, old_ts)

        repo_dir = tmp_path / "missing-repo"
        wt_dir = host.paths.worktree_dir("huge0001")
        wt_dir.mkdir(parents=True)
        (wt_dir / ".git").write_text(f"gitdir: {(repo_dir / '.git' / 'worktrees' / 'huge0001').as_posix()}\n", encoding="utf-8")
        (wt_dir / "wip.txt").write_text("wip", encoding="utf-8")
        _set_tree_mtime(wt_dir, old_ts)
        await host.ledger.add_tree(
            id="huge0001",
            session_id=sess.id,
            commission_id=None,
            lanlan_name="Lan",
            project="proj",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_dir),
            workdir=str(wt_dir),
            state="kept",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "huge0001"))
        )

        # Simulate timed-out scan returning whole=False with file_count=0 and old mtime.
        def incomplete_measure(path: Path, deadline: float, *, skip_names: frozenset[str] = frozenset()) -> storage_mod.DirStats:
            if path in (work_dir, wt_dir):
                return storage_mod.DirStats(exists=True, bytes=0, whole=False, file_count=0, newest_mtime=old_ts)
            return storage_mod.DirStats(exists=path.is_dir(), bytes=0, whole=True, file_count=0, newest_mtime=old_ts)

        monkeypatch.setattr(storage_mod, "measure_dir", incomplete_measure)

        view = await host.storage_overview()
        wd_item = next(d for d in view["work_dirs"] if d["session_id"] == sess.id)
        assert wd_item["empty"] is False
        assert wd_item["stale"] is False
        wt_item = next(t for t in view["worktrees"] if t["id"] == "huge0001")
        assert wt_item["stale"] is False

        # Two confirmed tidy rounds must NOT delete either directory while scans are incomplete.
        await host.space_keeper.tidy()
        clock.now += SPACE_UPKEEP_INTERVAL_SECONDS + 60.0
        await host.space_keeper.tidy()
        assert work_dir.is_dir()
        assert wt_dir.is_dir()
    finally:
        await host.shutdown()


async def test_auto_cleanup_spares_nested_project_workdir_and_recent_closed_fork(tmp_path: Path):
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        await host.set_storage_settings(auto_clean_work_dirs=True, work_dir_kept_days=7)
        old_ts = clock.now - 30 * DAY
        recent_ts = clock.now - 2 * DAY

        # 1. Active project whose workdir is a subdirectory inside an expired session's work/.
        nested_s = await host.ledger.create_session(name="parent-s", lanlan_name="Lan", project="demo")
        await host.ledger.update_session(nested_s.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, nested_s.id))
        )
        parent_work = host.paths.work_dir(nested_s.id, "demo")
        nested_app = parent_work / "subapp"
        nested_app.mkdir(parents=True)
        (nested_app / "main.py").write_text("print(1)", encoding="utf-8")
        _set_tree_mtime(parent_work, old_ts)
        await host.space.touch_project("demo", workdir=str(nested_app))

        # 2. Old source session with a closed, uncleared fork active 2 days ago.
        src = await host.ledger.create_session(name="old-src", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(src.id, status="closed")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, src.id))
        )
        src_work = host.paths.work_dir(src.id, DEFAULT_PROJECT)
        src_work.mkdir(parents=True)
        (src_work / "shared.txt").write_text("shared", encoding="utf-8")
        _set_tree_mtime(src_work, old_ts)

        fork = await host.ledger.create_session(
            name="recent-fork", lanlan_name="Lan", project=DEFAULT_PROJECT, kind="fork", forked_from=src.id, workdir=str(src_work)
        )
        await host.ledger.update_session(fork.id, status="closed")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (recent_ts, fork.id))
        )

        view = await host.storage_overview()
        by_sid = {d["session_id"]: d for d in view["work_dirs"]}
        assert by_sid[nested_s.id]["referenced_by_project"] is True
        assert by_sid[nested_s.id]["stale"] is False
        assert by_sid[src.id]["stale"] is False
        assert by_sid[src.id]["last_active_at"] == pytest.approx(recent_ts)

        # Two confirmed tidy rounds must spare both directories.
        await host.space_keeper.tidy()
        clock.now += SPACE_UPKEEP_INTERVAL_SECONDS + 60.0
        await host.space_keeper.tidy()
        assert nested_app.is_dir()
        assert src_work.is_dir()
    finally:
        await host.shutdown()


async def test_overview_prioritizes_cleanable_items_before_protected_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("config.nekopower_settings.STORAGE_MAX_ITEMS", 2)
    host = make_host(tmp_path)
    try:
        # Two huge running (in-use) sessions and one small idle (cleanable) session.
        for idx in range(2):
            busy = await host.ledger.create_session(name=f"busy-{idx}", lanlan_name="Lan", project=DEFAULT_PROJECT)
            await host.ledger.update_session(busy.id, status="running")
            bw = host.paths.work_dir(busy.id, DEFAULT_PROJECT)
            bw.mkdir(parents=True)
            (bw / "big.bin").write_bytes(b"b" * 10_000)

        idle = await host.ledger.create_session(name="small-idle", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(idle.id, status="idle")
        iw = host.paths.work_dir(idle.id, DEFAULT_PROJECT)
        iw.mkdir(parents=True)
        (iw / "tiny.bin").write_bytes(b"t" * 10)

        view = await host.storage_overview()
        assert len(view["work_dirs"]) == 2
        assert view["work_dirs_more"] == 1
        assert view["work_dirs"][0]["session_id"] == idle.id
        assert view["work_dirs"][0]["can_clean"] is True
    finally:
        await host.shutdown()


def _make_dir_link(link_path: Path, target: Path) -> bool:
    if sys.platform == "win32":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0
    os.symlink(target, link_path)
    return True


async def test_symlinked_managed_root_is_rejected_and_missing_dir_reports_missing(tmp_path: Path):
    """A symlink/junction at ``task_agents`` must be rejected, while a missing ``work/`` raises FileNotFoundError."""
    import asyncio

    host = make_host(tmp_path)
    try:
        # Missing work dir under a real task_agents root raises FileNotFoundError (404 place_missing).
        (host.paths.root() / "task_agents").mkdir(parents=True, exist_ok=True)
        with pytest.raises(FileNotFoundError, match="place_missing"):
            await host.resolve_storage_open_path("work_dir", format_work_dir_id("session", DEFAULT_PROJECT, "gone-1"))

        # Replace task_agents with a junction (Windows) or symlink (POSIX) to an outside directory.
        ta_root = host.paths.root() / "task_agents"
        ta_root.rmdir()
        outside = tmp_path / "outside_ta"
        victim = outside / "projects" / DEFAULT_PROJECT / "sessions" / "s1" / "work"
        victim.mkdir(parents=True)
        secret = victim / "keep.txt"
        secret.write_text("must survive", encoding="utf-8")

        assert await asyncio.to_thread(_make_dir_link, ta_root, outside)

        wid = format_work_dir_id("session", DEFAULT_PROJECT, "s1")
        with pytest.raises(ValueError, match="bad_work_dir"):
            await host.resolve_storage_open_path("work_dir", wid)

        res = await host.clean_work_dirs([wid])
        assert res["cleaned"] == []
        assert res["failed"] == [{"id": wid, "code": "unsafe_path"}]
        assert secret.read_text(encoding="utf-8") == "must survive"
    finally:
        await host.shutdown()


async def test_project_referenced_work_dir_and_unreadable_projects_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Manual cleanup must protect project-referenced dirs and fail closed when ``list_projects`` fails."""
    host = make_host(tmp_path)
    try:
        sess = await host.ledger.create_session(name="ref-s", lanlan_name="Lan", project="demo")
        await host.ledger.update_session(sess.id, status="idle")
        work_dir = host.paths.work_dir(sess.id, "demo")
        work_dir.mkdir(parents=True)
        (work_dir / "code.py").write_bytes(b"p" * 200)
        await host.space.touch_project("demo", workdir=str(work_dir))

        # Overview marks project-referenced session work_dir as not cleanable, and manual clean refuses it.
        view = await host.storage_overview()
        wid = format_work_dir_id("session", "demo", sess.id)
        item = next(d for d in view["work_dirs"] if d["id"] == wid)
        assert item["referenced_by_project"] is True
        assert item["can_clean"] is False

        refused = await host.clean_work_dirs([wid])
        assert refused["cleaned"] == []
        assert refused["failed"] == [{"id": wid, "code": "in_use"}]
        assert work_dir.is_dir()

        # When list_projects raises OSError, even an unreferenced idle work_dir fails closed.
        other = await host.ledger.create_session(name="other-s", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(other.id, status="idle")
        other_work = host.paths.work_dir(other.id, DEFAULT_PROJECT)
        other_work.mkdir(parents=True)
        (other_work / "tmp.txt").write_bytes(b"x" * 50)

        async def broken_list_projects(*_args: Any, **_kwargs: Any) -> tuple[list[Any], bool]:
            raise OSError("disk temporarily busy")

        monkeypatch.setattr(host.space, "list_projects_checked", broken_list_projects)
        other_id = format_work_dir_id("session", DEFAULT_PROJECT, other.id)
        blocked = await host.clean_work_dirs([other_id])
        assert blocked["cleaned"] == []
        assert blocked["failed"] == [{"id": other_id, "code": "in_use"}]
        assert other_work.is_dir()
    finally:
        await host.shutdown()


async def test_retry_discarded_rejects_in_tree_of_and_overview_counts_extra_session_files(tmp_path: Path):
    """``retry_discarded_tree`` rejects trees still in ``_tree_of``, and overview counts extra session files."""
    from nekopower.host.paths import DSH_SESSION_NAME

    host = make_host(tmp_path)
    try:
        sess = await host.ledger.create_session(name="dsh-s", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(sess.id, status="idle")
        sdir = host.paths.session_dir(sess.id, DEFAULT_PROJECT)
        sdir.mkdir(parents=True)
        (sdir / DSH_SESSION_NAME).write_bytes(b"d" * 333)

        view = await host.storage_overview()
        cats = {c["key"]: c for c in view["categories"]}
        ta_place = next(p for p in view["places"] if p["key"] == "task_agents")
        assert ta_place["bytes"] == 333
        assert cats["other"]["bytes"] >= 333

        # Add a discarding worktree still referenced in _tree_of -> retry_discarded_tree must refuse it.
        repo_dir = tmp_path / "gone-repo"
        wt_dir = host.paths.worktree_dir("busy0001")
        wt_dir.mkdir(parents=True)
        (wt_dir / ".git").write_text(
            f"gitdir: {(repo_dir / '.git' / 'worktrees' / 'busy0001').as_posix()}\n", encoding="utf-8"
        )
        await host.ledger.add_tree(
            id="busy0001",
            session_id=sess.id,
            commission_id=None,
            lanlan_name="Lan",
            project="proj",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_dir),
            workdir=str(wt_dir),
            state="discarding",
        )
        host.task_agents._tree_of["comm-active"] = "busy0001"
        try:
            with pytest.raises(ValueError, match="at_work"):
                await host.task_agents.retry_discarded_tree("busy0001")
            assert await host.task_agents.retry_discarded_trees() == 0
            assert wt_dir.is_dir()
        finally:
            host.task_agents._tree_of.pop("comm-active", None)
    finally:
        await host.shutdown()


async def test_expired_session_cleanup_survives_estimation_error_and_partial_freed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A single session estimation error must not abort ``clean_expired_sessions_now``, and partial outputs freed bytes are counted."""
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 120 * DAY
        s1 = await host.ledger.create_session(name="good-old", lanlan_name="Lan", project=DEFAULT_PROJECT)
        s2 = await host.ledger.create_session(name="err-old", lanlan_name="Lan", project=DEFAULT_PROJECT)
        for s in (s1, s2):
            await host.ledger.update_session(s.id, status="idle")
            await host.ledger.run(
                lambda conn, sid=s.id: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, sid))
            )
            sdir = host.paths.session_dir(s.id, DEFAULT_PROJECT)
            sdir.mkdir(parents=True)
            (sdir / TRANSCRIPT_NAME).write_bytes(b"t" * 200)

        real_record_bytes = host.space_keeper._record_bytes
        s2_dir = host.paths.session_dir(s2.id, DEFAULT_PROJECT)

        def flaky_record_bytes(directory: Path, measure_fn: Any) -> int:
            if directory == s2_dir:
                raise PermissionError("transient stat error")
            return real_record_bytes(directory, measure_fn)

        monkeypatch.setattr(host.space_keeper, "_record_bytes", flaky_record_bytes)
        res = await host.clean_transcripts("expired")
        assert res["cleaned"] == 2
        assert res["freed_bytes"] == 200  # s1's bytes preserved even when s2 estimation failed

        # Partial removal in clean_inactive_outputs_now still adds freed bytes.
        s3 = await host.ledger.create_session(name="partial-out", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(s3.id, status="idle")
        out_dir = host.paths.session_dir(s3.id, DEFAULT_PROJECT) / "outputs"
        out_dir.mkdir(parents=True)
        (out_dir / "big.txt").write_bytes(b"o" * 400)

        from nekopower.host import storage as storage_mod

        monkeypatch.setattr(storage_mod, "remove_dir_safely", lambda target, root: (False, 350))
        out_res = await host.clean_transcripts("outputs")
        assert out_res["cleaned"] == 0
        assert out_res["skipped"] == 1
        assert out_res["freed_bytes"] == 350
    finally:
        await host.shutdown()


async def test_unreadable_worktree_is_not_marked_stale_and_project_cleanup_scans_all_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An unreadable worktree (exists=False, whole=False) must not be marked stale; project cleanup scans all sessions."""
    from nekopower.host import storage as storage_mod

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        # 1. Tracked worktree whose lstat fails (exists=False, whole=False) must stay stale=False.
        repo_dir = tmp_path / "repo"
        wt_dir = host.paths.worktree_dir("unrd0001")
        wt_dir.mkdir(parents=True)
        sess = await host.ledger.create_session(name="w", lanlan_name="Lan", project="proj")
        await host.ledger.add_tree(
            id="unrd0001",
            session_id=sess.id,
            commission_id=None,
            lanlan_name="Lan",
            project="proj",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_dir),
            workdir=str(wt_dir),
            state="kept",
        )
        real_measure = storage_mod.measure_dir

        def unreadable_wt(path: Path, deadline: float, *, skip_names: frozenset[str] = frozenset()) -> storage_mod.DirStats:
            if path == wt_dir:
                return storage_mod.DirStats(exists=False, bytes=0, whole=False, file_count=0, newest_mtime=None)
            return real_measure(path, deadline, skip_names=skip_names)

        monkeypatch.setattr(storage_mod, "measure_dir", unreadable_wt)
        view = await host.storage_overview()
        wt_item = next(t for t in view["worktrees"] if t["id"] == "unrd0001")
        assert wt_item["stale"] is False
        assert wt_item["bytes_whole"] is False

        # 2. Project-scope work/ cleanup queries all sessions directly without calling find_sessions(limit=...).
        proj_work = host.paths.project_work_dir("archived-proj")
        proj_work.mkdir(parents=True)
        (proj_work / "data.bin").write_bytes(b"d" * 64)
        old_sess = await host.ledger.create_session(
            name="ancient", lanlan_name="Lan", project=DEFAULT_PROJECT, workdir=str(proj_work)
        )
        await host.ledger.update_session(old_sess.id, status="idle")
        # Simulate old_sess falling outside any capped find_sessions page: project cleanup must still find & hold it.
        async def empty_find_sessions(*_args: Any, **_kwargs: Any) -> list[Any]:
            return []

        monkeypatch.setattr(host.ledger, "find_sessions", empty_find_sessions)
        assert host.task_agents.hold_files(old_sess.id) is True
        try:
            refused = await host.clean_work_dirs([format_work_dir_id("project", "archived-proj")])
            assert refused["cleaned"] == []
            assert refused["failed"] == [{"id": format_work_dir_id("project", "archived-proj"), "code": "in_use"}]
            assert proj_work.is_dir()
        finally:
            host.task_agents.release_files(old_sess.id)

        cleaned = await host.clean_work_dirs([format_work_dir_id("project", "archived-proj")])
        assert cleaned["cleaned"] == [format_work_dir_id("project", "archived-proj")]
        assert cleaned["freed_bytes"] == 64
        assert not proj_work.exists()
    finally:
        await host.shutdown()


async def test_session_cleanup_holds_sharing_sessions_and_rejects_unreadable_project_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Session work/ deletion must hold sharing forks during deletion and fail closed on corrupt project.json."""
    from nekopower.host import storage as storage_mod

    host = make_host(tmp_path)
    try:
        # 1. Corrupt project.json makes list_projects() return a stub with workdir=None, but
        # read_project() reports readable=False -> must fail closed for both overview and cleanup.
        sess = await host.ledger.create_session(name="shared-src", lanlan_name="Lan", project="alpha")
        await host.ledger.update_session(sess.id, status="idle")
        work_dir = host.paths.work_dir(sess.id, "alpha")
        work_dir.mkdir(parents=True)
        (work_dir / "file.txt").write_text("precious", encoding="utf-8")
        await host.space.touch_project("alpha", workdir=str(work_dir))
        proj_json = host.paths.space().project_file("alpha")
        proj_json.write_text("{ corrupt json", encoding="utf-8")

        wid = format_work_dir_id("session", "alpha", sess.id)
        view = await host.storage_overview()
        item = next(d for d in view["work_dirs"] if d["id"] == wid)
        assert item["can_clean"] is False

        refused = await host.clean_work_dirs([wid])
        assert refused["cleaned"] == []
        assert refused["failed"] == [{"id": wid, "code": "in_use"}]
        assert work_dir.is_dir()

        # Repair project.json with a different workdir so sess's work_dir is no longer referenced.
        elsewhere = tmp_path / "other_project_dir"
        elsewhere.mkdir()
        proj_json.unlink()
        await host.space.touch_project("alpha", workdir=str(elsewhere))

        # 2. Idle fork sharing the same work_dir must ALSO be held in _clearing while remove_dir_safely runs.
        fork = await host.ledger.create_session(
            name="shared-fork",
            lanlan_name="Lan",
            project="alpha",
            kind="fork",
            forked_from=sess.id,
            workdir=str(work_dir),
        )
        await host.ledger.update_session(fork.id, status="idle")
        observed_holds: dict[str, bool] = {}
        real_remove = storage_mod.remove_dir_safely

        def spy_remove(target: Path, allowed_root: Path, **kwargs: Any) -> tuple[bool, int]:
            observed_holds["src"] = sess.id in host.task_agents._clearing
            observed_holds["fork"] = fork.id in host.task_agents._clearing
            return real_remove(target, allowed_root, **kwargs)

        monkeypatch.setattr(storage_mod, "remove_dir_safely", spy_remove)
        cleaned = await host.clean_work_dirs([wid])
        assert cleaned["cleaned"] == [wid]
        assert observed_holds == {"src": True, "fork": True}
        assert sess.id not in host.task_agents._clearing
        assert fork.id not in host.task_agents._clearing
    finally:
        await host.shutdown()


async def test_orphan_worktree_rejects_linked_wt_root_before_git_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A symlink/junction at ``wt/`` must be rejected before ``worktrees.open`` / ``remove`` is ever called."""
    import asyncio

    host = make_host(tmp_path)
    try:
        outside_wt = tmp_path / "outside_wt"
        victim = outside_wt / "orph0001"
        victim.mkdir(parents=True)
        fake_repo = tmp_path / "real_repo"
        fake_repo.mkdir()
        (victim / ".git").write_text(f"gitdir: {(fake_repo / '.git' / 'worktrees' / 'orph0001').as_posix()}\n", encoding="utf-8")
        secret = victim / "keep.txt"
        secret.write_text("untouched", encoding="utf-8")

        wt_root = host.paths.worktrees_dir()
        wt_root.parent.mkdir(parents=True, exist_ok=True)
        assert await asyncio.to_thread(_make_dir_link, wt_root, outside_wt)

        async def fail_open(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("worktrees.open must not run when wt/ is a link")

        monkeypatch.setattr(host.task_agents.worktrees, "open", fail_open)
        with pytest.raises(ValueError, match="unsafe_path"):
            await host.task_agents.remove_orphan_worktree("orph0001")
        assert secret.read_text(encoding="utf-8") == "untouched"
    finally:
        await host.shutdown()


async def test_worktree_forget_aborts_when_before_delete_detects_new_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``WorktreeManager._forget`` must re-check ``before_delete`` right before ``_remove_tree``."""
    from nekopower.host.worktrees import WorktreeManager

    mgr = WorktreeManager()
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt" / "t1"
    wt.mkdir(parents=True)
    saved = wt / "edited.txt"
    saved.write_text("new save", encoding="utf-8")

    async def fake_registered(_repo: Path) -> list[Path]:
        return [wt]

    class _FakeRun:
        def lines(self) -> list[str]:
            return [str(repo / ".git")]

    async def fake_git(_repo: Path, *_args: str, **_kwargs: Any) -> _FakeRun:
        return _FakeRun()

    monkeypatch.setattr(mgr, "_registered", fake_registered)
    monkeypatch.setattr(mgr, "_git", fake_git)
    monkeypatch.setattr("nekopower.host.worktrees._whose", lambda _p, _c: "ours")

    aborted = await mgr._forget(
        repo, wt, "t1", remove_directory=False, before_delete=lambda _path: False
    )
    assert aborted == "aborted"
    assert saved.read_text(encoding="utf-8") == "new save"

    # When _whose returns "theirs", _forget drops registration/refs, keeps directory, and returns "record_only".
    monkeypatch.setattr("nekopower.host.worktrees._whose", lambda _p, _c: "theirs")
    monkeypatch.setattr("nekopower.host.worktrees._drop_registration", lambda _c, _p: None)
    record_only = await mgr._forget(repo, wt, "t1", remove_directory=False)
    assert record_only == "record_only"
    assert saved.read_text(encoding="utf-8") == "new save"

    # Once _remove_tree deleted the directory, a later _delete_refs GitError raises WorktreeRemovalIncomplete.
    from nekopower.host.worktrees import GitError, WorktreeRemovalIncomplete

    monkeypatch.setattr("nekopower.host.worktrees._whose", lambda _p, _c: "ours")

    async def fail_delete_refs(_repo: Path, _wt_id: str) -> None:
        raise GitError(("update-ref", "--stdin"), 128, b"fatal: cannot lock ref")

    monkeypatch.setattr(mgr, "_delete_refs", fail_delete_refs)
    with pytest.raises(WorktreeRemovalIncomplete):
        await mgr._forget(repo, wt, "t1", remove_directory=False)
    assert not wt.exists()


async def test_work_dir_cleanup_preserves_retained_worktree_repo_and_idle_compaction_timers(tmp_path: Path):
    """Retained worktree backing repos must not be deleted, and work/ checks must preserve idle compaction timers."""
    import asyncio

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        # 1. Idle session whose work/ holds the Git repository backing a retained 'kept' worktree.
        old_ts = clock.now - 10 * DAY
        repo_sess = await host.ledger.create_session(name="repo-holder", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(repo_sess.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, repo_sess.id))
        )
        repo_work = host.paths.work_dir(repo_sess.id, DEFAULT_PROJECT)
        nested_repo = repo_work / "myrepo"
        nested_repo.mkdir(parents=True)
        (nested_repo / "README.md").write_text("repo data", encoding="utf-8")
        _set_tree_mtime(repo_work, old_ts)

        wt_dir = host.paths.worktree_dir("kept9001")
        wt_dir.mkdir(parents=True)
        await host.ledger.add_tree(
            id="kept9001",
            session_id=repo_sess.id,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(nested_repo),
            repo_root=str(nested_repo),
            path=str(wt_dir),
            workdir=str(wt_dir),
            state="kept",
        )

        # Also check project-level work/ backing a retained worktree.
        proj_work = host.paths.project_work_dir("repo-proj")
        proj_work.mkdir(parents=True)
        (proj_work / "file.txt").write_text("proj repo", encoding="utf-8")
        wt_proj = host.paths.worktree_dir("kept9002")
        wt_proj.mkdir(parents=True)
        await host.ledger.add_tree(
            id="kept9002",
            session_id=repo_sess.id,
            commission_id=None,
            lanlan_name="Lan",
            project="repo-proj",
            project_dir=str(proj_work),
            repo_root=str(proj_work),
            path=str(wt_proj),
            workdir=str(wt_proj),
            state="kept",
        )

        sess_wid = format_work_dir_id("session", DEFAULT_PROJECT, repo_sess.id)
        proj_wid = format_work_dir_id("project", "repo-proj")
        view = await host.storage_overview()
        by_id = {d["id"]: d for d in view["work_dirs"]}
        assert by_id[sess_wid]["can_clean"] is False
        assert by_id[proj_wid]["can_clean"] is False

        res = await host.clean_work_dirs([sess_wid, proj_wid])
        assert res["cleaned"] == []
        assert {item["id"]: item["code"] for item in res["failed"]} == {sess_wid: "in_use", proj_wid: "in_use"}
        assert nested_repo.is_dir()
        assert proj_work.is_dir()

        # 2. Recent closed session with an active idle-compaction timer: upkeep check must NOT cancel the timer.
        recent_ts = clock.now - 3600.0
        recent_sess = await host.ledger.create_session(name="recent-compact", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(recent_sess.id, status="closed")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (recent_ts, recent_sess.id))
        )
        recent_work = host.paths.work_dir(recent_sess.id, DEFAULT_PROJECT)
        recent_work.mkdir(parents=True)
        (recent_work / "notes.txt").write_text("fresh", encoding="utf-8")
        _set_tree_mtime(recent_work, recent_ts)

        loop = asyncio.get_running_loop()
        timer = loop.call_later(3600.0, lambda: None)
        host.task_agents._idle_timers[recent_sess.id] = timer
        # Also attach a timer on a session with outputs/ and verify clean_transcripts("outputs") keeps both timers.
        out_sess = await host.ledger.create_session(name="out-compact", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(out_sess.id, status="closed")
        out_dir = host.paths.session_dir(out_sess.id, DEFAULT_PROJECT) / "outputs"
        out_dir.mkdir(parents=True)
        (out_dir / "spill.txt").write_bytes(b"s" * 128)
        timer_out = loop.call_later(3600.0, lambda: None)
        host.task_agents._idle_timers[out_sess.id] = timer_out
        try:
            outcome, _freed = await host.storage._clean_one_work_dir(
                "session", DEFAULT_PROJECT, recent_sess.id, until=clock.now - 7 * DAY, skip_project_referenced=True
            )
            assert outcome == "recent"
            assert host.task_agents._idle_timers.get(recent_sess.id) is timer
            assert not timer.cancelled()

            out_res = await host.clean_transcripts("outputs")
            assert out_res["cleaned"] == 1
            assert not out_dir.exists()
            assert host.task_agents._idle_timers.get(recent_sess.id) is timer and not timer.cancelled()
            assert host.task_agents._idle_timers.get(out_sess.id) is timer_out and not timer_out.cancelled()
        finally:
            timer.cancel()
            timer_out.cancel()
            host.task_agents._idle_timers.pop(recent_sess.id, None)
            host.task_agents._idle_timers.pop(out_sess.id, None)
    finally:
        await host.shutdown()


async def test_expire_tree_does_not_defer_pre_deletion_git_errors_and_uses_auto_clean_scan_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Pre-deletion git errors must leave tree state & commission intact; auto-clean uses STORAGE_AUTO_CLEAN_SCAN_SECONDS."""
    from nekopower.host import storage as storage_mod
    from nekopower.host.worktrees import GitError, Worktree

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 30 * DAY
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        wt_dir = host.paths.worktree_dir("giterr01")
        wt_dir.mkdir(parents=True)
        (wt_dir / "change.txt").write_text("work", encoding="utf-8")
        _set_tree_mtime(wt_dir, old_ts)

        sess = await host.ledger.create_session(name="worker", lanlan_name="Lan", project="proj")
        comm, _ = await host.ledger.create(
            dispatch_text="fix",
            title="fix",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            workdir=str(wt_dir),
        )
        await host.ledger.transition(comm.id, "working", reason="start")
        await host.ledger.transition(comm.id, "blocked", reason="waiting")
        await host.ledger.add_tree(
            id="giterr01",
            session_id=sess.id,
            commission_id=comm.id,
            lanlan_name="Lan",
            project="proj",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(wt_dir),
            workdir=str(wt_dir),
            state="held",
            reason="ask",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "giterr01"))
        )

        fake_handle = Worktree(id="giterr01", repo_root=repo_dir, path=wt_dir, subpath="", start="abc123")

        async def fake_handle_of(_row: Any) -> Worktree:
            return fake_handle

        async def fail_remove_if_untouched(_handle: Any, _check: Any) -> bool:
            raise GitError(("rev-parse", "--git-common-dir"), 128, b"fatal: index.lock exists")

        monkeypatch.setattr(host.task_agents, "_handle_of", fake_handle_of)
        monkeypatch.setattr(host.task_agents.worktrees, "remove_if_untouched", fail_remove_if_untouched)

        res = await host.task_agents.expire_tree(
            "giterr01",
            until=clock.now - 14 * DAY,
            check_mtime=lambda path: storage_mod.newest_mtime_of(path, 30.0, skip_git=True),
        )
        assert res == {"expired": False, "reason": "not_removable"}
        row_after = await host.ledger.get_tree("giterr01")
        assert row_after is not None and row_after.state == "held"
        comm_after = await host.ledger.get(comm.id)
        assert comm_after is not None and comm_after.status == "blocked"

        # Verify background upkeep uses STORAGE_AUTO_CLEAN_SCAN_SECONDS rather than the 0.25s UI item budget.
        seen_budgets: list[float] = []
        real_newest = storage_mod.newest_mtime_of

        def spy_newest(path: Path, budget_seconds: float = 0.25, *, skip_git: bool = False) -> tuple[float | None, bool]:
            seen_budgets.append(budget_seconds)
            if budget_seconds < 1.0:
                return old_ts, False
            return real_newest(path, budget_seconds, skip_git=skip_git)

        monkeypatch.setattr(storage_mod, "newest_mtime_of", spy_newest)
        big_sess = await host.ledger.create_session(name="big-work", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(big_sess.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, big_sess.id))
        )
        big_work = host.paths.work_dir(big_sess.id, DEFAULT_PROJECT)
        big_work.mkdir(parents=True)
        (big_work / "bundle.bin").write_bytes(b"b" * 100)
        _set_tree_mtime(big_work, old_ts)

        monkeypatch.setattr("config.nekopower_settings.STORAGE_AUTO_CLEAN_SCAN_SECONDS", 0.25)
        out_short, _ = await host.storage._clean_one_work_dir(
            "session", DEFAULT_PROJECT, big_sess.id, until=clock.now - 7 * DAY, skip_project_referenced=True
        )
        assert out_short == "scan_incomplete"
        assert big_work.is_dir()

        monkeypatch.setattr("config.nekopower_settings.STORAGE_AUTO_CLEAN_SCAN_SECONDS", 30.0)
        out_full, freed = await host.storage._clean_one_work_dir(
            "session", DEFAULT_PROJECT, big_sess.id, until=clock.now - 7 * DAY, skip_project_referenced=True
        )
        assert out_full == "cleaned"
        assert freed == 100
        assert not big_work.exists()
        assert 30.0 in seen_budgets
    finally:
        await host.shutdown()


async def test_round5_project_enum_error_grandchild_fork_and_unclassified_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify project-enumeration fail-closed, grandchild fork protection, and non-dir 'sessions' accounting."""
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 30 * DAY
        recent_ts = clock.now - 1 * DAY

        # 1. Project directory enumeration PermissionError must fail closed (never treat as zero references).
        sess_proj = await host.ledger.create_session(name="p-owner", lanlan_name="Lan", project="alpha")
        await host.ledger.update_session(sess_proj.id, status="idle")
        work_p = host.paths.work_dir(sess_proj.id, "alpha")
        work_p.mkdir(parents=True)
        (work_p / "data.txt").write_text("keep", encoding="utf-8")
        await host.space.touch_project("alpha", workdir=str(work_p))

        projects_dir = host.paths.space().projects_dir()
        real_iterdir = Path.iterdir

        def deny_projects_iterdir(self: Path):
            if self == projects_dir:
                raise PermissionError("access denied")
            return real_iterdir(self)

        wid_p = format_work_dir_id("session", "alpha", sess_proj.id)
        with monkeypatch.context() as mp:
            mp.setattr(Path, "iterdir", deny_projects_iterdir)
            view_err = await host.storage_overview()
            item_err = next(d for d in view_err["work_dirs"] if d["id"] == wid_p)
            assert item_err["can_clean"] is False
            res_err = await host.clean_work_dirs([wid_p])
            assert res_err["cleaned"] == []
            assert res_err["failed"] == [{"id": wid_p, "code": "in_use"}]
            assert work_p.is_dir()

        # 2. Grandchild fork (A -> B -> C) recently active must protect A's work/ during auto-cleanup.
        sess_a = await host.ledger.create_session(name="root-a", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(sess_a.id, status="idle")
        work_a = host.paths.work_dir(sess_a.id, DEFAULT_PROJECT)
        work_a.mkdir(parents=True)
        (work_a / "shared.txt").write_text("grandchild uses this", encoding="utf-8")
        _set_tree_mtime(work_a, old_ts)
        sess_b = await host.ledger.create_session(
            name="child-b", lanlan_name="Lan", project=DEFAULT_PROJECT, kind="fork", forked_from=sess_a.id, workdir=str(work_a)
        )
        await host.ledger.update_session(sess_b.id, status="idle")
        sess_c = await host.ledger.create_session(
            name="grandchild-c", lanlan_name="Lan", project=DEFAULT_PROJECT, kind="fork", forked_from=sess_b.id, workdir=str(work_a)
        )
        await host.ledger.update_session(sess_c.id, status="idle")
        await host.ledger.run(
            lambda conn: (
                conn.execute("UPDATE sessions SET last_active_at = ? WHERE id IN (?, ?)", (old_ts, sess_a.id, sess_b.id)),
                conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (recent_ts, sess_c.id)),
            )
        )
        out_gc, _ = await host.storage._clean_one_work_dir(
            "session", DEFAULT_PROJECT, sess_a.id, until=clock.now - 7 * DAY, skip_project_referenced=True
        )
        assert out_gc == "recent"
        assert work_a.is_dir()

        # 3. Regular file named "sessions" under task_agents/projects/<proj>/ must be counted in "other".
        odd_proj = host.paths.root() / "task_agents" / "projects" / "fileproj"
        odd_proj.mkdir(parents=True)
        before_view = await host.storage_overview()
        before_other = next(c for c in before_view["categories"] if c["key"] == "other")["bytes"]
        (odd_proj / "sessions").write_bytes(b"z" * 333)
        view_other = await host.storage_overview()
        other_cat = next(c for c in view_other["categories"] if c["key"] == "other")
        assert other_cat["bytes"] - before_other == 333

        # 4. Ledger sessions_by_forked_from index prevents full table SCAN on forks_of queries.
        plan = await host.ledger.run(
            lambda conn: [
                str(row[3])
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM sessions WHERE forked_from = ? ORDER BY created_at, rowid",
                    (sess_a.id,),
                ).fetchall()
            ]
        )
        assert any("sessions_by_forked_from" in step for step in plan), plan
    finally:
        await host.shutdown()


async def test_round5_mid_batch_policy_and_recheck_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify mid-batch retention change, mid-scan edit, and post-scan policy disable abort work_dir/worktree reaping."""
    from nekopower.host import storage as storage_mod
    from nekopower.host.worktrees import Worktree

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 30 * DAY
        recent_ts = clock.now - 1 * DAY

        # 1. Mid-batch retention change stops reaping remaining work dirs under stale cutoff.
        s1 = await host.ledger.create_session(name="mid1", lanlan_name="Lan", project=DEFAULT_PROJECT)
        s2 = await host.ledger.create_session(name="mid2", lanlan_name="Lan", project=DEFAULT_PROJECT)
        for sid in (s1.id, s2.id):
            await host.ledger.update_session(sid, status="idle")
            await host.ledger.run(lambda conn, s=sid: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (clock.now - 10 * DAY, s)))
            wdir = host.paths.work_dir(sid, DEFAULT_PROJECT)
            wdir.mkdir(parents=True)
            (wdir / "f.bin").write_bytes(b"x" * 16)
            _set_tree_mtime(wdir, clock.now - 10 * DAY)
        await host.set_storage_settings(auto_clean_work_dirs=True, work_dir_kept_days=7)
        cleaned_mid: list[str] = []
        real_clean_one = host.storage._clean_one_work_dir

        async def bump_retention_after_first(*args: Any, **kwargs: Any) -> tuple[str, int]:
            res = await real_clean_one(*args, **kwargs)
            if res[0] == "cleaned":
                cleaned_mid.append(str(args[2]))
                await host.set_storage_settings(work_dir_kept_days=45)
            return res

        state = {
            "work_dirs_due_until": clock.now - 7 * DAY,
            "work_dirs_due_seen_at": clock.now - 2 * DAY,
        }
        with monkeypatch.context() as mp:
            mp.setattr(host.storage, "_clean_one_work_dir", bump_retention_after_first)
            await host.storage.reap_work_dirs(clock.now, state)
        assert len(cleaned_mid) == 1
        remaining_exists = sum(
            1 for sid in (s1.id, s2.id) if host.paths.work_dir(sid, DEFAULT_PROJECT).exists()
        )
        assert remaining_exists == 1

        # 2. Mid-scan file edit aborts work_dir deletion.
        edit_sess = await host.ledger.create_session(name="mid-edit", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(edit_sess.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, edit_sess.id))
        )
        edit_work = host.paths.work_dir(edit_sess.id, DEFAULT_PROJECT)
        edit_work.mkdir(parents=True)
        edit_file = edit_work / "draft.txt"
        edit_file.write_text("v1", encoding="utf-8")
        _set_tree_mtime(edit_work, old_ts)

        real_newest = storage_mod.newest_mtime_of
        scan_calls = {"n": 0}

        def touch_after_first_scan(path: Path, budget: float = 0.25, *, skip_git: bool = False) -> tuple[float | None, bool]:
            res = real_newest(path, budget, skip_git=skip_git)
            if path == edit_work and scan_calls["n"] == 0:
                scan_calls["n"] += 1
                edit_file.write_text("v2 saved during scan", encoding="utf-8")
                _set_tree_mtime(edit_work, recent_ts)
            return res

        with monkeypatch.context() as mp:
            mp.setattr(storage_mod, "newest_mtime_of", touch_after_first_scan)
            out_edit, _ = await host.storage._clean_one_work_dir(
                "session", DEFAULT_PROJECT, edit_sess.id, until=clock.now - 7 * DAY, skip_project_referenced=True
            )
        assert out_edit == "recent"
        assert edit_file.read_text(encoding="utf-8") == "v2 saved during scan"

        # 3. Mid-scan policy disable via reap_worktrees with dedicated budg0042 handle and custom 42.0s budget.
        fake_repo = tmp_path / "budg_repo"
        fake_repo.mkdir()
        wt_budget = host.paths.worktree_dir("budg0042")
        wt_budget.mkdir(parents=True)
        (wt_budget / "f.txt").write_text("old", encoding="utf-8")
        _set_tree_mtime(wt_budget, old_ts)
        await host.ledger.add_tree(
            id="budg0042",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(fake_repo),
            repo_root=str(fake_repo),
            path=str(wt_budget),
            workdir=str(wt_budget),
            state="kept",
        )
        await host.ledger.run(lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "budg0042")))
        await host.set_storage_settings(auto_clean_worktrees=True, worktree_kept_days=14)
        wt_calls: list[tuple[float, bool]] = []
        budg_handle = Worktree(id="budg0042", repo_root=fake_repo, path=wt_budget, subpath="", start="abc")

        async def fake_handle_budg(_row: Any) -> Worktree:
            return budg_handle

        async def fake_remove_if_untouched(handle: Worktree, is_untouched: Any = None) -> str:
            assert handle.id == "budg0042"
            if is_untouched is not None and not is_untouched(handle.path):
                return "aborted"
            return "removed"

        def spy_wt_newest(path: Path, budget: float = 0.25, *, skip_git: bool = False) -> tuple[float | None, bool]:
            if path == wt_budget:
                wt_calls.append((budget, skip_git))
                # Flip cached policy off during scan to verify pre-delete policy guard blocks removal.
                cur_wt = host.storage.settings._kept
                if cur_wt is not None:
                    host.storage.settings._kept = (cur_wt[0], {**cur_wt[1], "auto_clean_worktrees": False})
            return real_newest(path, budget, skip_git=skip_git)

        wt_state = {
            "worktrees_due_until": clock.now - 14 * DAY,
            "worktrees_due_seen_at": clock.now - 2 * DAY,
        }
        with monkeypatch.context() as mp:
            mp.setattr("config.nekopower_settings.STORAGE_AUTO_CLEAN_SCAN_SECONDS", 42.0)
            mp.setattr(storage_mod, "newest_mtime_of", spy_wt_newest)
            mp.setattr(host.task_agents, "_handle_of", fake_handle_budg)
            mp.setattr(host.task_agents.worktrees, "remove_if_untouched", fake_remove_if_untouched)
            await host.storage.reap_worktrees(clock.now, wt_state)
        assert (42.0, True) in wt_calls
        assert (await host.ledger.get_tree("budg0042")) is not None

        # 4. Policy disabled DURING _verify_and_remove's second newest_mtime_of scan aborts deletion.
        pol_sess = await host.ledger.create_session(name="mid-recheck-pol", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(pol_sess.id, status="idle")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, pol_sess.id))
        )
        pol_work = host.paths.work_dir(pol_sess.id, DEFAULT_PROJECT)
        pol_work.mkdir(parents=True)
        (pol_work / "keep.txt").write_text("must survive", encoding="utf-8")
        _set_tree_mtime(pol_work, old_ts)
        await host.set_storage_settings(auto_clean_work_dirs=True, work_dir_kept_days=7)
        recheck_n = {"n": 0}

        def disable_on_second_scan(path: Path, budget: float = 0.25, *, skip_git: bool = False) -> tuple[float | None, bool]:
            res = real_newest(path, budget, skip_git=skip_git)
            if path == pol_work:
                recheck_n["n"] += 1
                if recheck_n["n"] == 2:
                    cur_snap = host.storage.settings._kept
                    if cur_snap is not None:
                        host.storage.settings._kept = (cur_snap[0], {**cur_snap[1], "auto_clean_work_dirs": False})
            return res

        with monkeypatch.context() as mp:
            mp.setattr(storage_mod, "newest_mtime_of", disable_on_second_scan)
            out_pol, _ = await host.storage._clean_one_work_dir(
                "session",
                DEFAULT_PROJECT,
                pol_sess.id,
                until=clock.now - 7 * DAY,
                skip_project_referenced=True,
                expected_kept_days=7,
            )
        assert out_pol == "policy_changed"
        assert (pol_work / "keep.txt").read_text(encoding="utf-8") == "must survive"
    finally:
        await host.shutdown()


async def test_round5_worktree_orphan_expiry_and_gitfile_preservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify orphan case handling, partial git bytes accounting, expiry retry freshness, and .git preservation."""
    from nekopower.host import storage as storage_mod
    from nekopower.host import worktrees as wt_mod
    from nekopower.host.worktrees import Worktree, WorktreeError, WorktreeRemovalIncomplete

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 30 * DAY
        recent_ts = clock.now - 1 * DAY

        # 1. Case-variant orphan ID matching a tracked worktree respects volume case-sensitivity without touching tracked files.
        tracked_wt = host.paths.worktree_dir("abc012345678")
        tracked_wt.mkdir(parents=True)
        (tracked_wt / "keep.txt").write_text("tracked", encoding="utf-8")
        await host.ledger.add_tree(
            id="abc012345678",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(tmp_path),
            repo_root=str(tmp_path),
            path=str(tracked_wt),
            workdir=str(tracked_wt),
            state="active",
        )
        matches_tracked = (
            (tracked_wt.parent / "ABC012345678").exists()
            or os.path.normcase("ABC012345678") == os.path.normcase("abc012345678")
        )
        if matches_tracked:
            with pytest.raises(ValueError, match="busy"):
                await host.task_agents.remove_orphan_worktree("ABC012345678")
        else:
            out_case = await host.task_agents.remove_orphan_worktree("ABC012345678")
            assert out_case["removed"] is True
            assert out_case["freed_bytes"] == 0
        assert (tracked_wt / "keep.txt").exists()

        # 2. Orphan fallback after partial git removal includes git-freed bytes.
        orph_wt = host.paths.worktree_dir("orph9900")
        orph_wt.mkdir(parents=True)
        fake_repo = tmp_path / "orph_repo"
        fake_repo.mkdir()
        (orph_wt / ".git").write_text(f"gitdir: {(fake_repo / '.git' / 'worktrees' / 'orph9900').as_posix()}\n", encoding="utf-8")
        (orph_wt / "big.bin").write_bytes(b"a" * 800)
        (orph_wt / "small.bin").write_bytes(b"b" * 200)
        gitfile_size = (orph_wt / ".git").stat().st_size

        async def fake_open_orph(_repo: Path, path: Path) -> Worktree:
            return Worktree(id="orph9900", repo_root=fake_repo, path=path, subpath="", start="abc")

        async def partial_git_remove(handle: Worktree) -> None:
            (handle.path / "big.bin").unlink()
            raise WorktreeError("locked file remained")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents.worktrees, "open", fake_open_orph)
            mp.setattr(host.task_agents.worktrees, "remove", partial_git_remove)
            orph_out = await host.task_agents.remove_orphan_worktree("orph9900")
        assert orph_out["removed"] is True
        assert orph_out["freed_bytes"] == 800 + 200 + gitfile_size

        # 3. Auto-expired discarding worktree (reason="expired") respects freshness check on retry and overview() stale flag.
        sess_a = await host.ledger.create_session(name="wt-owner", lanlan_name="Lan", project=DEFAULT_PROJECT)
        exp_wt = host.paths.worktree_dir("expdef01")
        exp_wt.mkdir(parents=True)
        (exp_wt / "file.txt").write_text("old", encoding="utf-8")
        _set_tree_mtime(exp_wt, old_ts)
        comm_exp, _ = await host.ledger.create(
            dispatch_text="t", title="t", lanlan_name="Lan", source="dispatch", route="task_agent", workdir=str(exp_wt)
        )
        await host.ledger.transition(comm_exp.id, "working", reason="start")
        await host.ledger.transition(comm_exp.id, "blocked", reason="waiting")
        await host.ledger.add_tree(
            id="expdef01",
            session_id=sess_a.id,
            commission_id=comm_exp.id,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(fake_repo),
            repo_root=str(fake_repo),
            path=str(exp_wt),
            workdir=str(exp_wt),
            state="held",
            reason="ask",
        )
        await host.ledger.run(lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "expdef01")))
        exp_handle = Worktree(id="expdef01", repo_root=fake_repo, path=exp_wt, subpath="", start="abc")

        async def fake_handle_exp(_row: Any) -> Worktree:
            return exp_handle

        async def partial_expire_remove(_handle: Any, _check: Any = None) -> str:
            raise WorktreeRemovalIncomplete("partial removal")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_handle_of", fake_handle_exp)
            mp.setattr(host.task_agents.worktrees, "remove_if_untouched", partial_expire_remove)
            exp_res = await host.task_agents.expire_tree("expdef01", until=clock.now - 14 * DAY)
        assert exp_res == {"expired": True, "removed": False, "outcome": "deferred"}
        row_exp = await host.ledger.get_tree("expdef01")
        assert row_exp is not None and row_exp.state == "discarding" and row_exp.reason == "expired"
        comm_exp_after = await host.ledger.get(comm_exp.id)
        assert comm_exp_after is not None and comm_exp_after.status == "cancelled"

        (exp_wt / "file.txt").write_text("user edited", encoding="utf-8")
        _set_tree_mtime(exp_wt, recent_ts)
        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_handle_of", fake_handle_exp)
            retried = await host.task_agents.retry_discarded_tree(
                "expdef01",
                until=clock.now - 14 * DAY,
                check_mtime=lambda p: storage_mod.newest_mtime_of(p, 5.0, skip_git=True),
            )
        assert retried is False
        assert (exp_wt / "file.txt").read_text(encoding="utf-8") == "user edited"

        user_disc_wt = host.paths.worktree_dir("userdisc1")
        user_disc_wt.mkdir(parents=True)
        (user_disc_wt / "f.txt").write_text("recent user edit", encoding="utf-8")
        _set_tree_mtime(user_disc_wt, recent_ts)
        await host.ledger.add_tree(
            id="userdisc1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(fake_repo),
            repo_root=str(fake_repo),
            path=str(user_disc_wt),
            workdir=str(user_disc_wt),
            state="discarding",
            reason="",
        )
        view_disc = await host.storage_overview()
        wt_by_id = {w["id"]: w for w in view_disc["worktrees"]}
        assert wt_by_id["expdef01"]["stale"] is False
        assert wt_by_id["userdisc1"]["stale"] is True

        # 4. _remove_worktree_dir preserves .git on unlink error and restores .git on rmdir error.
        part_wt = tmp_path / "partial_wt"
        part_wt.mkdir()
        (part_wt / ".git").write_text("gitdir: /fake/.git/worktrees/part\n", encoding="utf-8")
        (part_wt / "removable.txt").write_text("bye", encoding="utf-8")
        locked = part_wt / "locked.txt"
        locked.write_text("busy", encoding="utf-8")
        real_unlink = os.unlink

        def fail_locked_unlink(p: Any) -> None:
            if Path(p).name == "locked.txt":
                raise PermissionError("file in use")
            real_unlink(p)

        with monkeypatch.context() as mp:
            mp.setattr(wt_mod.os, "unlink", fail_locked_unlink)
            wt_mod._remove_worktree_dir(part_wt)
        assert not (part_wt / "removable.txt").exists()
        assert locked.exists()
        assert (part_wt / ".git").exists()

        rmdir_wt = tmp_path / "rmdir_fail_wt"
        rmdir_wt.mkdir()
        git_content = "gitdir: /fake/.git/worktrees/rmdir_fail\n"
        (rmdir_wt / ".git").write_text(git_content, encoding="utf-8")
        (rmdir_wt / "done.txt").write_text("bye", encoding="utf-8")

        def fail_rmdir(p: Any) -> None:
            raise PermissionError("parent directory read-only or cwd in use")

        with monkeypatch.context() as mp:
            mp.setattr(wt_mod.os, "rmdir", fail_rmdir)
            wt_mod._remove_worktree_dir(rmdir_wt)
        assert not (rmdir_wt / "done.txt").exists()
        assert (rmdir_wt / ".git").read_text(encoding="utf-8") == git_content
    finally:
        await host.shutdown()


async def test_round5_transcript_estimation_and_untruncated_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify expired transcript estimation happens under hold_files and overview counts sessions beyond find_sessions cap."""
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        # 1. clean_expired_sessions_now estimates bytes AFTER acquiring hold_files.
        exp_sess = await host.ledger.create_session(name="old-tr", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(exp_sess.id, status="closed")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (clock.now - 60 * DAY, exp_sess.id))
        )
        exp_sdir = host.paths.session_dir(exp_sess.id, DEFAULT_PROJECT)
        exp_sdir.mkdir(parents=True, exist_ok=True)
        (exp_sdir / "transcript.jsonl").write_bytes(b"t" * 100)
        exp_outs = exp_sdir / "outputs"
        exp_outs.mkdir()
        (exp_outs / "spill.txt").write_bytes(b"o" * 900)

        real_hold = host.space_keeper._hold_files

        def hold_and_race_clear_outputs(sid: str, **kwargs: Any) -> bool:
            if sid == exp_sess.id and exp_outs.exists():
                import shutil
                shutil.rmtree(exp_outs)
            return bool(real_hold(sid, **kwargs))

        with monkeypatch.context() as mp:
            mp.setattr(host.space_keeper, "_hold_files", hold_and_race_clear_outputs)
            tr_res = await host.clean_transcripts("expired")
        assert tr_res["cleaned"] == 1
        assert tr_res["freed_bytes"] == 100

        # 2. overview() counts cleanable outputs even for sessions outside find_sessions()'s capped slice.
        capped_sess = await host.ledger.create_session(name="beyond-cap", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(capped_sess.id, status="idle")
        capped_out = host.paths.session_dir(capped_sess.id, DEFAULT_PROJECT) / "outputs"
        capped_out.mkdir(parents=True)
        (capped_out / "dump.txt").write_bytes(b"d" * 256)

        async def empty_find_sessions(*_args: Any, **_kwargs: Any) -> list[Any]:
            return []

        with monkeypatch.context() as mp:
            mp.setattr(host.ledger, "find_sessions", empty_find_sessions)
            view_cap = await host.storage_overview()
        assert view_cap["transcripts"]["outputs_count"] >= 1
        assert view_cap["transcripts"]["outputs_bytes"] >= 256
    finally:
        await host.shutdown()


async def test_expire_and_retry_tree_reject_linked_wt_root(tmp_path: Path):
    """Tracked worktree expiry and discarded-tree retry must fail closed when ``wt/`` is a symlink/junction."""
    import asyncio

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    try:
        old_ts = clock.now - 30 * DAY
        outside_wt = tmp_path / "outside_wt"
        victim = outside_wt / "keptlink1"
        victim.mkdir(parents=True)
        missing_repo = tmp_path / "missing_repo"
        (victim / ".git").write_text(
            f"gitdir: {(missing_repo / '.git' / 'worktrees' / 'keptlink1').as_posix()}\n", encoding="utf-8"
        )
        secret = victim / "important.txt"
        secret.write_text("do not delete", encoding="utf-8")
        _set_tree_mtime(victim, old_ts)

        wt_root = host.paths.worktrees_dir()
        wt_root.parent.mkdir(parents=True, exist_ok=True)
        assert await asyncio.to_thread(_make_dir_link, wt_root, outside_wt)

        linked_path = host.paths.worktree_dir("keptlink1")
        await host.ledger.add_tree(
            id="keptlink1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(missing_repo),
            repo_root=str(missing_repo),
            path=str(linked_path),
            workdir=str(linked_path),
            state="kept",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "keptlink1"))
        )

        exp = await host.task_agents.expire_tree("keptlink1", until=clock.now - 14 * DAY)
        assert exp == {"expired": False, "reason": "unsafe_path"}
        assert secret.read_text(encoding="utf-8") == "do not delete"

        await host.ledger.update_tree("keptlink1", state="discarding", reason="expired")
        assert await host.task_agents.retry_discarded_tree("keptlink1") is False
        assert secret.read_text(encoding="utf-8") == "do not delete"
    finally:
        await host.shutdown()


async def test_outputs_cleanup_rejects_linked_ancestors_and_gone_tree_retries_git_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Inactive output cleanup must reject symlinked project/sessions ancestors, and gone discarding trees retry git metadata."""
    import asyncio
    from nekopower.host.worktrees import GitError

    host = make_host(tmp_path)
    try:
        # 1. Symlinked project directory (_daily -> external dir): clean_transcripts("outputs") must refuse it.
        sess = await host.ledger.create_session(name="linked-out", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(sess.id, status="idle")
        outside_proj = tmp_path / "outside_daily"
        ext_outputs = outside_proj / "sessions" / sess.id / "outputs"
        ext_outputs.mkdir(parents=True)
        ext_file = ext_outputs / "keep.bin"
        ext_file.write_bytes(b"e" * 200)

        daily_dir = host.paths.root() / "task_agents" / "projects" / DEFAULT_PROJECT
        daily_dir.parent.mkdir(parents=True, exist_ok=True)
        assert await asyncio.to_thread(_make_dir_link, daily_dir, outside_proj)

        res = await host.clean_transcripts("outputs")
        assert res["cleaned"] == 0
        assert res["skipped"] == 1
        assert ext_file.read_bytes() == b"e" * 200

        # 2. Data-root-moved session at a real old task_agents/projects/<p>/sessions/<id>/outputs still cleans cleanly.
        moved_sess = await host.ledger.create_session(name="moved-out", lanlan_name="Lan", project="legacy")
        await host.ledger.update_session(moved_sess.id, status="idle")
        old_session_dir = tmp_path / "old_data" / "nekopower" / "task_agents" / "projects" / "legacy" / "sessions" / moved_sess.id
        old_outputs = old_session_dir / "outputs"
        old_outputs.mkdir(parents=True)
        (old_outputs / "spill.txt").write_bytes(b"m" * 150)
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE sessions SET session_dir = ? WHERE id = ?", (str(old_session_dir), moved_sess.id))
        )
        res_moved = await host.clean_transcripts("outputs")
        assert res_moved["cleaned"] == 1
        assert res_moved["freed_bytes"] == 150
        assert not old_outputs.exists()

        # 3. Discarding worktree whose directory is already gone retries git metadata cleanup via forget_gone()
        #    and retains the discarding row until forget_gone() succeeds.
        repo_dir = tmp_path / "real_repo"
        repo_dir.mkdir()
        gone_wt = host.paths.worktree_dir("gonemeta1")
        await host.ledger.add_tree(
            id="gonemeta1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="proj",
            project_dir=str(repo_dir),
            repo_root=str(repo_dir),
            path=str(gone_wt),
            workdir=str(gone_wt),
            state="discarding",
            reason="expired",
        )
        attempts = {"n": 0}

        async def repo_present(_row: Any) -> bool:
            return False

        async def flaky_forget_gone(root: Any, path: Any, wt_id: str) -> None:
            attempts["n"] += 1
            assert wt_id == "gonemeta1"
            if attempts["n"] == 1:
                raise GitError(("update-ref", "--stdin"), 128, b"fatal: cannot lock ref")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_repo_root_gone", repo_present)
            mp.setattr(host.task_agents.worktrees, "forget_gone", flaky_forget_gone)
            assert await host.task_agents.retry_discarded_tree("gonemeta1") is False
            assert (await host.ledger.get_tree("gonemeta1")) is not None
            assert await host.task_agents.retry_discarded_tree("gonemeta1") is True
            assert (await host.ledger.get_tree("gonemeta1")) is None
        assert attempts["n"] == 2
    finally:
        await host.shutdown()


async def test_round8_space_keeper_space_and_overview_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify fork freed-bytes preservation, corrupt project.json fail-closed, and busy-fork expired exclusion."""
    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 60 * DAY

        # 1. Parent failure in _clean_one_result preserves copies_freed from already-cleaned fork.
        parent = await host.ledger.create_session(name="parent-a", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(parent.id, status="closed")
        child = await host.ledger.create_session(
            name="fork-b", lanlan_name="Lan", project=DEFAULT_PROJECT, kind="fork", forked_from=parent.id
        )
        await host.ledger.update_session(child.id, status="closed")
        await host.ledger.run(
            lambda conn: conn.execute(
                "UPDATE sessions SET last_active_at = ? WHERE id IN (?, ?)", (old_ts, parent.id, child.id)
            )
        )
        for sid, size in ((parent.id, 300), (child.id, 100)):
            sdir = host.paths.session_dir(sid, DEFAULT_PROJECT)
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / TRANSCRIPT_NAME).write_bytes(b"t" * size)

        real_dir_of = host.space_keeper._session_dir_of

        def fail_parent_dir(session: Any) -> Any:
            if session.id == parent.id:
                raise PermissionError("parent dir denied")
            return real_dir_of(session)

        with monkeypatch.context() as mp:
            mp.setattr(host.space_keeper, "_session_dir_of", fail_parent_dir)
            outcome, freed = await host.space_keeper._clean_one_result(
                parent.id,
                clock.now - 30 * DAY,
                measure_fn=lambda p: sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file()),
            )
        assert outcome == "not_removed"
        assert freed == 100
        assert (await host.ledger.get_session(child.id)).status == "cleared"
        assert (await host.ledger.get_session(parent.id)).status == "closed"

        # 2. Corrupt project.json with 401-digit integer marks readable=False, keeps valid projects, fails closed on clean.
        good_sess = await host.ledger.create_session(name="good-s", lanlan_name="Lan", project="goodproj")
        await host.ledger.update_session(good_sess.id, status="idle")
        good_work = host.paths.work_dir(good_sess.id, "goodproj")
        good_work.mkdir(parents=True)
        (good_work / "a.txt").write_text("ok", encoding="utf-8")
        await host.space.touch_project("goodproj", workdir=str(good_work))
        await host.space.touch_project("badproj")
        bad_file = host.paths.space().project_dir("badproj") / "project.json"
        bad_file.write_text('{"name": "badproj", "commissions": ' + ("9" * 401) + "}", encoding="utf-8")

        listed, readable = await host.space.list_projects_checked(include_archived=False)
        assert readable is False
        assert any(p.name == "goodproj" and p.workdir == str(good_work) for p in listed)
        view = await host.storage_overview()
        wid_good = format_work_dir_id("session", "goodproj", good_sess.id)
        item_good = next(d for d in view["work_dirs"] if d["id"] == wid_good)
        assert item_good["can_clean"] is False
        clean_res = await host.clean_work_dirs([wid_good])
        assert clean_res["cleaned"] == []
        assert clean_res["failed"] == [{"id": wid_good, "code": "in_use"}]

        # 3. overview() excludes expired sessions that still have an active fork from expired_count / expired_bytes.
        exp_parent = await host.ledger.create_session(name="busy-fork-src", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(exp_parent.id, status="closed")
        active_fork = await host.ledger.create_session(
            name="active-fork", lanlan_name="Lan", project=DEFAULT_PROJECT, kind="fork", forked_from=exp_parent.id
        )
        await host.ledger.update_session(active_fork.id, status="running")
        free_expired = await host.ledger.create_session(name="free-exp", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(free_expired.id, status="closed")
        await host.ledger.run(
            lambda conn: conn.execute(
                "UPDATE sessions SET last_active_at = ? WHERE id IN (?, ?)", (old_ts, exp_parent.id, free_expired.id)
            )
        )
        for sid, size in ((exp_parent.id, 777), (free_expired.id, 123)):
            sdir = host.paths.session_dir(sid, DEFAULT_PROJECT)
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / TRANSCRIPT_NAME).write_bytes(b"e" * size)

        # Remove parent from test #1 (which is also expired) so exact expired counts reflect only these two.
        await host.ledger.mark_session_cleared(parent.id)
        view_exp = await host.storage_overview()
        assert view_exp["transcripts"]["expired_count"] == 1
        assert view_exp["transcripts"]["expired_bytes"] == 123
    finally:
        await host.shutdown()


async def test_round8_workdir_hold_and_idle_compact_reloop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify hold_workdir serializes against _claim_workdir_for_start and _idle_compact re-checks chained _clearing holds."""
    import asyncio

    host = make_host(tmp_path)
    try:
        # 1. Working-directory hold prevents concurrent start in the same directory until release_workdir runs.
        sess = await host.ledger.create_session(name="race-dir", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(sess.id, status="idle")
        target = host.paths.work_dir(sess.id, DEFAULT_PROJECT)
        target.mkdir(parents=True)

        hold_key = host.task_agents.hold_workdir(target)
        assert hold_key is not None
        assert host.task_agents.hold_workdir(target / "nested") is None

        claimed_done = asyncio.Event()

        async def starter() -> None:
            while not await host.task_agents._claim_workdir_for_start("comm-race", target / "nested"):
                pass
            claimed_done.set()

        start_task = asyncio.create_task(starter())
        await asyncio.sleep(0.02)
        assert not claimed_done.is_set()

        host.task_agents.release_workdir(hold_key)
        await asyncio.wait_for(claimed_done.wait(), timeout=2.0)
        await start_task

        # Once claimed by an active start, cleanup must refuse the overlapping workdir as in_use.
        assert host.task_agents.hold_workdir(target) is None
        out, freed = await host.storage._clean_one_work_dir("session", DEFAULT_PROJECT, sess.id)
        assert out == "in_use"
        assert freed == 0
        host.task_agents._starting_workdirs.pop("comm-race", None)

        # 2. _idle_compact loops when a second _clearing hold replaces the first before compaction resumes.
        sid = sess.id
        assert host.task_agents.hold_files(sid, drop_idle_timer=False) is True
        reached_idle_now = asyncio.Event()
        real_idle_now = host.task_agents._idle_now

        def spy_idle_now(session_id: str) -> bool:
            if session_id == sid:
                reached_idle_now.set()
            return real_idle_now(session_id)

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_idle_now", spy_idle_now)
            compact_task = asyncio.create_task(host.task_agents._idle_compact(sid, stops=host.task_agents._idle_stops))
            await asyncio.sleep(0.01)
            assert not reached_idle_now.is_set()
            # Release hold A and synchronously grab hold B before compact_task resumes.
            host.task_agents.release_files(sid)
            assert host.task_agents.hold_files(sid, drop_idle_timer=False) is True
            await asyncio.sleep(0.02)
            assert not reached_idle_now.is_set()
            # Release hold B: now _idle_compact proceeds past the clearing loop.
            host.task_agents.release_files(sid)
            await asyncio.wait_for(compact_task, timeout=2.0)
            assert reached_idle_now.is_set()
    finally:
        await host.shutdown()


async def test_round8_worktree_subdir_gone_restart_forget_gone_and_record_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify real git subdir-deleted metadata retry across restart, forget_gone reappear abort, and _forget record_only."""
    import asyncio
    import shutil
    from nekopower.host import worktrees as wt_mod
    from nekopower.host.worktrees import GitError, WorktreeError

    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    empty_cfg = tmp_path / "empty-gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    (tmp_path / "git-template").mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_ATTR_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "git-template"))
    for env_name in wt_mod._REDIRECTING_ENV:
        monkeypatch.delenv(env_name, raising=False)

    repo = tmp_path / "git_repo"
    subdir = repo / "packages" / "app"
    subdir.mkdir(parents=True)

    def _init_git_repo() -> None:
        for args in (
            ("git", "init"),
            ("git", "config", "user.name", "Test"),
            ("git", "config", "user.email", "test@example.com"),
        ):
            subprocess.run(args, cwd=repo, check=True, capture_output=True)
        (subdir / "hello.txt").write_text("hi\n", encoding="utf-8")
        subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True)
        subprocess.run(("git", "commit", "-m", "init"), cwd=repo, check=True, capture_output=True)

    await asyncio.to_thread(_init_git_repo)

    host1 = make_host(tmp_path)
    try:
        wt_base = host1.paths.worktrees_dir()
        handle = await host1.task_agents.worktrees.create(subdir, wt_base / "submeta01")
        assert handle.path.is_dir()

        # 1. forget_gone() aborts if target reappears with its .git file before _forget enters deletion.
        saved_git = (handle.path / ".git").read_text(encoding="utf-8")
        shutil.rmtree(handle.path)
        assert not handle.path.exists()

        real_registered = host1.task_agents.worktrees._registered

        async def restore_during_registered(repo_root: Path) -> list[Path]:
            res = await real_registered(repo_root)
            if not handle.path.exists():
                handle.path.mkdir(parents=True)
                (handle.path / ".git").write_text(saved_git, encoding="utf-8")
                (handle.path / "unsaved.txt").write_text("keep me", encoding="utf-8")
            return res

        with monkeypatch.context() as mp:
            mp.setattr(host1.task_agents.worktrees, "_registered", restore_during_registered)
            with pytest.raises(WorktreeError, match="reappeared"):
                await host1.task_agents.worktrees.forget_gone(handle.repo_root, handle.path, handle.id)
        assert (handle.path / "unsaved.txt").read_text(encoding="utf-8") == "keep me"
        assert handle.id in await host1.task_agents.worktrees._ref_ids(repo)

        # 2. Subdirectory deleted while repo_root still exists: discarding tree with gone dir retries real forget_gone across restart.
        await host1.ledger.add_tree(
            id=handle.id,
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="app",
            project_dir=str(subdir),
            repo_root=str(repo),
            path=str(handle.path),
            workdir=str(handle.workdir),
            state="discarding",
            reason="expired",
        )
        # Delete both the worktree dir and the project subdir while keeping git repo root intact.
        shutil.rmtree(handle.path)
        shutil.rmtree(subdir)
        assert not subdir.exists()
        assert (repo / ".git").is_dir()

        # First attempt fails transiently -> ledger row MUST be kept (not dropped via _repository_gone(project_dir)).
        async def transient_fail(*_args: Any, **_kwargs: Any) -> None:
            raise GitError(("update-ref", "--stdin"), 128, b"fatal: cannot lock ref")

        with monkeypatch.context() as mp:
            mp.setattr(host1.task_agents.worktrees, "forget_gone", transient_fail)
            assert await host1.task_agents.retry_discarded_tree(handle.id) is False
        assert (await host1.ledger.get_tree(handle.id)) is not None
    finally:
        await host1.shutdown()

    # Simulated restart: fresh host instance retries against repo_root and drops real git registration + refs.
    host2 = make_host(tmp_path)
    try:
        assert await host2.task_agents.retry_discarded_tree("submeta01") is True
        assert (await host2.ledger.get_tree("submeta01")) is None
        registered = await host2.task_agents.worktrees._registered(repo)
        assert all(p.name != "submeta01" for p in registered)
        assert "submeta01" not in await host2.task_agents.worktrees._ref_ids(repo)

        # 3. _forget() returns "record_only" when registration is already gone and an unrelated plain dir stands at path.
        handle.path.mkdir(parents=True)
        (handle.path / "user.txt").write_text("not a worktree", encoding="utf-8")
        outcome = await host2.task_agents.worktrees.remove_if_untouched(handle)
        assert outcome == "record_only"
        assert (handle.path / "user.txt").read_text(encoding="utf-8") == "not a worktree"
        shutil.rmtree(handle.path)

        # 4. Cancellation during _remove_what_is_left waits for removal thread before releasing _trees_in_hand.
        import threading
        from nekopower.host import tree_work as tw_mod

        orphan_repo = tmp_path / "gone_repo"
        rem_wt = host2.paths.worktree_dir("cancelrm1")
        rem_wt.mkdir(parents=True)
        (rem_wt / ".git").write_text(f"gitdir: {(orphan_repo / '.git' / 'worktrees' / 'cancelrm1').as_posix()}\n", encoding="utf-8")
        (rem_wt / "f.txt").write_text("bye", encoding="utf-8")
        await host2.ledger.add_tree(
            id="cancelrm1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(orphan_repo),
            repo_root=str(orphan_repo),
            path=str(rem_wt),
            workdir=str(rem_wt),
            state="discarding",
            reason="",
        )
        entered = threading.Event()
        allow_finish = threading.Event()
        real_remove_entry = tw_mod._remove_entry

        def slow_remove_entry(p: Path) -> None:
            if p.name == "f.txt":
                entered.set()
                allow_finish.wait(timeout=5.0)
            real_remove_entry(p)

        with monkeypatch.context() as mp:
            mp.setattr(tw_mod, "_remove_entry", slow_remove_entry)
            retry_task = asyncio.create_task(host2.task_agents.retry_discarded_tree("cancelrm1"))
            await asyncio.to_thread(entered.wait, 5.0)
            assert "cancelrm1" in host2.task_agents._trees_in_hand
            retry_task.cancel()
            await asyncio.sleep(0.05)
            # Still held in _trees_in_hand while worker thread has not returned.
            assert "cancelrm1" in host2.task_agents._trees_in_hand
            assert not retry_task.done()
            allow_finish.set()
            with pytest.raises(asyncio.CancelledError):
                await retry_task
            assert "cancelrm1" not in host2.task_agents._trees_in_hand
    finally:
        await host2.shutdown()


async def test_round9_worktree_active_session_protection_and_partial_retry_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify other active sessions block worktree expiry/retry, workdir hold serializes starts, and partial cleanup does not delay retries."""
    import asyncio
    from nekopower.host.worktrees import Worktree, WorktreeRemovalIncomplete

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 30 * DAY
        recent_ts = clock.now - 1 * DAY
        fake_repo = tmp_path / "r9_repo"
        fake_repo.mkdir()

        # 1. Another running session (e.g. a fork of a child session) working inside an old kept worktree blocks
        #    expire_tree(), discard_tree(), retry_discarded_tree(), and marks can_clean=False in overview().
        wt_shared = host.paths.worktree_dir("sharedwt1")
        nested_sub = wt_shared / "sub"
        nested_sub.mkdir(parents=True)
        (nested_sub / "work.txt").write_text("active session work", encoding="utf-8")
        _set_tree_mtime(wt_shared, old_ts)
        await host.ledger.add_tree(
            id="sharedwt1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(fake_repo),
            repo_root=str(fake_repo),
            path=str(wt_shared),
            workdir=str(wt_shared),
            state="kept",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "sharedwt1"))
        )
        other_sess = await host.ledger.create_session(
            name="fork-in-wt", lanlan_name="Lan", project=DEFAULT_PROJECT, workdir=str(nested_sub)
        )
        await host.ledger.update_session(other_sess.id, status="running")

        view_busy = await host.storage_overview()
        wt_row_view = next(w for w in view_busy["worktrees"] if w["id"] == "sharedwt1")
        assert wt_row_view["can_clean"] is False

        exp_busy = await host.task_agents.expire_tree("sharedwt1", until=clock.now - 14 * DAY)
        assert exp_busy == {"expired": False, "reason": "in_use"}
        assert (nested_sub / "work.txt").exists()

        with pytest.raises(ValueError, match="at_work"):
            await host.task_agents.discard_tree("sharedwt1")
        assert (nested_sub / "work.txt").exists()

        await host.ledger.update_tree("sharedwt1", state="discarding", reason="expired")
        assert await host.task_agents.retry_discarded_tree("sharedwt1") is False
        assert (nested_sub / "work.txt").exists()

        # Once the other session stops, expire_tree holds the workdir against concurrent starts throughout removal.
        await host.ledger.update_session(other_sess.id, status="idle")
        await host.ledger.update_tree("sharedwt1", state="kept", reason="")
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "sharedwt1"))
        )

        shared_handle = Worktree(id="sharedwt1", repo_root=fake_repo, path=wt_shared, subpath="", start="abc")
        entered_remove = asyncio.Event()
        finish_remove = asyncio.Event()

        async def fake_handle_shared(_row: Any) -> Worktree:
            return shared_handle

        async def slow_remove_if_untouched(_handle: Worktree, _check: Any = None) -> str:
            entered_remove.set()
            await finish_remove.wait()
            return "removed"

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_handle_of", fake_handle_shared)
            mp.setattr(host.task_agents.worktrees, "remove_if_untouched", slow_remove_if_untouched)
            exp_task = asyncio.create_task(host.task_agents.expire_tree("sharedwt1", until=clock.now - 14 * DAY))
            await asyncio.wait_for(entered_remove.wait(), timeout=2.0)

            start_claimed = asyncio.Event()

            async def try_start_in_wt() -> None:
                while not await host.task_agents._claim_workdir_for_start("comm-wt-race", nested_sub):
                    pass
                start_claimed.set()

            race_task = asyncio.create_task(try_start_in_wt())
            await asyncio.sleep(0.02)
            assert not start_claimed.is_set()

            finish_remove.set()
            exp_done = await asyncio.wait_for(exp_task, timeout=2.0)
            assert exp_done == {"expired": True, "removed": True, "outcome": "removed"}
            await asyncio.wait_for(start_claimed.wait(), timeout=2.0)
            await race_task
            host.task_agents._starting_workdirs.pop("comm-wt-race", None)

        # 2. Partial auto-expiry via real _remove_worktree_dir restores surviving directory mtimes when deleting
        #    one file while leaving a locked file behind, so the next reap_worktrees() round retries immediately
        #    once unlocked (not delayed 14 days), while moving an OLD-mtime file into a deferred worktree updates
        #    the directory's mtime and MUST spare the worktree.
        import shutil
        from nekopower.host import worktrees as wt_mod

        wt_partial = host.paths.worktree_dir("partwt01")
        wt_partial.mkdir(parents=True)
        (wt_partial / ".git").write_text("gitdir: /fake/.git/worktrees/partwt01\n", encoding="utf-8")
        rem_file = wt_partial / "removable.txt"
        locked_file = wt_partial / "locked.txt"
        rem_file.write_text("gone on first try", encoding="utf-8")
        locked_file.write_text("locked on first try", encoding="utf-8")
        _set_tree_mtime(wt_partial, old_ts)
        await host.ledger.add_tree(
            id="partwt01",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(fake_repo),
            repo_root=str(fake_repo),
            path=str(wt_partial),
            workdir=str(wt_partial),
            state="kept",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "partwt01"))
        )
        part_handle = Worktree(id="partwt01", repo_root=fake_repo, path=wt_partial, subpath="", start="abc")
        lock_active = {"locked": True}
        removed_on_retry = {"done": False}
        real_unlink = os.unlink

        async def fake_handle_part(_row: Any) -> Worktree:
            return part_handle

        def maybe_lock_unlink(p: Any) -> None:
            if lock_active["locked"] and Path(p).name == "locked.txt":
                raise PermissionError("file in use")
            real_unlink(p)

        async def real_dir_remove_if_untouched(handle: Worktree, is_untouched: Any = None) -> str:
            if is_untouched is not None and not is_untouched(handle.path):
                return "aborted"
            wt_mod._remove_worktree_dir(handle.path, preserve_dir_times=is_untouched is not None)
            if handle.path.exists():
                raise WorktreeRemovalIncomplete("locked.txt still open")
            removed_on_retry["done"] = True
            return "removed"

        await host.set_storage_settings(auto_clean_worktrees=True, worktree_kept_days=14)
        wt_state = {
            "worktrees_due_until": clock.now - 14 * DAY,
            "worktrees_due_seen_at": clock.now - 2 * DAY,
        }
        with monkeypatch.context() as mp:
            mp.setattr(wt_mod.os, "unlink", maybe_lock_unlink)
            mp.setattr(host.task_agents, "_handle_of", fake_handle_part)
            mp.setattr(host.task_agents.worktrees, "remove_if_untouched", real_dir_remove_if_untouched)
            # Round 1: expires partwt01, removes removable.txt, leaves locked.txt + .git, preserves dir old_ts.
            await host.storage.reap_worktrees(clock.now, wt_state)
            row_mid = await host.ledger.get_tree("partwt01")
            assert row_mid is not None and row_mid.state == "discarding" and row_mid.reason == "expired"
            assert not rem_file.exists() and locked_file.exists()
            assert abs(wt_partial.stat().st_mtime - old_ts) < 2.0

            # overview() still considers it stale because surviving dir & file mtimes remain old.
            view_mid = await host.storage_overview()
            item_mid = next(w for w in view_mid["worktrees"] if w["id"] == "partwt01")
            assert item_mid["stale"] is True

            # Round 2 (same day, after lock released): retries immediately and completes removal!
            lock_active["locked"] = False
            await host.storage.reap_worktrees(clock.now + 3600.0, wt_state)
            assert removed_on_retry["done"] is True
            assert (await host.ledger.get_tree("partwt01")) is None
            assert not wt_partial.exists()

        # 3. Moving an OLD-mtime file into a deferred auto-expired worktree refreshes the destination directory's
        #    mtime while keeping the file's old mtime; reap_worktrees() and overview() MUST still spare it!
        wt_moved = host.paths.worktree_dir("movewt02")
        wt_moved.mkdir(parents=True)
        (wt_moved / "existing.txt").write_text("old", encoding="utf-8")
        _set_tree_mtime(wt_moved, old_ts)
        old_src = tmp_path / "old_archive.bin"
        old_src.write_bytes(b"precious old archive")
        _set_mtime(old_src, old_ts)
        # Simulate user moving an old-mtime file into wt_moved (file keeps old_ts, directory gets recent mtime).
        shutil.move(str(old_src), str(wt_moved / "old_archive.bin"))
        _set_mtime(wt_moved / "old_archive.bin", old_ts)
        _set_mtime(wt_moved, recent_ts)

        await host.ledger.add_tree(
            id="movewt02",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project=DEFAULT_PROJECT,
            project_dir=str(fake_repo),
            repo_root=str(fake_repo),
            path=str(wt_moved),
            workdir=str(wt_moved),
            state="discarding",
            reason="expired",
        )
        move_handle = Worktree(id="movewt02", repo_root=fake_repo, path=wt_moved, subpath="", start="abc")

        async def fake_handle_move(_row: Any) -> Worktree:
            return move_handle

        async def fail_if_removed(_handle: Worktree, is_untouched: Any = None) -> str:
            if is_untouched is not None and not is_untouched(_handle.path):
                return "aborted"
            raise AssertionError("worktree with newly moved-in old file must not be removed")

        view_moved = await host.storage_overview()
        item_moved = next(w for w in view_moved["worktrees"] if w["id"] == "movewt02")
        assert item_moved["stale"] is False

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_handle_of", fake_handle_move)
            mp.setattr(host.task_agents.worktrees, "remove_if_untouched", fail_if_removed)
            await host.storage.reap_worktrees(clock.now + 3600.0, wt_state)
        assert (await host.ledger.get_tree("movewt02")) is not None
        assert (wt_moved / "old_archive.bin").read_bytes() == b"precious old archive"

        # 4. If a new entry appears concurrently DURING partial _remove_worktree_dir, do NOT rewind the dir mtime.
        wt_conc = tmp_path / "conc_move_wt"
        wt_conc.mkdir()
        (wt_conc / ".git").write_text("gitdir: /fake/.git/worktrees/conc\n", encoding="utf-8")
        (wt_conc / "removable.txt").write_text("bye", encoding="utf-8")
        (wt_conc / "locked.txt").write_text("stay", encoding="utf-8")
        _set_tree_mtime(wt_conc, old_ts)

        def concurrent_add_on_unlink(p: Any) -> None:
            if Path(p).name == "locked.txt":
                raise PermissionError("locked")
            real_unlink(p)
            if Path(p).name == "removable.txt":
                added = wt_conc / "moved_during_cleanup.bin"
                added.write_bytes(b"new")
                _set_mtime(added, old_ts)
                _set_mtime(wt_conc, recent_ts)

        with monkeypatch.context() as mp:
            mp.setattr(wt_mod.os, "unlink", concurrent_add_on_unlink)
            wt_mod._remove_worktree_dir(wt_conc, preserve_dir_times=True)
        assert (wt_conc / "moved_during_cleanup.bin").exists()
        assert wt_conc.stat().st_mtime > old_ts + DAY

        # 5. If settings/freshness flip during _snapshot_dir_times, before_delete runs AFTER the snapshot,
        #    aborts removal without touching files, and does NOT rewind the directory's fresh mtime.
        wt_abort = tmp_path / "abort_after_snap_wt"
        wt_abort.mkdir()
        (wt_abort / ".git").write_text("gitdir: /fake/.git/worktrees/abort\n", encoding="utf-8")
        (wt_abort / "keep.txt").write_text("do not delete", encoding="utf-8")
        _set_tree_mtime(wt_abort, old_ts)
        real_snapshot = wt_mod._snapshot_dir_times
        order: list[str] = []

        def spy_snapshot(p: Path) -> Any:
            res = real_snapshot(p)
            order.append("snapshot")
            # Simulate policy change or fresh touch occurring while snapshot walked the tree.
            _set_mtime(wt_abort, recent_ts)
            return res

        def abort_guard(_p: Path) -> bool:
            order.append("before_delete")
            return False

        with monkeypatch.context() as mp:
            mp.setattr(wt_mod, "_snapshot_dir_times", spy_snapshot)
            assert wt_mod._remove_worktree_dir(wt_abort, before_delete=abort_guard, preserve_dir_times=True) is False
        assert order == ["snapshot", "before_delete"]
        assert (wt_abort / "keep.txt").read_text(encoding="utf-8") == "do not delete"
        assert wt_abort.stat().st_mtime > old_ts + DAY
    finally:
        await host.shutdown()


async def test_round9_subdir_gone_with_existing_worktree_uses_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify worktree cleanup opens via repo_root (and refuses _remove_what_is_left) when project subdir is gone but repo and worktree exist."""
    import asyncio
    import shutil
    from nekopower.host import worktrees as wt_mod
    from nekopower.host.worktrees import WorktreeError

    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    empty_cfg = tmp_path / "empty-gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    (tmp_path / "git-template").mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_ATTR_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "git-template"))
    for env_name in wt_mod._REDIRECTING_ENV:
        monkeypatch.delenv(env_name, raising=False)

    repo = tmp_path / "sub_exist_repo"
    subdir = repo / "packages" / "web"
    subdir.mkdir(parents=True)

    def _init_repo() -> None:
        for args in (
            ("git", "init"),
            ("git", "config", "user.name", "Test"),
            ("git", "config", "user.email", "test@example.com"),
        ):
            subprocess.run(args, cwd=repo, check=True, capture_output=True)
        (subdir / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
        subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True)
        subprocess.run(("git", "commit", "-m", "init"), cwd=repo, check=True, capture_output=True)

    await asyncio.to_thread(_init_repo)

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    try:
        old_ts = clock.now - 30 * DAY
        handle = await host.task_agents.worktrees.create(subdir, host.paths.worktrees_dir() / "sublive01")
        await host.ledger.add_tree(
            id=handle.id,
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="web",
            project_dir=str(subdir),
            repo_root=str(repo),
            path=str(handle.path),
            workdir=str(handle.workdir),
            token=handle.token,
            state="kept",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, handle.id))
        )
        # Drop cached handle and delete the project subdir while BOTH repo_root and worktree directory still exist.
        host.task_agents._tree_handles.pop(handle.id, None)
        shutil.rmtree(subdir)
        assert not subdir.exists()
        assert (repo / ".git").is_dir()
        assert handle.path.is_dir()

        # 1. If opening via repo_root fails transiently while repo_root exists, expire_tree must NOT fall back to
        #    _remove_what_is_left (which would drop the row without cleaning git metadata).
        real_open = host.task_agents.worktrees.open

        async def fail_open(*_args: Any, **_kwargs: Any) -> Any:
            raise WorktreeError("transient git lock")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents.worktrees, "open", fail_open)
            with pytest.raises(ValueError, match="not_opened"):
                await host.task_agents.expire_tree(handle.id, until=clock.now - 14 * DAY)
        assert handle.path.is_dir()
        assert (await host.ledger.get_tree(handle.id)) is not None

        # 2. Once git open succeeds via repo_root fallback, expire_tree cleans directory + git registration + refs.
        host.task_agents.worktrees.open = real_open  # type: ignore[method-assign]
        res = await host.task_agents.expire_tree(handle.id, until=clock.now - 14 * DAY)
        assert res == {"expired": True, "removed": True, "outcome": "removed"}
        assert not handle.path.exists()
        assert (await host.ledger.get_tree(handle.id)) is None
        registered = await host.task_agents.worktrees._registered(repo)
        assert all(p.name != "sublive01" for p in registered)
        assert "sublive01" not in await host.task_agents.worktrees._ref_ids(repo)
    finally:
        await host.shutdown()


async def test_round12_security_paths_and_storage_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify link-chmod guard, symlinked wt/ & workdir rejection, migrated outputs overview, and corrupt archive resilience."""
    import asyncio
    import json
    import shutil
    from nekopower.host import storage as storage_mod
    from nekopower.host.storage import remove_dir_safely

    host = make_host(tmp_path)
    try:
        # 1. remove_dir_safely must NOT call _clear_readonly on a link when unlinking the link fails.
        ta_root = host.paths.root() / "task_agents"
        work_dir = host.paths.work_dir("lnk-sess", DEFAULT_PROJECT)
        work_dir.mkdir(parents=True)
        ext_dir = tmp_path / "external_link_target"
        ext_dir.mkdir()
        (ext_dir / "secret.txt").write_text("keep", encoding="utf-8")
        child_link = work_dir / "linked"
        assert await asyncio.to_thread(_make_dir_link, child_link, ext_dir)

        chmod_calls: list[str] = []

        def record_chmod(p: str) -> None:
            chmod_calls.append(p)

        real_rmdir = os.rmdir
        real_unlink = os.unlink

        def fail_link_rm(p: Any, *args: Any, **kwargs: Any) -> None:
            if os.path.normcase(os.path.basename(str(p))) == "linked":
                raise PermissionError("link locked")
            real_rmdir(p, *args, **kwargs)

        def fail_link_unlink(p: Any, *args: Any, **kwargs: Any) -> None:
            if os.path.normcase(os.path.basename(str(p))) == "linked":
                raise PermissionError("link locked")
            real_unlink(p, *args, **kwargs)

        with monkeypatch.context() as mp:
            mp.setattr(storage_mod, "_clear_readonly", record_chmod)
            mp.setattr(os, "rmdir", fail_link_rm)
            mp.setattr(os, "unlink", fail_link_unlink)
            removed, _freed = remove_dir_safely(work_dir, ta_root)
        assert removed is False
        assert all(os.path.normcase(os.path.basename(p)) != "linked" for p in chmod_calls)
        assert (ext_dir / "secret.txt").read_text(encoding="utf-8") == "keep"

        # 2. Migrated session_dir outputs/ appear in overview outputs_count & outputs_bytes without double counting.
        moved_sess = await host.ledger.create_session(name="moved-view", lanlan_name="Lan", project="legacy")
        await host.ledger.update_session(moved_sess.id, status="idle")
        old_sess_dir = (
            tmp_path / "old_root" / "nekopower" / "task_agents" / "projects" / "legacy" / "sessions" / moved_sess.id
        )
        old_out = old_sess_dir / "outputs"
        old_out.mkdir(parents=True)
        (old_out / "blob.txt").write_bytes(b"b" * 175)
        await host.ledger.run(
            lambda conn: conn.execute(
                "UPDATE sessions SET session_dir = ? WHERE id = ?", (str(old_sess_dir), moved_sess.id)
            )
        )
        view_migrated = await host.storage_overview()
        assert view_migrated["transcripts"]["outputs_count"] == 1
        assert view_migrated["transcripts"]["outputs_bytes"] == 175

        # 2b. Manually moved data folder: stale session_dir points to gone old location while current folder holds
        #     transcript & outputs/; overview must match TaskAgentManager._session_dir_of() and count current folder.
        hand_sess = await host.ledger.create_session(name="hand-moved", lanlan_name="Lan", project="handproj")
        await host.ledger.update_session(hand_sess.id, status="idle")
        missing_old_dir = (
            tmp_path / "gone_old_root" / "nekopower" / "task_agents" / "projects" / "handproj" / "sessions" / hand_sess.id
        )
        cur_hand_dir = host.paths.session_dir(hand_sess.id, "handproj")
        (cur_hand_dir / "outputs").mkdir(parents=True)
        (cur_hand_dir / TRANSCRIPT_NAME).write_bytes(b"h" * 64)
        (cur_hand_dir / "outputs" / "cache.txt").write_bytes(b"c" * 225)
        await host.ledger.run(
            lambda conn: conn.execute(
                "UPDATE sessions SET session_dir = ? WHERE id = ?", (str(missing_old_dir), hand_sess.id)
            )
        )
        view_hand_moved = await host.storage_overview()
        assert view_hand_moved["transcripts"]["outputs_count"] == 2
        assert view_hand_moved["transcripts"]["outputs_bytes"] == 175 + 225

        # 3. Corrupt archived project.json (huge integer -> OverflowError) must not break storage_overview().
        bad_arch = host.paths.space().archived_dir("2025", "corrupt_arch")
        bad_arch.mkdir(parents=True)
        (bad_arch / "project.json").write_text(
            json.dumps({"name": "corrupt_arch", "commissions": 10**400}), encoding="utf-8"
        )
        view_arch = await host.storage_overview()
        assert "categories" in view_arch

        # 4. Symlinked worktree project subdir is refused by resolve_storage_open_path("worktree", ...).
        wt_real = host.paths.worktree_dir("sublnk01")
        wt_real.mkdir(parents=True)
        (wt_real / "real_app").mkdir()
        ext_app = tmp_path / "outside_app"
        ext_app.mkdir()
        link_sub = wt_real / "bad_sub"
        assert await asyncio.to_thread(_make_dir_link, link_sub, ext_app)
        await host.ledger.add_tree(
            id="sublnk01",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="app",
            project_dir=str(tmp_path / "repo"),
            repo_root=str(tmp_path / "repo"),
            path=str(wt_real),
            workdir=str(link_sub),
            state="kept",
        )
        with pytest.raises(ValueError, match="bad_worktree"):
            await host.resolve_storage_open_path("worktree", "sublnk01")
        # Regular subdir inside worktree opens normally.
        await host.ledger.update_tree("sublnk01", workdir=str(wt_real / "real_app"))
        assert await host.resolve_storage_open_path("worktree", "sublnk01") == wt_real / "real_app"

        # 5. Symlinked wt/ root: refused by overview measurement/can_clean, clean_worktrees, and place open.
        shutil.rmtree(host.paths.worktrees_dir())
        ext_wt_root = tmp_path / "outside_wt_root"
        (ext_wt_root / "sublnk01").mkdir(parents=True)
        (ext_wt_root / "sublnk01" / "payload.bin").write_bytes(b"p" * 4096)
        assert await asyncio.to_thread(_make_dir_link, host.paths.worktrees_dir(), ext_wt_root)
        view_wt_lnk = await host.storage_overview()
        wt_entry = next(w for w in view_wt_lnk["worktrees"] if w["id"] == "sublnk01")
        assert wt_entry["bytes"] == 0
        assert wt_entry["can_clean"] is False
        assert wt_entry["stale"] is False
        clean_wt_lnk = await host.clean_worktrees(["sublnk01"])
        assert clean_wt_lnk["cleaned"] == []
        assert clean_wt_lnk["failed"] == [{"id": "sublnk01", "code": "unsafe_path"}]
        with pytest.raises(ValueError, match="bad_place"):
            await host.resolve_storage_open_path("place", "worktrees")
    finally:
        await host.shutdown()


async def test_round12_partial_clean_and_lifecycle_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify partial transcript freed bytes, mid-page close stop, work_dir retry mtime, pending expiry recovery, and idle-compact retry."""
    import asyncio
    import shutil
    from nekopower.host import storage as storage_mod
    from nekopower.host.worktrees import Worktree, WorktreeError

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    host.space_keeper._clock = clock
    try:
        old_ts = clock.now - 60 * DAY

        # 1. Partial session cleanup (transcript deleted, outputs/ rmtree fails) reports freed transcript bytes.
        ps = await host.ledger.create_session(name="partial-sess", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(ps.id, status="idle")
        await host.ledger.run(lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, ps.id)))
        sdir = host.paths.session_dir(ps.id, DEFAULT_PROJECT)
        (sdir / "outputs").mkdir(parents=True)
        (sdir / "transcript.jsonl").write_bytes(b"t" * 512)
        (sdir / "outputs" / "locked.txt").write_bytes(b"o" * 256)

        real_rmtree = shutil.rmtree

        def fail_outputs(p: Any, *args: Any, **kwargs: Any) -> None:
            if Path(p) == sdir / "outputs":
                raise PermissionError("output locked")
            real_rmtree(p, *args, **kwargs)

        with monkeypatch.context() as mp:
            mp.setattr(shutil, "rmtree", fail_outputs)
            partial_res = await host.clean_transcripts("expired")
        assert partial_res["cleaned"] == 0
        assert partial_res["skipped"] == 1
        assert partial_res["freed_bytes"] == 512
        assert not (sdir / "transcript.jsonl").exists()
        assert (await host.ledger.get_session(ps.id)).status != "cleared"

        # 2. Partial auto-cleanup of work/ preserves directory mtime so next upkeep retries without 30-day reset.
        ws = await host.ledger.create_session(name="partial-work", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(ws.id, status="idle")
        await host.ledger.run(lambda conn: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, ws.id)))
        wdir = host.paths.work_dir(ws.id, DEFAULT_PROJECT)
        wdir.mkdir(parents=True)
        (wdir / "a_gone.txt").write_text("gone", encoding="utf-8")
        (wdir / "b_locked.txt").write_text("locked", encoding="utf-8")
        _set_tree_mtime(wdir, old_ts)

        real_unlink = os.unlink

        def fail_b_locked(p: Any, *args: Any, **kwargs: Any) -> None:
            if os.path.basename(str(p)) == "b_locked.txt":
                raise PermissionError("locked")
            real_unlink(p, *args, **kwargs)
            # Explicitly advance parent dir mtime past clock.now cutoff so the test cannot
            # pass accidentally when wall-clock time is earlier than clock.now.
            os.utime(wdir, (clock.now, clock.now))

        with monkeypatch.context() as mp:
            mp.setattr(os, "unlink", fail_b_locked)
            out1, _ = await host.storage._clean_one_work_dir(
                "session", DEFAULT_PROJECT, ws.id, until=clock.now - 14 * DAY, skip_project_referenced=True
            )
        assert out1 == "not_removed"
        assert not (wdir / "a_gone.txt").exists()
        assert (wdir / "b_locked.txt").exists()
        assert abs(wdir.stat().st_mtime - old_ts) < 2.0
        # Next upkeep retries immediately (not misclassified as "recent").
        out2, _ = await host.storage._clean_one_work_dir(
            "session", DEFAULT_PROJECT, ws.id, until=clock.now - 14 * DAY, skip_project_referenced=True
        )
        assert out2 == "cleaned"
        assert not wdir.exists()

        # 3. _handle_for_cleanup does NOT cache repo_root fallback handle in _tree_handles.
        repo_root = tmp_path / "cache_repo"
        sub_dir = repo_root / "sub"
        repo_root.mkdir()
        wt_fb = host.paths.worktree_dir("nocache01")
        wt_fb.mkdir(parents=True)
        await host.ledger.add_tree(
            id="nocache01",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="sub",
            project_dir=str(sub_dir),
            repo_root=str(repo_root),
            path=str(wt_fb),
            workdir=str(wt_fb / "sub"),
            token="tok-nocache01",
            state="kept",
        )
        row_fb = await host.ledger.get_tree("nocache01")
        assert row_fb is not None
        fake_handle = Worktree(
            id="nocache01", repo_root=repo_root, path=wt_fb, subpath="", start="abc", token="tok-nocache01"
        )

        async def open_only_root(pdir: Any, _wt: Any) -> Worktree:
            if Path(pdir) == repo_root:
                return fake_handle
            raise WorktreeError("subdir missing")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents.worktrees, "open", open_only_root)
            got = await host.task_agents._handle_for_cleanup(row_fb)
        assert got is fake_handle
        assert "nocache01" not in host.task_agents._tree_handles

        # 4. Pending expiry persisted in space state settles blocked commission across simulated restart.
        comm_sess = await host.ledger.create_session(name="exp-sess", lanlan_name="Lan", project="expproj")
        comm, _created = await host.ledger.create(
            lanlan_name="Lan",
            title="Blocked on expired tree",
            dispatch_text="do work",
            source="dispatch",
            project="expproj",
            session_id=comm_sess.id,
        )
        wt_rec = host.paths.worktree_dir("pendexp01")
        wt_rec.mkdir(parents=True)
        await host.ledger.update(comm.id, workdir=str(wt_rec))
        await host.ledger.transition(comm.id, "working")
        await host.ledger.transition(comm.id, "blocked", reason="waiting for landing")
        await host.ledger.add_tree(
            id="pendexp01",
            session_id=comm_sess.id,
            commission_id=comm.id,
            lanlan_name="Lan",
            project="expproj",
            project_dir=str(tmp_path / "gone_repo"),
            repo_root=str(tmp_path / "gone_repo"),
            path=str(wt_rec),
            workdir=str(wt_rec),
            state="held",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "pendexp01"))
        )
        # Directory deleted by expire_tree, then ledger transition fails before settling.
        real_transition = host.ledger.transition

        async def fail_transition(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("transient ledger failure")

        async def ok_remove_if_untouched(handle: Any, _cb: Any = None) -> str:
            shutil.rmtree(handle.path, ignore_errors=True)
            return "removed"

        async def open_pend(_pdir: Any, _wt: Any) -> Worktree:
            return Worktree(id="pendexp01", repo_root=tmp_path / "gone_repo", path=wt_rec, subpath="", start="abc")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents.worktrees, "open", open_pend)
            mp.setattr(host.task_agents.worktrees, "remove_if_untouched", ok_remove_if_untouched)
            mp.setattr(host.ledger, "transition", fail_transition)
            with pytest.raises(RuntimeError, match="transient ledger failure"):
                await host.task_agents.expire_tree("pendexp01", until=clock.now - 14 * DAY)
        assert not wt_rec.exists()
        assert (await host.ledger.get(comm.id)).status == "blocked"
        assert (await host.ledger.get_tree("pendexp01")) is not None

        # Simulated host restart: reconcile_trees recovers pending expiry and cancels the blocked commission.
        host.task_agents._tree_handles.clear()
        host.task_agents._trees_mine.clear()
        host.ledger.transition = real_transition  # type: ignore[method-assign]
        rec = await host.task_agents.reconcile_trees()
        assert "pendexp01" in rec["removed"]
        assert (await host.ledger.get_tree("pendexp01")) is None
        assert (await host.ledger.get(comm.id)).status == "cancelled"

        # 5. Startup reconcile_trees leaves discarding/expired rows to upkeep even when directory is gone.
        await host.ledger.add_tree(
            id="discexp01",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="expproj",
            project_dir=str(tmp_path / "gone_repo"),
            repo_root=str(tmp_path / "gone_repo"),
            path=str(host.paths.worktree_dir("discexp01")),
            workdir=str(host.paths.worktree_dir("discexp01")),
            state="discarding",
            reason="expired",
        )
        rec_disc = await host.task_agents.reconcile_trees()
        assert "discexp01" in rec_disc["kept"]
        assert (await host.ledger.get_tree("discexp01")) is not None

        # 6. _idle_compact waits and retries when a non-transcript file hold starts during _idle_compact_wanted().
        ic_sess = await host.ledger.create_session(name="ic-wait", lanlan_name="Lan", project=DEFAULT_PROJECT)
        await host.ledger.update_session(ic_sess.id, status="idle")
        ic_calls = {"wanted": 0, "claimed": False}

        async def wanted_with_concurrent_hold(sid: str) -> bool:
            ic_calls["wanted"] += 1
            if ic_calls["wanted"] == 1:
                assert host.task_agents.hold_files(sid, drop_idle_timer=False) is True

                async def release_soon() -> None:
                    await asyncio.sleep(0.01)
                    host.task_agents.release_files(sid)

                asyncio.ensure_future(release_soon())
            return True

        async def note_claimed(_sid: str) -> float:
            ic_calls["claimed"] = True
            return 10**9

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_idle_compact_wanted", wanted_with_concurrent_hold)
            mp.setattr(host.task_agents, "_idle_after", note_claimed)
            await host.task_agents._idle_compact(ic_sess.id)
        assert ic_calls["wanted"] == 2
        assert ic_calls["claimed"] is True

        # 7. clean_expired_sessions_now stops mid-page when _closed becomes True.
        c1 = await host.ledger.create_session(name="close-1", lanlan_name="Lan", project=DEFAULT_PROJECT)
        c2 = await host.ledger.create_session(name="close-2", lanlan_name="Lan", project=DEFAULT_PROJECT)
        for s in (c1, c2):
            await host.ledger.update_session(s.id, status="idle")
            await host.ledger.run(lambda conn, sid=s.id: conn.execute("UPDATE sessions SET last_active_at = ? WHERE id = ?", (old_ts, sid)))
        seen_clean: list[str] = []

        async def stop_after_first(sid: str, *_args: Any, **_kwargs: Any) -> tuple[str, int]:
            seen_clean.append(sid)
            host.space_keeper._closed = True
            return "removed", 10

        with monkeypatch.context() as mp:
            mp.setattr(host.space_keeper, "_clean_one_result", stop_after_first)
            res_closed = await host.space_keeper.clean_expired_sessions_now()
        host.space_keeper._closed = False
        assert len(seen_clean) == 1
        assert res_closed["cleaned"] == 1
        assert storage_mod._classify_path_safety is not None
    finally:
        await host.shutdown()


async def test_round12_forget_gone_aborts_if_different_dir_reappears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """forget_gone must abort without dropping registration or refs if a different directory reappears at path."""
    import asyncio
    import shutil
    import subprocess
    from nekopower.host import worktrees as wt_mod
    from nekopower.host.worktrees import WorktreeError

    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    empty_cfg = tmp_path / "empty_gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_ATTR_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "git-template"))
    for env_name in wt_mod._REDIRECTING_ENV:
        monkeypatch.delenv(env_name, raising=False)

    repo = tmp_path / "fg_repo"
    repo.mkdir()

    def _init_repo() -> None:
        for args in (
            ("git", "init"),
            ("git", "config", "user.name", "Test"),
            ("git", "config", "user.email", "test@example.com"),
        ):
            subprocess.run(args, cwd=repo, check=True, capture_output=True)
        (repo / "a.txt").write_text("hi\n", encoding="utf-8")
        subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True)
        subprocess.run(("git", "commit", "-m", "init"), cwd=repo, check=True, capture_output=True)

    await asyncio.to_thread(_init_repo)
    mgr = wt_mod.WorktreeManager()
    wt_path = tmp_path / "wt" / "fgtheirs1"
    handle = await mgr.create(repo, wt_path)
    shutil.rmtree(wt_path)
    assert not wt_path.exists()

    real_whose = wt_mod._whose

    def recreate_theirs_dir(path: Path, common_dir: Path) -> str:
        # Simulate another directory appearing at path right before _whose() checks it.
        path.mkdir(parents=True, exist_ok=True)
        (path / "other.txt").write_text("not yours", encoding="utf-8")
        return real_whose(path, common_dir)

    with monkeypatch.context() as mp:
        mp.setattr(wt_mod, "_whose", recreate_theirs_dir)
        with pytest.raises(WorktreeError, match="reappeared"):
            await mgr.forget_gone(repo, wt_path, handle.id)

    # Directory, git registration, and refs must all remain intact.
    assert (wt_path / "other.txt").read_text(encoding="utf-8") == "not yours"
    registered = await mgr._registered(repo)
    assert any(p.name == "fgtheirs1" for p in registered)
    assert "fgtheirs1" in await mgr._ref_ids(repo)


async def test_round14_expiry_cancellation_recovery_and_edge_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify shutdown cancellation during expire_tree recovers via pre-delete intent, archive/recursion guards, and orphan partial bytes."""
    import asyncio
    import shutil
    from nekopower.host.worktrees import Worktree

    clock = Clock(1_800_000_000.0)
    host1 = make_host(tmp_path)
    host1.storage._clock = clock
    try:
        old_ts = clock.now - 30 * DAY

        # 1. Shutdown CancelledError right after deletion finishes inside remove_if_untouched (before post-delete
        #    code runs): pre-delete "expiring" intent survives into a fresh host instance and cancels the blocked task.
        sess = await host1.ledger.create_session(name="cancel-exp", lanlan_name="Lan", project="cproj")
        comm, _ = await host1.ledger.create(
            lanlan_name="Lan",
            title="Blocked during shutdown",
            dispatch_text="work",
            source="dispatch",
            project="cproj",
            session_id=sess.id,
        )
        wt_cancel = host1.paths.worktree_dir("cncexp01")
        wt_cancel.mkdir(parents=True)
        await host1.ledger.update(comm.id, workdir=str(wt_cancel))
        await host1.ledger.transition(comm.id, "working")
        await host1.ledger.transition(comm.id, "blocked", reason="waiting for landing")
        await host1.ledger.add_tree(
            id="cncexp01",
            session_id=sess.id,
            commission_id=comm.id,
            lanlan_name="Lan",
            project="cproj",
            project_dir=str(tmp_path / "gone_repo"),
            repo_root=str(tmp_path / "gone_repo"),
            path=str(wt_cancel),
            workdir=str(wt_cancel),
            state="held",
        )
        await host1.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "cncexp01"))
        )

        async def open_cancel(_pdir: Any, _wt: Any) -> Worktree:
            return Worktree(id="cncexp01", repo_root=tmp_path / "gone_repo", path=wt_cancel, subpath="", start="abc")

        async def delete_then_cancel(handle: Worktree, _cb: Any = None) -> str:
            shutil.rmtree(handle.path)
            raise asyncio.CancelledError()

        with monkeypatch.context() as mp:
            mp.setattr(host1.task_agents.worktrees, "open", open_cancel)
            mp.setattr(host1.task_agents.worktrees, "remove_if_untouched", delete_then_cancel)
            with pytest.raises(asyncio.CancelledError):
                await host1.task_agents.expire_tree("cncexp01", until=clock.now - 14 * DAY)
        assert not wt_cancel.exists()
        assert (await host1.ledger.get(comm.id)).status == "blocked"
        assert (await host1.ledger.get_tree("cncexp01")) is not None

        # 2. Aborted freshness check clears "expiring" intent and keeps blocked commission & worktree intact.
        comm2, _ = await host1.ledger.create(
            lanlan_name="Lan",
            title="Blocked but fresh",
            dispatch_text="work",
            source="dispatch",
            project="cproj",
            session_id=sess.id,
        )
        wt_abort = host1.paths.worktree_dir("abtexp01")
        wt_abort.mkdir(parents=True)
        (wt_abort / "keep.txt").write_text("fresh", encoding="utf-8")
        await host1.ledger.update(comm2.id, workdir=str(wt_abort))
        await host1.ledger.transition(comm2.id, "working")
        await host1.ledger.transition(comm2.id, "blocked", reason="waiting")
        await host1.ledger.add_tree(
            id="abtexp01",
            session_id=sess.id,
            commission_id=comm2.id,
            lanlan_name="Lan",
            project="cproj",
            project_dir=str(tmp_path / "gone_repo"),
            repo_root=str(tmp_path / "gone_repo"),
            path=str(wt_abort),
            workdir=str(wt_abort),
            state="held",
        )
        await host1.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "abtexp01"))
        )

        async def open_abort(_pdir: Any, _wt: Any) -> Worktree:
            return Worktree(id="abtexp01", repo_root=tmp_path / "gone_repo", path=wt_abort, subpath="", start="abc")

        async def abort_remove(_handle: Worktree, _cb: Any = None) -> str:
            return "aborted"

        with monkeypatch.context() as mp:
            mp.setattr(host1.task_agents.worktrees, "open", open_abort)
            mp.setattr(host1.task_agents.worktrees, "remove_if_untouched", abort_remove)
            res_abort = await host1.task_agents.expire_tree("abtexp01", until=clock.now - 14 * DAY)
        assert res_abort["expired"] is False
        assert "abtexp01" not in await host1.task_agents._read_pending_expired_trees()

        # 3. Unwritable space state stops deletion before touching disk.
        async def fail_write_state(**_kwargs: Any) -> None:
            raise OSError("disk read-only")

        with monkeypatch.context() as mp:
            mp.setattr(host1.task_agents.worktrees, "open", open_abort)
            mp.setattr(host1.space, "write_state", fail_write_state)
            res_ro = await host1.task_agents.expire_tree("abtexp01", until=clock.now - 14 * DAY)
        assert res_ro == {"expired": False, "reason": "state_unwritable"}
        assert wt_abort.exists()

        # 4. Partial orphan worktree cleanup reports freed_bytes and failed=not_removed (never deferred), and preserves 0.
        orph1 = host1.paths.worktree_dir("orphpart1")
        orph1.mkdir(parents=True)
        (orph1 / "a.bin").write_bytes(b"a" * 300)
        orph2 = host1.paths.worktree_dir("orphzero1")
        orph2.mkdir(parents=True)
        (orph2 / "b.bin").write_bytes(b"b" * 400)

        async def fake_remove_orphan(tid: str) -> dict[str, Any]:
            if tid == "orphpart1":
                return {"removed": False, "freed_bytes": 123}
            return {"removed": True, "freed_bytes": 0}

        with monkeypatch.context() as mp:
            mp.setattr(host1.task_agents, "remove_orphan_worktree", fake_remove_orphan)
            res_orph = await host1.clean_worktrees(["orphpart1", "orphzero1"])
        assert res_orph["deferred"] == []
        assert res_orph["failed"] == [{"id": "orphpart1", "code": "not_removed"}]
        assert res_orph["cleaned"] == ["orphzero1"]
        assert res_orph["freed_bytes"] == 123

        # 5. Deeply nested project.json (RecursionError) fails closed without crashing overview; corrupt archived
        #    project.json (OverflowError) is skipped by storage overview but STILL blocks open_project() overwrite.
        deep_proj = host1.paths.space().project_dir("deepproj")
        deep_proj.mkdir(parents=True)
        (deep_proj / "project.json").write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
        view_deep = await host1.storage_overview()
        assert "categories" in view_deep
        shutil.rmtree(deep_proj)

        bad_arch = host1.paths.space().archived_dir("2025", "dup_guard")
        bad_arch.mkdir(parents=True)
        (bad_arch / "project.json").write_text(
            '{"name": "dup_guard", "commissions": ' + ("9" * 401) + "}",
            encoding="utf-8",
        )
        view_bad_arch = await host1.storage_overview()
        assert "categories" in view_bad_arch
        with pytest.raises(OverflowError):
            await host1.space.open_project("dup_guard")
        assert not host1.paths.space().project_dir("dup_guard").exists()
    finally:
        await host1.shutdown()

    # Fresh host instance on the same data root recovers "cncexp01" and cancels its blocked commission.
    host2 = make_host(tmp_path)
    try:
        rec = await host2.task_agents.reconcile_trees()
        assert "cncexp01" in rec["removed"]
        assert (await host2.ledger.get_tree("cncexp01")) is None
        assert (await host2.ledger.get(comm.id)).status == "cancelled"
        assert (await host2.ledger.get(comm2.id)).status == "blocked"
        assert (await host2.ledger.get_tree("abtexp01")) is not None
    finally:
        await host2.shutdown()


async def test_round15_review_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Round 15 fixes: _measure onerror, skip_corrupt archives, iterative remove_dir_safely,
    storage.json RecursionError, task_agents link guard, repo_root token check, concurrent archive
    revive vs cleanup coordination, and reconcile_trees pending-marker clearing under _trees_in_hand.
    """
    import asyncio
    import os
    from app.agent_server.api_console import _measure
    from nekopower.host.storage import StorageSettingsStore, remove_dir_safely
    from nekopower.host.worktrees import Worktree, WorktreeError

    # 1. _measure() marks whole=False on os.walk traversal errors while preserving counted bytes.
    walk_root = tmp_path / "walk_err"
    walk_root.mkdir()
    (walk_root / "ok.bin").write_bytes(b"x" * 77)
    real_walk = os.walk

    def walk_with_error(top: Any, **kwargs: Any):
        onerror = kwargs.get("onerror")
        if onerror is not None:
            onerror(PermissionError("denied"))
        yield from real_walk(top, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(os, "walk", walk_with_error)
        [(size, whole)] = _measure([walk_root], 5.0)
    assert size == 77
    assert whole is False

    # 2. remove_dir_safely() deletes deep directory trees iteratively even below a low recursion limit
    #    where a naive recursive deletion would raise RecursionError.
    import subprocess
    import sys

    deep_root = tmp_path / "deep_root"
    deep_target = deep_root / "work"
    probe_script = (
        "import os, sys; from pathlib import Path; "
        "from nekopower.host.storage import _fs, remove_dir_safely; "
        "root, target = Path(sys.argv[1]), Path(sys.argv[2]); "
        "cur = target\n"
        "for _ in range(120):\n"
        "    cur = cur / 'a'\n"
        "os.makedirs(_fs(cur), exist_ok=True)\n"
        "with open(_fs(cur / 'leaf.bin'), 'wb') as f:\n"
        "    f.write(b'y' * 25)\n"
        "sys.setrecursionlimit(80)\n"
        "removed, freed = remove_dir_safely(target, root)\n"
        "assert removed is True and freed == 25 and not target.exists(), (removed, freed)\n"
    )
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", probe_script, str(deep_root), str(deep_target)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    # 3. StorageSettingsStore fails closed on RecursionError (deeply nested JSON) without caching or overwriting.
    deep_settings_path = tmp_path / "deep_storage.json"
    deep_settings_path.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
    store = StorageSettingsStore(deep_settings_path)
    vals = await store.get()
    assert vals["auto_clean_work_dirs"] is False
    assert vals["auto_clean_worktrees"] is False
    with pytest.raises(OSError, match="unreadable"):
        await store.put(auto_clean_work_dirs=True)
    assert deep_settings_path.read_text(encoding="utf-8").startswith("[[")
    deep_settings_path.write_text('{"auto_clean_work_dirs": true, "work_dir_kept_days": 10}', encoding="utf-8")
    recovered = await store.get()
    assert recovered["auto_clean_work_dirs"] is True
    assert recovered["work_dir_kept_days"] == 10

    host = make_host(tmp_path)
    try:
        # 4. _archived_projects(skip_corrupt=True) skips invalid JSON archive records while preserving
        #    missing-file archive dirs and keeping legacy list_projects() placeholder behavior intact.
        corrupt_arch = host.paths.space().archived_dir("2025", "bad_json_arch")
        corrupt_arch.mkdir(parents=True)
        (corrupt_arch / "project.json").write_text("{not valid json", encoding="utf-8")
        nofile_arch = host.paths.space().archived_dir("2025", "nofile_arch")
        nofile_arch.mkdir(parents=True)

        checked_list, ok = await host.space.list_projects_checked(include_archived=True)
        assert ok is True
        checked_names = {p.name for p in checked_list}
        assert "bad_json_arch" not in checked_names
        assert "nofile_arch" in checked_names

        legacy_list = await host.space.list_projects(include_archived=True)
        legacy_names = {p.name for p in legacy_list}
        assert "bad_json_arch" in legacy_names
        assert "nofile_arch" in legacy_names

        # 5. _handle_for_cleanup() rejects repo_root fallback when ledger token is empty or mismatched.
        r_root = tmp_path / "tok_repo"
        r_sub = r_root / "sub"
        r_root.mkdir()
        wt_tok = host.paths.worktree_dir("tokchk01")
        wt_tok.mkdir(parents=True)
        await host.ledger.add_tree(
            id="tokchk01",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="tokproj",
            project_dir=str(r_sub),
            repo_root=str(r_root),
            path=str(wt_tok),
            workdir=str(wt_tok / "sub"),
            token="expected-token",
            state="kept",
        )
        row_tok = await host.ledger.get_tree("tokchk01")
        assert row_tok is not None

        async def open_wrong_tok(pdir: Any, _wt: Any) -> Worktree:
            if Path(pdir) == r_root:
                return Worktree(
                    id="tokchk01", repo_root=r_root, path=wt_tok, subpath="", start="abc", token="other-token"
                )
            raise WorktreeError("subdir missing")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents.worktrees, "open", open_wrong_tok)
            assert await host.task_agents._handle_for_cleanup(row_tok) is None

        # Regular _handle_of() path also rejects mismatched non-empty ledger tokens and does not cache.
        async def open_regular_wrong_tok(_pdir: Any, _wt: Any) -> Worktree:
            return Worktree(
                id="tokchk01", repo_root=r_root, path=wt_tok, subpath="", start="abc", token="other-token"
            )

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents.worktrees, "open", open_regular_wrong_tok)
            assert await host.task_agents._handle_of(row_tok) is None
            assert "tokchk01" not in host.task_agents._tree_handles

        await host.ledger.update_tree("tokchk01", token="")
        row_empty_tok = await host.ledger.get_tree("tokchk01")
        assert row_empty_tok is not None

        async def open_empty_tok(pdir: Any, _wt: Any) -> Worktree:
            if Path(pdir) == r_root:
                return Worktree(id="tokchk01", repo_root=r_root, path=wt_tok, subpath="", start="abc", token="")
            raise WorktreeError("subdir missing")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents.worktrees, "open", open_empty_tok)
            assert await host.task_agents._handle_for_cleanup(row_empty_tok) is None

        # 6. Concurrent archive revival waits while target workdir is held for cleanup and never races deletion.
        sess = await host.ledger.create_session(name="rev-s", lanlan_name="Lan", project="revproj")
        target_work = host.paths.work_dir(sess.id, "revproj")
        target_work.mkdir(parents=True)
        (target_work / "keep.txt").write_text("important", encoding="utf-8")
        await host.space.touch_project("revproj", workdir=str(target_work), count=False)
        assert await host.space.archive_project("revproj") is not None

        hold_key = host.task_agents.hold_workdir(target_work)
        assert hold_key is not None
        revive_task = asyncio.create_task(host.space.open_project("revproj"))
        await asyncio.sleep(0.05)
        assert not revive_task.done()
        assert not host.paths.space().project_dir("revproj").exists()
        host.task_agents.release_workdir(hold_key)
        revived = await asyncio.wait_for(revive_task, timeout=2.0)
        assert revived is not None
        assert host.paths.space().project_dir("revproj").is_dir()

        # Once revived, cleaning target_work must refuse as in_use and keep files intact.
        res_clean = await host.clean_work_dirs([f"session:revproj/{sess.id}"])
        assert res_clean["cleaned"] == []
        assert res_clean["failed"] == [{"id": f"session:revproj/{sess.id}", "code": "in_use"}]
        assert (target_work / "keep.txt").read_text(encoding="utf-8") == "important"

        # 7. _reconcile_trees_now() clears pending_expired_trees while holding _trees_in_hand,
        #    never wiping a fresh "expiring" marker written after release.
        wt_rec2 = host.paths.worktree_dir("recrace1")
        wt_rec2.mkdir(parents=True)
        await host.ledger.add_tree(
            id="recrace1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="tokproj",
            project_dir=str(r_root),
            repo_root=str(r_root),
            path=str(wt_rec2),
            workdir=str(wt_rec2),
            token="tok-rec",
            state="kept",
        )
        await host.task_agents._record_pending_expired_tree("recrace1", "expiring")
        await host.task_agents._record_pending_expired_tree("stale_gone_id", "removed")

        cleared_while_held: list[bool] = []
        orig_clear = host.task_agents._clear_pending_expired_trees

        async def spy_clear(tree_ids: Any, *, expected: Any = None) -> None:
            if "recrace1" in tree_ids:
                cleared_while_held.append("recrace1" in host.task_agents._trees_in_hand)
            await orig_clear(tree_ids, expected=expected)

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_clear_pending_expired_trees", spy_clear)
            await host.task_agents._reconcile_trees_now()
        assert cleared_while_held == [True]
        remaining_pending = await host.task_agents._read_pending_expired_trees()
        assert "recrace1" not in remaining_pending
        assert "stale_gone_id" not in remaining_pending

        # 8. resolve_open_path("place", "task_agents") rejects symlinked task_agents directory.
        ta_dir = host.paths.root() / "task_agents"
        ext_dir = tmp_path / "external_ta"
        ext_dir.mkdir()
        if ta_dir.exists():
            import shutil

            shutil.rmtree(ta_dir)
        try:
            ta_dir.symlink_to(ext_dir, target_is_directory=True)
        except OSError:
            pass
        else:
            with pytest.raises(ValueError, match="bad_place"):
                await host.resolve_storage_open_path("place", "task_agents")
    finally:
        await host.shutdown()


async def test_round16_codex_and_greptile_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify nested-repo worktree dependency protection across expiry/discard/retry/overview and
    that session work dirs remain listed after scan budget expiry.
    """
    from config import nekopower_settings as np_settings

    clock = Clock(1_800_000_000.0)
    host = make_host(tmp_path)
    host.storage._clock = clock
    try:
        old_ts = clock.now - 30 * DAY

        # 1. Parent worktree contains a nested repository backing another retained worktree.
        parent_wt = host.paths.worktree_dir("parentwt1")
        nested_repo = parent_wt / "nested_repo"
        nested_repo.mkdir(parents=True)
        child_wt = host.paths.worktree_dir("childwt01")
        child_wt.mkdir(parents=True)
        await host.ledger.add_tree(
            id="parentwt1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="outer",
            project_dir=str(tmp_path / "outer_repo"),
            repo_root=str(tmp_path / "outer_repo"),
            path=str(parent_wt),
            workdir=str(parent_wt),
            token="tok-parent",
            state="kept",
        )
        await host.ledger.add_tree(
            id="childwt01",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="inner",
            project_dir=str(nested_repo),
            repo_root=str(nested_repo),
            path=str(child_wt),
            workdir=str(child_wt),
            token="tok-child",
            state="kept",
        )
        await host.ledger.run(
            lambda conn: conn.execute("UPDATE worktrees SET updated_at = ? WHERE id = ?", (old_ts, "parentwt1"))
        )

        # Overview marks parentwt1 non-cleanable while childwt01 depends on nested_repo.
        ov = await host.storage_overview()
        by_id = {item["id"]: item for item in ov["worktrees"]}
        assert by_id["parentwt1"]["can_clean"] is False
        assert by_id["childwt01"]["can_clean"] is True

        # Auto-expiry and manual discard both refuse while childwt01 is retained.
        exp_res = await host.task_agents.expire_tree("parentwt1", until=clock.now - 14 * DAY)
        assert exp_res == {"expired": False, "reason": "in_use"}
        with pytest.raises(ValueError, match="at_work"):
            await host.task_agents.discard_tree("parentwt1")
        assert nested_repo.is_dir()

        # Even if parentwt1 is in discarding/expired retry state, retry_discarded_tree refuses.
        await host.ledger.update_tree("parentwt1", state="discarding", reason="expired")
        assert await host.task_agents.retry_discarded_tree("parentwt1") is False
        assert nested_repo.is_dir()

        # Once the dependent worktree is dropped, parentwt1 is no longer blocked by tree dependency.
        await host.ledger.drop_tree("childwt01")
        assert (
            await host.task_agents._workdir_referenced_actively(parent_wt, exclude_tree_id="parentwt1")
            is False
        )

        # 2. Session work dirs AND expired transcripts / outputs counts stay visible even when scan budget is expired.
        s1 = await host.ledger.create_session(name="bud-1", lanlan_name="Lan", project="budproj")
        s2 = await host.ledger.create_session(name="bud-2", lanlan_name="Lan", project="budproj")
        old_transcript_ts = clock.now - 90 * DAY
        for sid in (s1.id, s2.id):
            sdir = host.paths.session_dir(sid, "budproj")
            wdir = host.paths.work_dir(sid, "budproj")
            wdir.mkdir(parents=True)
            (wdir / "f.bin").write_bytes(b"z" * 16)
            (sdir / "transcript.jsonl").write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")
            out_d = sdir / "outputs"
            out_d.mkdir(parents=True)
            (out_d / "out.txt").write_bytes(b"o" * 32)
            await host.ledger.update_session(sid, session_dir=str(sdir), workdir=str(wdir))
            await host.ledger.run(
                lambda conn, _sid=sid, _ts=old_transcript_ts: conn.execute(
                    "UPDATE sessions SET last_active_at = ? WHERE id = ?", (_ts, _sid)
                )
            )

        with monkeypatch.context() as mp:
            mp.setattr(np_settings, "STORAGE_SCAN_BUDGET_SECONDS", -1.0)
            ov_over = await host.storage_overview()
        listed_ids = {item["id"] for item in ov_over["work_dirs"]}
        assert f"session:budproj/{s1.id}" in listed_ids
        assert f"session:budproj/{s2.id}" in listed_ids
        for item in ov_over["work_dirs"]:
            if item["project"] == "budproj":
                assert item["bytes_whole"] is False
                assert item["can_clean"] is True
                assert item["empty"] is False
        assert ov_over["transcripts"]["expired_count"] >= 2
        assert ov_over["transcripts"]["outputs_count"] >= 2
        assert ov_over["transcripts"]["bytes_whole"] is False

        # 3. _record_pending_expired_tree() uses strict reads by default so read failures never overwrite existing markers.
        assert await host.task_agents._record_pending_expired_tree("keep_other", "expiring") is True

        async def fail_read_state() -> dict[str, Any]:
            raise OSError("transient read error")

        with monkeypatch.context() as mp:
            mp.setattr(host.space, "read_state", fail_read_state)
            assert await host.task_agents._record_pending_expired_tree("new_tree", "removed") is False
        persisted = await host.task_agents._read_pending_expired_trees()
        assert persisted.get("keep_other") == "expiring"
        assert "new_tree" not in persisted
    finally:
        await host.shutdown()


async def test_round19_review_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Round 19 review fixes: NaN/inf cutoff guard, root-switch settings cache,
    active-commission session workdir protection, unsafe worktree bytes_whole, mid-restore
    workdir edit rollback, orphan cleanup vs _make_tree_now reservation & identity checks,
    bounded regular .git reader, strict reconcile pending-expiry read & restored-dir guard,
    and _schedule_idle_compact waiting for transcript-preserving file holds.
    """
    import asyncio
    import json
    import shutil

    from nekopower.host import space as space_facade
    from nekopower.host.storage import _confirmed_cutoff
    from nekopower.host.tree_work import _repo_from_worktree_gitfile
    from nekopower.host.worktrees import Probe, Worktree

    # 1. _confirmed_cutoff rejects NaN and infinite values.
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert _confirmed_cutoff({"u": bad, "s": 100.0}, 10000.0, 5000.0, until_key="u", seen_key="s") == (None, True)
        assert _confirmed_cutoff({"u": 500.0, "s": bad}, 10000.0, 5000.0, until_key="u", seen_key="s") == (None, True)

    # 2. StorageSettingsStore binds cache to current storage.json path across data-root switches.
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "storage.json").write_text('{"auto_clean_work_dirs": true, "work_dir_kept_days": 7}', encoding="utf-8")
    (root_b / "storage.json").write_text('{"auto_clean_work_dirs": false, "work_dir_kept_days": 30}', encoding="utf-8")
    active_root = [root_a]
    store = StorageSettingsStore(lambda: active_root[0] / "storage.json")
    vals_a = await store.get()
    assert vals_a["auto_clean_work_dirs"] is True
    assert store.allows_auto_clean("work_dirs", 7) is True
    active_root[0] = root_b
    assert store.allows_auto_clean("work_dirs", 7) is False
    vals_b = await store.get()
    assert vals_b["auto_clean_work_dirs"] is False
    assert store.allows_auto_clean("work_dirs", 30) is False

    # 3. _repo_from_worktree_gitfile rejects directories, symlinks, and oversized .git files.
    wt_dir = tmp_path / "gitfile_check"
    wt_dir.mkdir()
    (wt_dir / ".git").mkdir()
    assert _repo_from_worktree_gitfile(wt_dir) is None
    (wt_dir / ".git").rmdir()
    (wt_dir / ".git").write_bytes(b"gitdir: " + b"x" * 70000)
    assert _repo_from_worktree_gitfile(wt_dir) is None
    fake_repo = tmp_path / "real_repo"
    (wt_dir / ".git").write_text(f"gitdir: {fake_repo}/.git/worktrees/wt1\n", encoding="utf-8")
    assert _repo_from_worktree_gitfile(wt_dir) == fake_repo

    host = make_host(tmp_path)
    try:
        # 4. Active blocked commission protects its session's workdir even when commission.workdir is None.
        outer_s = await host.ledger.create_session(name="outer", lanlan_name="Lan", project="nest")
        outer_work = host.paths.work_dir(outer_s.id, "nest")
        nested_sub = outer_work / "sub"
        nested_sub.mkdir(parents=True)
        (nested_sub / "keep.txt").write_text("alive", encoding="utf-8")
        await host.ledger.update_session(outer_s.id, status="idle", workdir=str(outer_work))
        blocked_s = await host.ledger.create_session(name="inner", lanlan_name="Lan", project="nest")
        blocked_c, _ = await host.ledger.create(
            dispatch_text="waiting",
            title="waiting",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="nest",
            session_id=blocked_s.id,
            workdir=None,
        )
        await host.ledger.transition(blocked_c.id, "working")
        await host.ledger.transition(blocked_c.id, "blocked")
        await host.ledger.update_session(blocked_s.id, status="idle", workdir=str(nested_sub))
        assert (await host.ledger.get(blocked_c.id)).status == "blocked"
        assert (await host.ledger.get_session(blocked_s.id)).status == "idle"
        clean_res = await host.clean_work_dirs([f"session:nest/{outer_s.id}"])
        assert clean_res["cleaned"] == []
        assert clean_res["failed"] == [{"id": f"session:nest/{outer_s.id}", "code": "in_use"}]
        assert (nested_sub / "keep.txt").exists()

        # 5. Unreadable/unsafe tracked worktree reports bytes_whole=False and can_clean=False in overview.
        outside_wt = tmp_path / "escaped_wt"
        outside_wt.mkdir()
        await host.ledger.add_tree(
            id="unsafe01",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="nest",
            project_dir=str(tmp_path),
            repo_root=str(tmp_path),
            path=str(outside_wt),
            workdir=str(outside_wt),
            token="tok-u",
            state="kept",
        )
        ov_u = await host.storage_overview()
        u_item = next(item for item in ov_u["worktrees"] if item["id"] == "unsafe01")
        assert u_item["exists"] is False
        assert u_item["bytes_whole"] is False
        assert u_item["can_clean"] is False

        # 6. _restore() rolls back move and waits if project.json mutates to clearing workdir B before _move().
        dir_a = tmp_path / "wd_a"
        dir_b = tmp_path / "wd_b"
        dir_a.mkdir()
        dir_b.mkdir()
        await host.space.touch_project("raceproj", workdir=str(dir_a), count=False)
        assert await host.space.archive_project("raceproj") is not None

        hold_b = host.task_agents.hold_workdir(dir_b)
        assert hold_b is not None
        orig_move = space_facade._move
        mutated = [False]
        loop = asyncio.get_running_loop()
        rolled_back = asyncio.Event()

        def mutate_then_move(src: Path, dst: Path) -> None:
            if not mutated[0] and dst.name == "raceproj":
                mutated[0] = True
                pfile = src / "project.json"
                data = json.loads(pfile.read_text(encoding="utf-8"))
                data["workdir"] = str(dir_b)
                pfile.write_text(json.dumps(data), encoding="utf-8")
                orig_move(src, dst)
                return
            orig_move(src, dst)
            if mutated[0] and src.name == "raceproj":
                loop.call_soon_threadsafe(rolled_back.set)

        revive_task: asyncio.Task[Any] | None = None
        try:
            with monkeypatch.context() as mp:
                # Bypass the pre-move global clearing gate so this test exercises the defense-in-depth
                # post-move workdir check and rollback when project.json mutates right before _move().
                mp.setattr(host.space, "_any_workdir_clearing", lambda: None)
                mp.setattr(space_facade, "_move", mutate_then_move)
                revive_task = asyncio.create_task(host.space.open_project("raceproj"))
                await asyncio.wait_for(rolled_back.wait(), timeout=2.0)
                # Rolled back to archive while dir_b is clearing; active dir must not exist yet.
                assert mutated[0] is True
                assert not revive_task.done()
                assert not host.paths.space().project_dir("raceproj").exists()
                host.task_agents.release_workdir(hold_b)
                revived = await asyncio.wait_for(revive_task, timeout=2.0)
        finally:
            host.task_agents.release_workdir(hold_b)
            if revive_task is not None and not revive_task.done():
                await asyncio.wait_for(revive_task, timeout=2.0)
        assert revived is not None
        assert revived.workdir == str(dir_b)
        assert host.paths.space().project_dir("raceproj").is_dir()

        # 7. _reconcile_trees_now aborts on strict read_state OSError and preserves restored dir on "removed" marker.
        gone_wt = host.paths.worktree_dir("recon_err1")
        await host.ledger.add_tree(
            id="recon_err1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="nest",
            project_dir=str(tmp_path),
            repo_root=str(tmp_path),
            path=str(gone_wt),
            workdir=str(gone_wt),
            token="tok-r1",
            state="kept",
        )

        async def fail_read_state() -> dict[str, Any]:
            raise OSError("state unavailable")

        with monkeypatch.context() as mp:
            mp.setattr(host.space, "read_state", fail_read_state)
            await host.task_agents._reconcile_trees_now()
        assert await host.ledger.get_tree("recon_err1") is not None

        restored_wt = host.paths.worktree_dir("recon_rest1")
        restored_wt.mkdir(parents=True)
        await host.ledger.add_tree(
            id="recon_rest1",
            session_id=None,
            commission_id=None,
            lanlan_name="Lan",
            project="nest",
            project_dir=str(tmp_path),
            repo_root=str(tmp_path),
            path=str(restored_wt),
            workdir=str(restored_wt),
            token="tok-r2",
            state="kept",
        )
        assert await host.task_agents._record_pending_expired_tree("recon_rest1", "removed") is True
        await host.task_agents._reconcile_trees_now()
        assert await host.ledger.get_tree("recon_rest1") is not None
        assert (await host.task_agents._read_pending_expired_trees()).get("recon_rest1") == "removed"

        # 8. Orphan cleanup aborts with busy if the target directory is replaced mid-flight,
        #    and _make_tree_now skips names currently reserved in _trees_in_hand.
        orph_swap = host.paths.worktree_dir("orphswap1")
        orph_swap.mkdir(parents=True)
        (orph_swap / "old.txt").write_text("old", encoding="utf-8")
        import nekopower.host.storage as storage_mod

        real_measure = storage_mod.measure_dir
        swapped = [False]

        def swap_during_measure(path: Path, deadline: float, **kwargs: Any) -> Any:
            res = real_measure(path, deadline, **kwargs)
            if not swapped[0] and path == orph_swap:
                swapped[0] = True
                # Rename (do not delete) old dir so both directories coexist with distinct inodes.
                orph_swap.rename(tmp_path / "orph_swap_old")
                orph_swap.mkdir()
                (orph_swap / "fresh.txt").write_text("fresh", encoding="utf-8")
            return res

        with monkeypatch.context() as mp:
            mp.setattr(storage_mod, "measure_dir", swap_during_measure)
            with pytest.raises(ValueError, match="busy"):
                await host.task_agents.remove_orphan_worktree("orphswap1")
        assert (orph_swap / "fresh.txt").exists()

        res_s = await host.ledger.create_session(name="res-s", lanlan_name="Lan", project="nest", workdir=str(tmp_path))
        res_c, _ = await host.ledger.create(
            dispatch_text="res-c",
            title="res-c",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="nest",
            session_id=res_s.id,
        )
        base_name = res_c.id[:12]
        host.task_agents._trees_in_hand.add(base_name)
        try:
            async def fake_probe(_home: Any) -> Probe:
                return Probe(reason=None, repo_root=tmp_path, subpath="")

            async def fake_create(_home: Any, wt_path: Path) -> Worktree:
                wt_path.mkdir(parents=True, exist_ok=True)
                return Worktree(id=wt_path.name, repo_root=tmp_path, path=wt_path, subpath="", start="abc", token="t-res")

            with monkeypatch.context() as mp:
                mp.setattr(host.task_agents.worktrees, "probe", fake_probe)
                mp.setattr(host.task_agents.worktrees, "create", fake_create)
                made_row, err_code, _detail = await host.task_agents._make_tree_now(res_c, res_s, str(tmp_path))
            assert err_code == ""
            assert made_row is not None
            assert made_row.id == f"{base_name}-2"
        finally:
            host.task_agents._trees_in_hand.discard(base_name)

        # 9. _schedule_idle_compact waits for transcript-preserving hold_files(drop_idle_timer=False),
        #    and aborts on transcript-dropping hold_files(drop_idle_timer=True).
        ic_s = await host.ledger.create_session(name="ic-wait", lanlan_name="Lan", project="nest")
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def slow_wanted(_sid: str) -> bool:
            entered.set()
            await gate.wait()
            return True

        async def fast_after(_sid: str) -> float:
            return 60.0

        async def fast_model(_sid: str) -> str:
            return "test://model"

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_idle_compact_wanted", slow_wanted)
            mp.setattr(host.task_agents, "_idle_after", fast_after)
            mp.setattr(host.task_agents, "_task_model_key", fast_model)
            sched_task = asyncio.create_task(host.task_agents._schedule_idle_compact(ic_s.id))
            await entered.wait()
            assert host.task_agents.hold_files(ic_s.id, drop_idle_timer=False) is True
            gate.set()
            await asyncio.sleep(0.05)
            assert not sched_task.done()
            assert ic_s.id not in host.task_agents._idle_timers
            host.task_agents.release_files(ic_s.id)
            await asyncio.wait_for(sched_task, timeout=2.0)
            assert ic_s.id in host.task_agents._idle_timers
            host.task_agents._drop_idle_timer(ic_s.id)

            # Now with drop_idle_timer=True (transcript deletion): timer must NOT be set.
            gate.clear()
            entered.clear()
            sched_task2 = asyncio.create_task(host.task_agents._schedule_idle_compact(ic_s.id))
            await entered.wait()
            assert host.task_agents.hold_files(ic_s.id, drop_idle_timer=True) is True
            gate.set()
            await asyncio.wait_for(sched_task2, timeout=2.0)
            host.task_agents.release_files(ic_s.id)
            assert ic_s.id not in host.task_agents._idle_timers

        # 10. Round 20 fixes: huge-int cutoff OverflowError, _workdir_referenced_actively joining
        #     sessions.workdir, _says_it_is bounded gitfile check, malformed pending_expired_trees
        #     entries skipped without TypeError, and instance-scoped pending markers preventing
        #     stale record_only markers from dropping a reused worktree ID.
        huge = 10**400
        assert _confirmed_cutoff({"u": huge, "s": 100.0}, 10000.0, 5000.0, until_key="u", seen_key="s") == (None, True)
        assert _confirmed_cutoff({"u": 500.0, "s": huge}, 10000.0, 5000.0, until_key="u", seen_key="s") == (None, True)

        from nekopower.host.tree_work import _says_it_is

        bad_git_wt = tmp_path / "bad_git_says"
        bad_git_wt.mkdir()
        valid_git_ptr = f"gitdir: {(tmp_path / '.git' / 'worktrees' / 'bad_git_says').as_posix()}\n"
        (bad_git_wt / ".git").write_text(valid_git_ptr, encoding="utf-8")
        assert _says_it_is(bad_git_wt, "bad_git_says", str(tmp_path)) is True
        (bad_git_wt / ".git").write_text(valid_git_ptr + (" " * 70000), encoding="utf-8")
        assert _says_it_is(bad_git_wt, "bad_git_says", str(tmp_path)) is False

        # Unrelated blocked commission with commissions.workdir=None protects its idle session's workdir inside a worktree.
        wt_ref = host.paths.worktree_dir("wtref01")
        wt_ref_sub = wt_ref / "nested"
        wt_ref_sub.mkdir(parents=True)
        idle_ref_s = await host.ledger.create_session(
            name="idle-ref", lanlan_name="Lan", project="nest", workdir=str(wt_ref_sub)
        )
        ref_comm, _ = await host.ledger.create(
            dispatch_text="blocked-on-wt",
            title="blocked-on-wt",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="nest",
            session_id=idle_ref_s.id,
            workdir=None,
        )
        await host.ledger.transition(ref_comm.id, "working")
        await host.ledger.transition(ref_comm.id, "blocked")
        await host.ledger.update_session(idle_ref_s.id, status="idle")
        assert (await host.ledger.get(ref_comm.id)).status == "blocked"
        assert (await host.ledger.get_session(idle_ref_s.id)).status == "idle"
        assert await host.task_agents._workdir_referenced_actively(wt_ref, ignore_blocked_commission_id="other-c") is True
        assert await host.task_agents._workdir_referenced_actively(wt_ref, ignore_blocked_commission_id=ref_comm.id) is False

        # Malformed list value in pending_expired_trees is skipped without raising TypeError.
        await host.space.write_state(
            pending_expired_trees={"bad_arr": ["removed"], "good_def": "deferred", "bad_int": 123}
        )
        parsed_pending = await host.task_agents._read_pending_expired_trees(raise_on_error=True)
        assert parsed_pending == {"good_def": "deferred"}

        # Stale record_only marker from an older worktree instance must NOT drop or cancel a reused ID/path.
        reused_wt = host.paths.worktree_dir("reused01")
        reused_wt.mkdir(parents=True)
        new_comm, _ = await host.ledger.create(
            dispatch_text="retry-work",
            title="retry-work",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="nest",
            session_id=res_s.id,
            workdir=str(reused_wt),
        )
        await host.ledger.transition(new_comm.id, "working")
        await host.ledger.transition(new_comm.id, "blocked")
        assert (await host.ledger.get(new_comm.id)).status == "blocked"
        new_row = await host.ledger.add_tree(
            id="reused01",
            session_id=res_s.id,
            commission_id=new_comm.id,
            lanlan_name="Lan",
            project="nest",
            project_dir=str(tmp_path),
            repo_root=str(tmp_path),
            path=str(reused_wt),
            workdir=str(reused_wt),
            token="tok-new-instance",
            state="held",
        )
        await host.space.write_state(
            pending_expired_trees={
                "reused01": {
                    "outcome": "record_only",
                    "token": "tok-old-instance",
                    "created_at": new_row.created_at - 100.0,
                }
            }
        )
        await host.task_agents._reconcile_trees_now()
        assert await host.ledger.get_tree("reused01") is not None
        assert (await host.ledger.get(new_comm.id)).status == "blocked"
        assert "reused01" not in await host.task_agents._read_pending_expired_trees()

        # Matching instance marker DOES still settle record_only recovery properly.
        await host.space.write_state(
            pending_expired_trees={
                "reused01": {
                    "outcome": "record_only",
                    "token": new_row.token,
                    "created_at": float(new_row.created_at),
                }
            }
        )
        await host.task_agents._reconcile_trees_now()
        assert await host.ledger.get_tree("reused01") is None
        assert (await host.ledger.get(new_comm.id)).status == "cancelled"
        assert "reused01" not in await host.task_agents._read_pending_expired_trees()
    finally:
        await host.shutdown()


async def test_round21_review_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Round 21 fixes: symlink/realpath alias matching across live reference checks
    and workdir holds, settings cache invalidation on unreadable put(), strict dict
    marker validation, late pending-expiry re-read before reconcile drop_tree(), and
    both _restore() failure branches vetoing active workdir deletion.
    """
    import asyncio
    import json

    from nekopower.host import space as space_facade

    # 1. StorageSettingsStore._put() invalidates stale cache when storage.json becomes unreadable.
    sfile = tmp_path / "storage.json"
    sfile.write_text('{"auto_clean_work_dirs": true, "work_dir_kept_days": 7}', encoding="utf-8")
    store = StorageSettingsStore(sfile)
    vals = await store.get()
    assert vals["auto_clean_work_dirs"] is True
    assert store.allows_auto_clean("work_dirs", 7) is True
    sfile.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(OSError, match="unreadable"):
        await store.put(work_dir_kept_days=14)
    assert store.allows_auto_clean("work_dirs", 7) is False
    failed_vals = await store.get()
    assert failed_vals["auto_clean_work_dirs"] is False
    sfile.write_text('{"auto_clean_work_dirs": true, "work_dir_kept_days": 14}', encoding="utf-8")
    recovered = await store.get()
    assert recovered["work_dir_kept_days"] == 14
    assert store.allows_auto_clean("work_dirs", 14) is True

    # 2. Dict-format pending_expired_trees markers require valid token AND created_at.
    from nekopower.host.tree_work import TreeWork

    assert TreeWork._normalize_pending_marker({"outcome": "record_only"}) is None
    assert TreeWork._normalize_pending_marker({"outcome": "record_only", "token": 123, "created_at": 1.0}) is None
    assert TreeWork._normalize_pending_marker({"outcome": "record_only", "token": "t", "created_at": "bad"}) is None
    assert TreeWork._normalize_pending_marker({"outcome": "record_only", "token": "t", "created_at": float("nan")}) is None
    assert TreeWork._normalize_pending_marker("record_only") == "record_only"
    assert TreeWork._normalize_pending_marker({"outcome": "record_only", "token": "t", "created_at": 42.0}) == {
        "outcome": "record_only",
        "token": "t",
        "created_at": 42.0,
    }

    host = make_host(tmp_path)
    try:
        # 3. Symlink/realpath alias matching in _workdir_referenced_actively and hold_workdir/_claim_workdir_for_start.
        alias_root = tmp_path / "alias_root"
        real_root = tmp_path / "real_root"
        real_wt = real_root / "wt1"
        real_wt.mkdir(parents=True)
        alias_wt = alias_root / "wt1"
        real_realpath = os.path.realpath

        def fake_realpath(p: Any) -> str:
            s = str(p)
            norm_s = os.path.normcase(os.path.normpath(s))
            norm_alias = os.path.normcase(os.path.normpath(str(alias_root)))
            if norm_s == norm_alias or norm_s.startswith(norm_alias + os.sep):
                return str(real_root) + s[len(str(alias_root)) :]
            return real_realpath(s)

        fork_s = await host.ledger.create_session(
            name="alias-fork", lanlan_name="Lan", project="aliasp", workdir=str(real_wt)
        )
        await host.ledger.update_session(fork_s.id, status="running")
        with monkeypatch.context() as mp:
            mp.setattr(os.path, "realpath", fake_realpath)
            assert await host.task_agents._workdir_referenced_actively(alias_wt) is True
            hold_k = host.task_agents.hold_workdir(alias_wt)
            assert hold_k is not None
            try:
                assert host.task_agents.is_workdir_clearing(real_wt) is True
                entered_wait = asyncio.Event()

                class _SignalWaitEvent(asyncio.Event):
                    async def wait(self) -> bool:
                        entered_wait.set()
                        return await super().wait()

                host.task_agents._clearing_workdirs[hold_k].event = _SignalWaitEvent()
                claim_task = asyncio.create_task(host.task_agents._claim_workdir_for_start("c-alias", real_wt))
                await asyncio.wait_for(entered_wait.wait(), timeout=2.0)
                assert not claim_task.done()
                host.task_agents.release_workdir(hold_k)
                assert await asyncio.wait_for(claim_task, timeout=2.0) is False
            finally:
                host.task_agents.release_workdir(hold_k)
        await host.ledger.update_session(fork_s.id, status="idle")

        # 4. _reconcile_trees_now re-reads pending_expired_trees before dropping a missing worktree row
        #    so a concurrent expire_tree() after the initial snapshot still cancels its blocked commission.
        late_wt = host.paths.worktree_dir("lateexp01")
        late_s = await host.ledger.create_session(name="late-s", lanlan_name="Lan", project="latep")
        late_c, _ = await host.ledger.create(
            dispatch_text="late-c",
            title="late-c",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="latep",
            session_id=late_s.id,
            workdir=str(late_wt),
        )
        await host.ledger.transition(late_c.id, "working")
        await host.ledger.transition(late_c.id, "blocked")
        late_row = await host.ledger.add_tree(
            id="lateexp01",
            session_id=late_s.id,
            commission_id=late_c.id,
            lanlan_name="Lan",
            project="latep",
            project_dir=str(tmp_path),
            repo_root=str(tmp_path),
            path=str(late_wt),
            workdir=str(late_wt),
            token="tok-late",
            state="held",
        )
        orig_handle_of = host.task_agents._handle_of

        async def write_marker_mid_reconcile(row: Any) -> Any:
            if row.id == "lateexp01":
                await host.task_agents._record_pending_expired_tree("lateexp01", "removed", row=late_row)
                return None
            return await orig_handle_of(row)

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_handle_of", write_marker_mid_reconcile)
            await host.task_agents._reconcile_trees_now()
        assert await host.ledger.get_tree("lateexp01") is None
        assert (await host.ledger.get(late_c.id)).status == "cancelled"
        assert "lateexp01" not in await host.task_agents._read_pending_expired_trees()

        # 5. _restore() failure branches:
        #    (a) post-move _project_to_change() OSError while cleanup is active rolls back & waits;
        #        if rollback also fails, vetoes cleanup so StorageManager aborts with "in_use".
        #    (b) rollback OSError + _put_project() OSError vetoes cleanup so StorageManager aborts with "in_use".
        wd_s1 = await host.ledger.create_session(name="veto-s1", lanlan_name="Lan", project="vetop")
        wd_dir1 = host.paths.work_dir(wd_s1.id, "vetop")
        wd_dir1.mkdir(parents=True)
        (wd_dir1 / "keep1.txt").write_text("must-keep-1", encoding="utf-8")
        await host.ledger.update_session(wd_s1.id, status="idle", workdir=str(wd_dir1))

        await host.space.touch_project("failreadp", workdir=str(tmp_path / "other1"), count=False)
        assert await host.space.archive_project("failreadp") is not None

        orig_move = space_facade._move
        orig_to_change = host.space._project_to_change

        def fail_read_and_rollback_move(src: Path, dst: Path) -> None:
            if src.name == "failreadp" and src.parent.name == "projects":
                raise OSError("rollback move failed")
            orig_move(src, dst)

        def fail_to_change(directory: Path) -> Any:
            if directory.name == "failreadp":
                raise OSError("post-move unreadable")
            return orig_to_change(directory)

        # Simulate cleanup holding wd_dir1 while _restore bypasses pre-move wait (testing the
        # defense-in-depth post-move OSError + rollback OSError veto branch).
        orig_busy = host.storage._busy_workdirs
        orig_any_clearing = host.space._any_workdir_clearing
        injected_a = [False]

        async def busy_then_restore_a(*args: Any, **kwargs: Any) -> Any:
            res = await orig_busy(*args, **kwargs)
            if not injected_a[0]:
                injected_a[0] = True
                any_calls = [0]

                def any_clearing_after_move() -> str | None:
                    any_calls[0] += 1
                    return None if any_calls[0] == 1 else (orig_any_clearing() if orig_any_clearing else None)

                with monkeypatch.context() as mp2:
                    mp2.setattr(host.space, "_any_workdir_clearing", any_clearing_after_move)
                    mp2.setattr(space_facade, "_move", fail_read_and_rollback_move)
                    mp2.setattr(host.space, "_project_to_change", fail_to_change)
                    with pytest.raises(OSError, match="post-move unreadable"):
                        await host.space.open_project("failreadp")
            return res

        with monkeypatch.context() as mp:
            mp.setattr(host.storage, "_busy_workdirs", busy_then_restore_a)
            clean_a = await host.clean_work_dirs([f"session:vetop/{wd_s1.id}"])
        assert clean_a["cleaned"] == []
        assert clean_a["failed"] == [{"id": f"session:vetop/{wd_s1.id}", "code": "in_use"}]
        assert (wd_dir1 / "keep1.txt").read_text(encoding="utf-8") == "must-keep-1"

        # (b) Mid-restore project.json edit to wd_dir2 + rollback OSError + _put_project OSError -> veto cleanup.
        wd_s2 = await host.ledger.create_session(name="veto-s2", lanlan_name="Lan", project="vetop")
        wd_dir2 = host.paths.work_dir(wd_s2.id, "vetop")
        wd_dir2.mkdir(parents=True)
        (wd_dir2 / "keep2.txt").write_text("must-keep-2", encoding="utf-8")
        await host.ledger.update_session(wd_s2.id, status="idle", workdir=str(wd_dir2))

        await host.space.touch_project("failputp", workdir=str(tmp_path / "other2"), count=False)
        assert await host.space.archive_project("failputp") is not None

        orig_put = host.space._put_project
        mutated_b = [False]

        def mutate_and_fail_rollback(src: Path, dst: Path) -> None:
            if not mutated_b[0] and dst.name == "failputp" and dst.parent.name == "projects":
                mutated_b[0] = True
                pfile = src / "project.json"
                data = json.loads(pfile.read_text(encoding="utf-8"))
                data["workdir"] = str(wd_dir2)
                pfile.write_text(json.dumps(data), encoding="utf-8")
                orig_move(src, dst)
                return
            if mutated_b[0] and src.name == "failputp" and src.parent.name == "projects":
                raise OSError("rollback failed")
            orig_move(src, dst)

        def fail_put(directory: Path, proj: Any) -> None:
            if directory.name == "failputp":
                raise OSError("put failed")
            orig_put(directory, proj)

        injected_b = [False]

        async def busy_then_restore_b(*args: Any, **kwargs: Any) -> Any:
            res = await orig_busy(*args, **kwargs)
            if not injected_b[0]:
                injected_b[0] = True
                with monkeypatch.context() as mp2:
                    mp2.setattr(host.space, "_any_workdir_clearing", lambda: None)
                    mp2.setattr(space_facade, "_move", mutate_and_fail_rollback)
                    mp2.setattr(host.space, "_put_project", fail_put)
                    with pytest.raises(OSError, match="put failed"):
                        await host.space.open_project("failputp")
            return res

        with monkeypatch.context() as mp:
            mp.setattr(host.storage, "_busy_workdirs", busy_then_restore_b)
            clean_b = await host.clean_work_dirs([f"session:vetop/{wd_s2.id}"])
        assert clean_b["cleaned"] == []
        assert clean_b["failed"] == [{"id": f"session:vetop/{wd_s2.id}", "code": "in_use"}]
        assert (wd_dir2 / "keep2.txt").read_text(encoding="utf-8") == "must-keep-2"
    finally:
        await host.shutdown()


async def test_round22_review_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Round 22 fixes: per-hold veto isolation & zero realpath in veto checks, op_lock
    mutual exclusion in remove_dir_safely, pre-move _restore() wait & OverflowError rollback/veto,
    overview() O(N+M) alias pre-resolution, prepare_place() shutdown re-check, and tree_work
    reconcile stale-row re-read / forget_gone reappeared / pre-drop re-check.
    """
    import asyncio
    import json
    import threading

    from nekopower.host import space as space_facade
    from nekopower.host import storage as storage_mod
    from nekopower.host.worktrees import WorktreeError

    host = make_host(tmp_path)
    try:
        # 1. Per-hold veto isolation (subpath veto does not leak to a later hold) and zero realpath on veto check.
        managed = tmp_path / "managed_work"
        sub = managed / "sub"
        sub.mkdir(parents=True)
        hold1 = host.task_agents.hold_workdir(managed)
        assert hold1 is not None
        guard1 = host.task_agents.workdir_hold_guard(hold1)
        assert guard1 is not None
        _lock1, check1 = guard1
        assert check1() is False
        host.task_agents.veto_workdir_clear(sub)

        realpath_calls = [0]
        orig_realpath = os.path.realpath

        def counting_realpath(p: Any) -> str:
            realpath_calls[0] += 1
            return orig_realpath(p)

        with monkeypatch.context() as mp:
            mp.setattr(os.path, "realpath", counting_realpath)
            assert check1() is True
            assert host.task_agents.is_workdir_clear_vetoed(hold1) is True
        assert realpath_calls[0] == 0

        host.task_agents.release_workdir(hold1)
        hold2 = host.task_agents.hold_workdir(managed)
        assert hold2 is not None
        try:
            guard2 = host.task_agents.workdir_hold_guard(hold2)
            assert guard2 is not None
            assert guard2[1]() is False
            assert host.task_agents.is_workdir_clear_vetoed(managed) is False
        finally:
            host.task_agents.release_workdir(hold2)

        # 2. Mutual exclusion between remove_dir_safely() unlink and veto_workdir_clear():
        #    pause remove_dir_safely right after its loop-level should_abort() check (before unlink),
        #    publish veto_workdir_clear(), and confirm the file is NOT deleted.
        race_root = tmp_path / "race_root"
        race_dir = race_root / "work"
        race_dir.mkdir(parents=True)
        protected_file = race_dir / "keep.txt"
        protected_file.write_text("must-survive", encoding="utf-8")

        race_hold = host.task_agents.hold_workdir(race_dir)
        assert race_hold is not None
        try:
            op_lock, veto_check = host.task_agents.workdir_hold_guard(race_hold)  # type: ignore[misc]
            check_count = [0]
            paused_after_check = threading.Event()
            resume_delete = threading.Event()

            def pausing_should_abort() -> bool:
                res = veto_check()
                check_count[0] += 1
                # Calls: 1=pre-loop, 2=while-top, 3=for-entry (right before entry.stat & op_lock unlink).
                if check_count[0] == 3 and not res:
                    paused_after_check.set()
                    resume_delete.wait(timeout=5.0)
                return res

            result_box: list[tuple[bool, int]] = []

            def run_rm() -> None:
                result_box.append(
                    storage_mod.remove_dir_safely(
                        race_dir, race_root, should_abort=pausing_should_abort, op_lock=op_lock
                    )
                )

            worker = threading.Thread(target=run_rm)
            worker.start()
            assert paused_after_check.wait(timeout=5.0)
            host.task_agents.veto_workdir_clear(race_dir)
            resume_delete.set()
            worker.join(timeout=5.0)
            assert not worker.is_alive()
            assert result_box == [(False, 0)]
            assert protected_file.read_text(encoding="utf-8") == "must-survive"
        finally:
            host.task_agents.release_workdir(race_hold)

        # 2b. Independent lock-enforcement test: prove the final should_abort() re-check and
        #     os.unlink() execute inside the exact same op_lock acquisition (removing `with lock_cm:`
        #     while keeping the second should_abort() check fails this test).
        lock_root = tmp_path / "lock_probe_root"
        lock_dir = lock_root / "work"
        lock_dir.mkdir(parents=True)
        (lock_dir / "probe.txt").write_text("payload", encoding="utf-8")

        class _ProbeLock:
            def __init__(self) -> None:
                self._inner = threading.Lock()
                self.held = False
                self.acquire_id = 0

            def __enter__(self) -> "_ProbeLock":
                self._inner.acquire()
                self.held = True
                self.acquire_id += 1
                return self

            def __exit__(self, *args: Any) -> None:
                self.held = False
                self._inner.release()

        probe_lock = _ProbeLock()
        check_events: list[tuple[bool, int]] = []
        unlink_events: list[tuple[bool, int]] = []
        orig_unlink = os.unlink

        def probe_abort() -> bool:
            check_events.append((probe_lock.held, probe_lock.acquire_id))
            return False

        def probe_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
            unlink_events.append((probe_lock.held, probe_lock.acquire_id))
            orig_unlink(path, *args, **kwargs)

        with monkeypatch.context() as mp:
            mp.setattr(os, "unlink", probe_unlink)
            removed_probe, freed_probe = storage_mod.remove_dir_safely(
                lock_dir, lock_root, should_abort=probe_abort, op_lock=probe_lock  # type: ignore[arg-type]
            )
        assert removed_probe is True and freed_probe == len("payload")
        assert len(unlink_events) == 1
        unlink_held, unlink_acq = unlink_events[0]
        assert unlink_held is True
        assert any(held and acq == unlink_acq for held, acq in check_events)

        # 3. _restore():
        #    (a) With normal wiring, _restore() waits BEFORE _move(source, target) while any workdir hold is active.
        await host.space.touch_project("waitp", workdir=str(tmp_path / "wait_other"), count=False)
        assert await host.space.archive_project("waitp") is not None
        gate_dir = tmp_path / "gate_work"
        gate_dir.mkdir()
        gate_hold = host.task_agents.hold_workdir(gate_dir)
        assert gate_hold is not None
        try:
            moved_during_hold = [False]
            orig_move = space_facade._move

            def watch_move(src: Path, dst: Path) -> None:
                if dst.name == "waitp" and host.task_agents.any_workdir_clearing() is not None:
                    moved_during_hold[0] = True
                orig_move(src, dst)

            entered_restore_wait = asyncio.Event()

            class _RestoreWaitEvent(asyncio.Event):
                async def wait(self) -> bool:
                    entered_restore_wait.set()
                    return await super().wait()

            host.task_agents._clearing_workdirs[gate_hold].event = _RestoreWaitEvent()
            with monkeypatch.context() as mp:
                mp.setattr(space_facade, "_move", watch_move)
                restore_task = asyncio.create_task(host.space.open_project("waitp"))
                await asyncio.wait_for(entered_restore_wait.wait(), timeout=2.0)
                assert not host.paths.space().project_dir("waitp").exists()
                host.task_agents.release_workdir(gate_hold)
                restored_p = await asyncio.wait_for(restore_task, timeout=2.0)
            assert restored_p is not None and restored_p.name == "waitp"
            assert moved_during_hold[0] is False
        finally:
            host.task_agents.release_workdir(gate_hold)

        #    (b) Post-move OverflowError (commissions: 1e400) rolls back cleanly (not activated);
        #        if rollback also raises OSError, vetoes active cleanup before raising OverflowError.
        await host.space.touch_project("ovfp", workdir=str(tmp_path / "ovf_other"), count=False)
        assert await host.space.archive_project("ovfp") is not None
        orig_move2 = space_facade._move

        def inject_overflow_on_move(src: Path, dst: Path) -> None:
            orig_move2(src, dst)
            if dst.name == "ovfp" and dst.parent.name == "projects":
                (dst / "project.json").write_text(
                    '{"name": "ovfp", "commissions": ' + ("9" * 401) + "}",
                    encoding="utf-8",
                )

        with monkeypatch.context() as mp:
            mp.setattr(space_facade, "_move", inject_overflow_on_move)
            with pytest.raises(OverflowError):
                await host.space.open_project("ovfp")
        assert not host.paths.space().project_dir("ovfp").exists()
        # Remove the rolled-back corrupt archive so subsequent _archived_of() scans can proceed.
        import shutil as _shutil

        for year_dir in host.paths.space().archive_dir().iterdir():
            ovfp_arch = year_dir / "ovfp"
            if ovfp_arch.exists():
                _shutil.rmtree(ovfp_arch)

        # Re-archive cleanly and force BOTH OverflowError AND rollback OSError while a hold is active.
        await host.space.touch_project("ovfvetop", workdir=str(tmp_path / "ovf_other2"), count=False)
        assert await host.space.archive_project("ovfvetop") is not None
        ovf_work = tmp_path / "ovf_work"
        ovf_work.mkdir()
        ovf_hold = host.task_agents.hold_workdir(ovf_work)
        assert ovf_hold is not None
        try:
            def inject_overflow_and_fail_rollback(src: Path, dst: Path) -> None:
                if src.name == "ovfvetop" and src.parent.name == "projects":
                    raise OSError("rollback blocked")
                orig_move2(src, dst)
                if dst.name == "ovfvetop" and dst.parent.name == "projects":
                    (dst / "project.json").write_text(
                        '{"name": "ovfvetop", "commissions": ' + ("9" * 401) + "}",
                        encoding="utf-8",
                    )

            with monkeypatch.context() as mp:
                mp.setattr(host.space, "_any_workdir_clearing", lambda: None)
                mp.setattr(space_facade, "_move", inject_overflow_and_fail_rollback)
                with pytest.raises(OverflowError):
                    await host.space.open_project("ovfvetop")
            assert host.task_agents.is_workdir_clear_vetoed(ovf_hold) is True
        finally:
            host.task_agents.release_workdir(ovf_hold)
        ovfvetop_dir = host.paths.space().project_dir("ovfvetop")
        if ovfvetop_dir.exists():
            _shutil.rmtree(ovfvetop_dir)

        # 4. overview() pre-resolves active reference aliases once (O(N+M) realpath, not O(N*M)).
        for i in range(6):
            s_ov = await host.ledger.create_session(name=f"ov-s-{i}", lanlan_name="Lan", project="ovproj")
            w_ov = host.paths.work_dir(s_ov.id, "ovproj")
            w_ov.mkdir(parents=True)
            await host.ledger.update_session(s_ov.id, status="idle", workdir=str(w_ov))
        for j in range(5):
            active_s = await host.ledger.create_session(
                name=f"ov-act-{j}", lanlan_name="Lan", project="ovproj", workdir=str(tmp_path / f"act_{j}")
            )
            await host.ledger.update_session(active_s.id, status="running")

        ov_realpath_calls = [0]

        def count_ov_realpath(p: Any) -> str:
            ov_realpath_calls[0] += 1
            return orig_realpath(p)

        with monkeypatch.context() as mp:
            mp.setattr(os.path, "realpath", count_ov_realpath)
            ov_data = await host.storage_overview()
        assert "categories" in ov_data
        # Linear bound: well below O(N*M) pairwise comparisons (would exceed 100+ calls).
        assert ov_realpath_calls[0] < 40

        # 4.5. prepare_place() re-checks _closed after _path_aliases() resolution before claiming workdir or mkdir.
        from types import SimpleNamespace

        from nekopower.host.task_agents.types import _Session

        shut_s = await host.ledger.create_session(name="shut-s", lanlan_name="Lan", project="shutp")
        shut_c, _ = await host.ledger.create(
            dispatch_text="shut-c",
            title="shut-c",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="shutp",
            session_id=shut_s.id,
        )
        shut_work = host.paths.work_dir(shut_s.id, "shutp")
        assert not shut_work.exists()
        orig_path_aliases = host.task_agents._path_aliases
        closed_triggered = [False]

        def close_during_aliases(workdir: Any) -> tuple[str, ...]:
            if os.path.normcase(os.path.normpath(str(workdir))) == os.path.normcase(os.path.normpath(str(shut_work))):
                closed_triggered[0] = True
                host.task_agents._closed = True
            return orig_path_aliases(workdir)

        async def fake_session_model(_kept: Any) -> Any:
            return SimpleNamespace(
                config={"model": "test-model", "api_key": "k", "base_url": "http://127.0.0.1:1"},
                agent=None,
                ident="test",
            )

        host.task_agents._sessions[shut_c.id] = _Session(commission_id=shut_c.id, session_id=shut_s.id)
        try:
            with monkeypatch.context() as mp:
                mp.setattr(host.task_agents, "session_model", fake_session_model)
                mp.setattr(host.task_agents, "_path_aliases", close_during_aliases)
                started = await host.task_agents._start(shut_c.id)
        finally:
            host.task_agents._closed = False
            host.task_agents._sessions.pop(shut_c.id, None)
        assert closed_triggered[0] is True
        assert started is False
        assert shut_c.id not in host.task_agents._starting_workdirs
        assert not shut_work.exists()

        # 5. tree_work.py:
        #    (a) Stale held -> discarding(expired) row re-read after _trees_in_hand keeps row for upkeep.
        stale_wt = host.paths.worktree_dir("staledsc1")
        stale_s = await host.ledger.create_session(name="stale-s", lanlan_name="Lan", project="stalep")
        stale_c, _ = await host.ledger.create(
            dispatch_text="stale-c",
            title="stale-c",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="stalep",
            session_id=stale_s.id,
            workdir=str(stale_wt),
        )
        await host.ledger.add_tree(
            id="staledsc1",
            session_id=stale_s.id,
            commission_id=stale_c.id,
            lanlan_name="Lan",
            project="stalep",
            project_dir=str(tmp_path),
            repo_root=str(tmp_path),
            path=str(stale_wt),
            workdir=str(stale_wt),
            token="tok-stale",
            state="held",
        )
        orig_trees = host.ledger.trees
        flipped = [False]

        async def trees_then_flip(*args: Any, **kwargs: Any) -> Any:
            rows = await orig_trees(*args, **kwargs)
            if not flipped[0] and any(r.id == "staledsc1" for r in rows):
                flipped[0] = True
                await host.ledger.update_tree("staledsc1", state="discarding", reason="expired")
            return rows

        with monkeypatch.context() as mp:
            mp.setattr(host.ledger, "trees", trees_then_flip)
            rec_stale = await host.task_agents._reconcile_trees_now()
        assert "staledsc1" in rec_stale["kept"]
        kept_stale_row = await host.ledger.get_tree("staledsc1")
        assert kept_stale_row is not None and kept_stale_row.state == "discarding" and kept_stale_row.reason == "expired"

        #    (b) _settle_reconciled_pending_expiry: if forget_gone() raises WorktreeError because the
        #        directory reappeared on a "removed" marker, keep row, marker, and blocked commission.
        reap_repo = tmp_path / "reap_repo"
        (reap_repo / ".git").mkdir(parents=True)
        reap_wt = host.paths.worktree_dir("reapfg01")
        reap_s = await host.ledger.create_session(name="reap-s", lanlan_name="Lan", project="reapp")
        reap_c, _ = await host.ledger.create(
            dispatch_text="reap-c",
            title="reap-c",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="reapp",
            session_id=reap_s.id,
            workdir=str(reap_wt),
        )
        await host.ledger.transition(reap_c.id, "working")
        await host.ledger.transition(reap_c.id, "blocked")
        reap_row = await host.ledger.add_tree(
            id="reapfg01",
            session_id=reap_s.id,
            commission_id=reap_c.id,
            lanlan_name="Lan",
            project="reapp",
            project_dir=str(reap_repo),
            repo_root=str(reap_repo),
            path=str(reap_wt),
            workdir=str(reap_wt),
            token="tok-reap",
            state="held",
        )
        await host.task_agents._record_pending_expired_tree("reapfg01", "removed", row=reap_row)
        forget_called = [False]

        async def forget_gone_after_restore(_repo: Any, path: Any, _tid: str) -> None:
            forget_called[0] = True
            Path(path).mkdir(parents=True, exist_ok=True)
            raise WorktreeError("worktree directory exists again")

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents.worktrees, "forget_gone", forget_gone_after_restore)
            rec_reap = await host.task_agents._reconcile_trees_now()
        assert forget_called[0] is True
        assert "reapfg01" in rec_reap["kept"]
        assert (await host.ledger.get_tree("reapfg01")).state == "held"
        assert (await host.ledger.get(reap_c.id)).status == "blocked"
        assert "reapfg01" in await host.task_agents._read_pending_expired_trees()

        #    (c) _reconcile_trees_now: if directory reappears while reading pending markers (empty marker),
        #        do NOT call drop_tree().
        reap2_wt = host.paths.worktree_dir("reapdrp1")
        reap2_s = await host.ledger.create_session(name="reap2-s", lanlan_name="Lan", project="reapp2")
        reap2_c, _ = await host.ledger.create(
            dispatch_text="reap2-c",
            title="reap2-c",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="reapp2",
            session_id=reap2_s.id,
            workdir=str(reap2_wt),
        )
        await host.ledger.add_tree(
            id="reapdrp1",
            session_id=reap2_s.id,
            commission_id=reap2_c.id,
            lanlan_name="Lan",
            project="reapp2",
            project_dir=str(tmp_path),
            repo_root=str(tmp_path),
            path=str(reap2_wt),
            workdir=str(reap2_wt),
            token="tok-reap2",
            state="held",
        )
        orig_read_pending = host.task_agents._read_pending_expired_trees
        reap2_restored = [False]

        async def restore_dir_during_marker_read(*args: Any, **kwargs: Any) -> dict[str, Any]:
            res = await orig_read_pending(*args, **kwargs)
            if "reapdrp1" in host.task_agents._trees_in_hand and not reap2_wt.exists():
                reap2_restored[0] = True
                reap2_wt.mkdir(parents=True, exist_ok=True)
            return res

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_read_pending_expired_trees", restore_dir_during_marker_read)
            rec_reap2 = await host.task_agents._reconcile_trees_now()
        assert reap2_restored[0] is True
        assert "reapdrp1" in rec_reap2["kept"]
        assert await host.ledger.get_tree("reapdrp1") is not None

        #    (d) _settle_reconciled_pending_expiry: if directory reappears while _repo_root_gone()
        #        returns True (so forget_gone() is skipped), still keep row, "removed" marker, and commission.
        reap3_wt = host.paths.worktree_dir("reapgon1")
        reap3_s = await host.ledger.create_session(name="reap3-s", lanlan_name="Lan", project="reapp3")
        reap3_c, _ = await host.ledger.create(
            dispatch_text="reap3-c",
            title="reap3-c",
            lanlan_name="Lan",
            source="dispatch",
            route="task_agent",
            project="reapp3",
            session_id=reap3_s.id,
            workdir=str(reap3_wt),
        )
        await host.ledger.transition(reap3_c.id, "working")
        await host.ledger.transition(reap3_c.id, "blocked")
        gone_repo = tmp_path / "vanished_repo"
        reap3_row = await host.ledger.add_tree(
            id="reapgon1",
            session_id=reap3_s.id,
            commission_id=reap3_c.id,
            lanlan_name="Lan",
            project="reapp3",
            project_dir=str(gone_repo),
            repo_root=str(gone_repo),
            path=str(reap3_wt),
            workdir=str(reap3_wt),
            token="tok-reap3",
            state="held",
        )
        await host.task_agents._record_pending_expired_tree("reapgon1", "removed", row=reap3_row)
        orig_repo_gone = host.task_agents._repo_root_gone
        reap3_triggered = [False]

        async def restore_during_repo_gone(repo_root: str) -> bool:
            res = await orig_repo_gone(repo_root)
            if os.path.normcase(os.path.normpath(repo_root)) == os.path.normcase(os.path.normpath(str(gone_repo))):
                reap3_triggered[0] = True
                reap3_wt.mkdir(parents=True, exist_ok=True)
            return res

        with monkeypatch.context() as mp:
            mp.setattr(host.task_agents, "_repo_root_gone", restore_during_repo_gone)
            rec_reap3 = await host.task_agents._reconcile_trees_now()
        assert reap3_triggered[0] is True
        assert "reapgon1" in rec_reap3["kept"]
        assert (await host.ledger.get_tree("reapgon1")).state == "held"
        assert (await host.ledger.get(reap3_c.id)).status == "blocked"
        assert "reapgon1" in await host.task_agents._read_pending_expired_trees()

        #    (e) Worktree cleanup paths synchronize active project reads under space write lock.
        lock_flags: list[bool] = []
        orig_list_checked = host.space.list_projects_checked

        async def spy_list_checked(*args: Any, **kwargs: Any) -> Any:
            lock_flags.append(bool(kwargs.get("under_write_lock", False)))
            return await orig_list_checked(*args, **kwargs)

        orph_sync = host.paths.worktree_dir("syncwt01")
        orph_sync.mkdir(parents=True)
        with monkeypatch.context() as mp:
            mp.setattr(host.space, "list_projects_checked", spy_list_checked)
            res_sync = await host.task_agents.remove_orphan_worktree("syncwt01")
        assert res_sync["removed"] is True
        assert lock_flags == [True]
    finally:
        await host.shutdown()

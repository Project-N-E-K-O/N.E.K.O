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

        def spy_remove(target: Path, allowed_root: Path) -> tuple[bool, int]:
            observed_holds["src"] = sess.id in host.task_agents._clearing
            observed_holds["fork"] = fork.id in host.task_agents._clearing
            return real_remove(target, allowed_root)

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
        (odd_proj / "sessions").write_bytes(b"z" * 333)
        view_other = await host.storage_overview()
        other_cat = next(c for c in view_other["categories"] if c["key"] == "other")
        assert other_cat["bytes"] >= 333

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
                host.storage.settings._kept = {**(host.storage.settings._kept or {}), "auto_clean_worktrees": False}
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
                    host.storage.settings._kept = {**(host.storage.settings._kept or {}), "auto_clean_work_dirs": False}
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
        case_insensitive = (tracked_wt.parent / "ABC012345678").exists()
        if case_insensitive:
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
    for args in (
        ("git", "init"),
        ("git", "config", "user.name", "Test"),
        ("git", "config", "user.email", "test@example.com"),
    ):
        subprocess.run(args, cwd=repo, check=True, capture_output=True)
    (subdir / "hello.txt").write_text("hi\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True)
    subprocess.run(("git", "commit", "-m", "init"), cwd=repo, check=True, capture_output=True)

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

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

        async def broken_list_projects(*_args: Any, **_kwargs: Any) -> list[Any]:
            raise OSError("disk temporarily busy")

        monkeypatch.setattr(host.space, "list_projects", broken_list_projects)
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

        real_dir_of = host.space_keeper._session_dir_of
        calls = {"n": 0}

        def flaky_dir_of(session: Any) -> Path:
            if session.id == s2.id and calls["n"] == 0:
                calls["n"] += 1
                raise PermissionError("transient stat error")
            return real_dir_of(session)

        monkeypatch.setattr(host.space_keeper, "_session_dir_of", flaky_dir_of)
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

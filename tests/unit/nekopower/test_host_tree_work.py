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

"""Task agents that change files side by side, and a leader's team (``tree_work.py``).

A member added beside the one that works in a project's directory has a git
worktree made for each commission and does not wait for the other; what it
changed is merged into the project's directory when it is done, once whoever
works there is between two steps; what clashes goes back to it a few times,
and past that the user decides. A leader starts a team for one commission
and keeps it. Most of this is told with a stand-in for the worktrees (a
directory each, landed by copying), so that what a merge comes to can be
said by the test; the last tests run git itself.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import shutil
import types
import uuid
from pathlib import Path
from typing import Any

import pytest

from config import nekopower_settings as settings
from config.prompts import prompts_nekopower_brain as brain_texts
from nekopower.host.board import STATES
from nekopower.host.service import ProjectBusy
from nekopower.host.worktrees import (
    FileChange,
    MergeResult,
    Probe,
    ReconcileReport,
    Worktree,
    WorktreeError,
    WorktreeUnavailable,
)
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekopower.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    scripted_runtime,
    scripts_by_commission,
    wait_status,
    wait_until,
)

pytestmark = pytest.mark.unit

FIRST = "fix the header of the shop site"
SECOND = "repair the broken links in the footer on every page of the shop site"
# What the title of an inspector's commission begins with: its script is told by it (it
# comes before what it quotes of the commission it checks).
CHECK = "Check: "


@pytest.fixture(autouse=True)
def _one_after_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_FORK_CONTINUE_ENABLED", False)
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_REVIEW_ENABLED", False)
    # The shortest way to a project with a leader (``test_host_families.py``).
    monkeypatch.setattr(settings, "LEADER_IN_GIT_REPOSITORIES", True)


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


class FakeTrees:
    """Stands in for ``WorktreeManager``: a worktree is a directory, and landing copies its files into the project's."""

    def __init__(self) -> None:
        self.unusable: str | None = None
        self.fail_create: Exception | None = None
        # Which makings fail so, counted from one; every one when none is named.
        self.fail_creates: set[int] = set()
        self.creates = 0
        # Set once a worktree is being made, which waits for ``create_gate`` when there is one.
        self.creating = asyncio.Event()
        self.create_gate: asyncio.Event | None = None
        # Whether a worktree that is there cannot be opened this moment (git does not answer).
        self.fail_open = False
        # Set once a worktree is being opened, which waits for ``open_gate`` when there is one.
        self.opening = asyncio.Event()
        self.open_gate: asyncio.Event | None = None
        # What the next merges come to; one that is not said copies the files and says ``landed``.
        self.results: list[Any] = []
        self.gate: asyncio.Event | None = None
        self.merging = asyncio.Event()
        self.made: list[Path] = []
        self.merged: list[str] = []
        self.prepared: list[str] = []
        self.removed: list[str] = []
        self.left_over: list[Path] = []
        self.reconciled: list[str] = []
        # What removing a worktree fails with (a file of it is in use), while it is set.
        self.fail_remove: Exception | None = None
        # Set once a worktree is looked at for changes, which waits for ``changes_gate`` when there is one.
        self.checking = asyncio.Event()
        self.changes_gate: asyncio.Event | None = None

    async def probe(self, directory: Any) -> Probe:
        if self.unusable is not None:
            return Probe(self.unusable, f"{directory} will not do")
        return Probe(None, "", Path(directory), "")

    async def create(self, project_dir: Any, path: Any, start: Any = None) -> Worktree:
        del start
        self.creates += 1
        self.creating.set()
        if self.create_gate is not None:
            await self.create_gate.wait()
        if self.fail_create is not None and (not self.fail_creates or self.creates in self.fail_creates):
            raise self.fail_create
        Path(path).mkdir(parents=True)
        self.made.append(Path(path))
        return Worktree(Path(path).name, Path(project_dir), Path(path), "", "start", uuid.uuid4().hex)

    async def open(self, project_dir: Any, path: Any) -> Worktree:
        if not Path(path).is_dir():
            raise WorktreeError(f"{path} is not a worktree")
        self.opening.set()
        if self.open_gate is not None:
            await self.open_gate.wait()
        if self.fail_open:
            raise WorktreeError("git did not answer")
        return Worktree(Path(path).name, Path(project_dir), Path(path), "", "start", "")

    async def has_changes(self, worktree: Worktree) -> bool:
        self.checking.set()
        if self.changes_gate is not None:
            await self.changes_gate.wait()
        return worktree.path.is_dir() and any(worktree.path.iterdir())

    async def merge_back(self, worktree: Worktree, landing: Any = None) -> MergeResult:
        del landing
        self.merged.append(worktree.id)
        self.merging.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        files = []
        for file in sorted(worktree.path.rglob("*")):
            if file.is_file():
                name = file.relative_to(worktree.path)
                target = worktree.repo_root / name
                status = "modified" if target.exists() else "added"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
                files.append(FileChange(name.as_posix(), status, 1, 0))
        if not files:
            return MergeResult("unchanged")
        return MergeResult("landed", files=tuple(files), lines_added=len(files))

    async def prepare_conflicts(self, worktree: Worktree, conflict: MergeResult) -> Worktree:
        assert conflict.status == "conflict"
        self.prepared.append(worktree.id)
        return dataclasses.replace(worktree, start="start-2")

    async def remove(self, worktree: Worktree) -> None:
        if self.fail_remove is not None:
            raise self.fail_remove
        shutil.rmtree(worktree.path, ignore_errors=True)
        self.removed.append(worktree.id)

    async def reconcile(self, project_dir: Any, base_dir: Any, known: Any) -> ReconcileReport:
        self.reconciled.append(str(project_dir))
        report = ReconcileReport()
        report.kept.extend(path for path in self.left_over if path.name not in set(known))
        return report


def _host(hosts: list[Any], tmp_path: Path, pick: Any, **kwargs: Any) -> tuple[Any, FakeTrees]:
    """A host with no plugins whose worktrees are stand-ins; a project is in a git repository when a test says so."""
    pick = pick if callable(pick) else (lambda spec, llm=pick: llm)
    kwargs.setdefault("task_agent_model", fake_model)
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    host = make_host(tmp_path, None, runtime_factory=scripted_runtime(pick), **kwargs)
    trees = FakeTrees()
    host.task_agents.worktrees = trees
    host.task_agents.families._worktrees = trees

    async def nowhere(_directory: str) -> bool:
        return False

    host.task_agents.families._probe = nowhere
    hosts.append(host)
    return host, trees


async def _shop(hosts: list[Any], tmp_path: Path, pick: Any, allow: bool = True) -> tuple[Any, FakeTrees, Path]:
    """A host with the project "Shop site", which gets a leader with its first commission."""
    host, trees = _host(hosts, tmp_path, pick)
    workdir = tmp_path / "Shop site-files"
    workdir.mkdir()
    await host.space.touch_project("Shop site", workdir=str(workdir))

    async def probe(directory: str) -> bool:
        return directory == str(workdir)

    host.task_agents.families._probe = probe
    if allow:
        await host.permission_store.set_mode("open_session", "allow")
    return host, trees, workdir


async def _dispatch(host: Any, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "en")
    receipt = await host.dispatch(text, **kwargs)
    await host.brain.settle()
    return receipt


def _report(summary: str = "done", status: str = "ready") -> list[Any]:
    return [call("report", {"status": status, "summary": summary}), finish("tool_calls")]


def _write(name: str, content: str = "x") -> list[Any]:
    return [call("write", {"file_path": name, "content": content}), finish("tool_calls")]


def _hand_over(**arguments: Any) -> list[list[Any]]:
    arguments.setdefault("description", "does the work")
    arguments.setdefault("brief", "Do what the commission says.")
    return [[call("hand_over", arguments), finish("tool_calls")], [say("handed over"), finish("stop")]]


def _by_role(leader: Any, members: Any) -> Any:
    by_commission = scripts_by_commission(members) if isinstance(members, dict) else None

    def pick(spec: Any) -> Any:
        if "hand_over" not in spec.disabled_tools:
            return leader
        return by_commission(spec) if by_commission is not None else members

    return pick


async def _events(host: Any, commission_id: str, kind: str) -> list[dict[str, Any]]:
    return [event.payload for event in await host.ledger.events(commission_id) if event.type == kind]


async def _session_of(host: Any, commission_id: str) -> Any:
    commission = await host.ledger.get(commission_id)
    return await host.ledger.get_session(commission.session_id)


def _said_to(llm: ScriptedLLM) -> str:
    """Everything a session's model was sent that is not its system prompt, as one text."""
    return json.dumps([message for request in llm.requests for message in request if message.get("role") != "system"])


async def _two_members(
    hosts: list[Any], tmp_path: Path, second: list[list[Any]], first: Any = None, **more: Any
) -> tuple[Any, FakeTrees, Path, Any, Any, dict[str, ScriptedLLM]]:
    """The shop with a member at work in its directory, and a second commission handed to a new member beside it.

    The first member's first request waits until ``host.release`` is
    set, and then reports; ``first``, given what waits so, returns
    another script for it. The second one's script is ``second``.
    """
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM(first(wait) if first is not None else [[wait, *_report("header fixed")]]),
        "broken links": ScriptedLLM(second),
        **more,
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))
    host.release = release
    one = await _dispatch(host, FIRST)
    await wait_until(lambda: len(members["the header"].requests) >= 1, what="the first member being at work")
    two = await _dispatch(host, SECOND)
    return host, trees, workdir, one, two, members


# ---------------------------------------------------------------------------
# Who works in a worktree
# ---------------------------------------------------------------------------


async def test_a_member_added_beside_the_first_works_in_a_worktree_and_does_not_wait(hosts, tmp_path):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, [_write("links.txt", "repaired"), _report("links repaired")]
    )

    # At work at once, beside the first one: nothing waits.
    await wait_until(lambda: len(members["broken links"].requests) >= 2, what="the second member having written")
    assert host.task_agents.queued == []
    first, second = await _session_of(host, one.commission_id), await _session_of(host, two.commission_id)
    assert (first.seat, second.seat) == ("", "tree")
    (tree,) = await host.ledger.trees()
    assert (tree.commission_id, tree.session_id, tree.project, tree.project_dir) == (
        two.commission_id, second.id, "Shop site", str(workdir),
    )
    assert tree.id == two.commission_id[:12] and Path(tree.path) == host.paths.worktree_dir(tree.id)
    assert (Path(tree.workdir) / "links.txt").read_text() == "repaired" and not (workdir / "links.txt").exists()
    # The commission works there; its session keeps the project's directory as where it works.
    assert (await host.ledger.get(two.commission_id)).workdir == tree.workdir
    assert second.workdir == str(workdir) and (await _session_of(host, two.commission_id)).workdir == str(workdir)
    (made,) = await _events(host, two.commission_id, "tree_made")
    assert (made["tree"], made["path"]) == (tree.id, tree.path)
    # It was told where it works, and what is not there.
    told = _said_to(members["broken links"])
    assert "git worktree" in told and json.dumps(tree.workdir)[1:-1] in told and json.dumps(str(workdir))[1:-1] in told
    # Only the first one writes in the project's directory.
    assert host.task_agents._writes_in == {first.id: ("Shop site", first.id)}

    # It said it is done while the first one is in the middle of a step: landed once that one is through.
    await wait_until(lambda: two.commission_id in host.task_agents._dones, what="the second one saying it is done")
    assert trees.merged == [] and (await host.ledger.get(two.commission_id)).status == "working"
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    await wait_status(host, one.commission_id, "done")
    assert (workdir / "links.txt").read_text() == "repaired"
    assert await host.ledger.trees() == [] and trees.removed == [tree.id] and not Path(tree.path).exists()
    (landed,) = await _events(host, two.commission_id, "landed")
    assert landed["files"] == [{"path": "links.txt", "status": "added", "added": 1, "deleted": 0}]
    assert (landed["more"], landed["lines_added"], landed["outside"]) == (0, 1, [])
    assert done.result_summary.startswith("links repaired") and "Merged into the project's directory" in done.result_summary
    assert "links.txt (new)" in done.result_summary


async def test_where_no_worktree_can_be_made_a_member_waits_its_turn_as_it_always_did(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM([[wait, *_report("header fixed")]]),
        "broken links": ScriptedLLM([_write("links.txt"), _report("links repaired")]),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))
    trees.unusable = "not_a_repository"

    one = await _dispatch(host, FIRST)
    await wait_until(lambda: len(members["the header"].requests) == 1, what="the first member being at work")
    two = await _dispatch(host, SECOND)
    await wait_until(lambda: host.task_agents.queued == [two.commission_id], what="the second one waiting")
    assert (await _session_of(host, two.commission_id)).seat == "tree"
    assert members["broken links"].requests == [] and trees.made == []

    release.set()
    await wait_status(host, one.commission_id, "done")
    done = await wait_status(host, two.commission_id, "done")
    # It worked in the project's directory itself, and nothing was landed.
    assert done.workdir == str(workdir) and (workdir / "links.txt").exists()
    assert trees.merged == [] and await host.ledger.trees() == []
    (why,) = await _events(host, two.commission_id, "tree_not_made")
    assert why["reason"] == "not_a_repository"
    assert done.result_summary == "links repaired"


async def test_a_member_whose_worktree_cannot_be_made_as_it_starts_takes_its_turn_in_the_directory(hosts, tmp_path):
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    hold = asyncio.Event()

    async def until_looked() -> None:
        await hold.wait()

    elsewhere = asyncio.Event()

    async def in_another_project() -> None:
        await elsewhere.wait()

    members = {
        "the header": ScriptedLLM([[wait, *_report("header fixed")]]),
        "broken links": ScriptedLLM([_write("links.txt"), [until_looked, *_report("links repaired")]]),
        "photo album": ScriptedLLM([[in_another_project, *_report("sorted")]]),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))
    photos = tmp_path / "photos"
    photos.mkdir()
    await host.space.touch_project("Photo album", workdir=str(photos))
    # One that writes in another project is at work all the while: nobody of this project waits for it.
    other = await _dispatch(host, "sort the photos of the photo album by date")
    await wait_until(lambda: len(members["photo album"].requests) == 1, what="the other project's member being at work")
    # It looks as if one could be made, and the disk turns out to be full.
    trees.fail_create = WorktreeUnavailable("disk_space", "1 bytes free, 2 needed")

    one = await _dispatch(host, FIRST)
    await wait_until(lambda: len(members["the header"].requests) == 1, what="the first member being at work")
    two = await _dispatch(host, SECOND)

    async def not_made() -> list[dict[str, Any]]:
        return await _events(host, two.commission_id, "tree_not_made")

    await wait_until(not_made, what="the worktree not being made")
    (why,) = await not_made()
    assert (why["reason"], why["detail"]) == ("disk_space", "1 bytes free, 2 needed")
    # It had taken its place beside the other, and waits there for the one at work in the directory:
    # held to its turn from here on, as one that writes there.
    second = await _session_of(host, two.commission_id)
    assert host.task_agents._writes_in.get(second.id) == ("Shop site", second.id)
    await asyncio.sleep(0.5)
    assert members["broken links"].requests == [] and (await host.ledger.get(two.commission_id)).status == "queued"

    release.set()
    await wait_status(host, one.commission_id, "done")
    await wait_until(lambda: len(members["broken links"].requests) == 2, what="the second one being at work")
    # At work in the directory now, it is still one that writes there: others wait for it.
    assert host.task_agents._writes_in.get(second.id) == ("Shop site", second.id)
    hold.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.workdir == str(workdir) and (workdir / "links.txt").exists() and trees.merged == []
    assert await host.ledger.trees() == []
    assert (await host.ledger.get(other.commission_id)).status == "working"
    elsewhere.set()
    await wait_status(host, other.commission_id, "done")


async def test_a_project_has_no_more_worktrees_than_it_may(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKTREES_PER_PROJECT", 0)
    host, trees, workdir, one, two, _members = await _two_members(
        hosts, tmp_path, [_write("links.txt"), _report("links repaired")]
    )
    await wait_until(lambda: host.task_agents.queued == [two.commission_id], what="the second one waiting")
    host.release.set()
    await wait_status(host, two.commission_id, "done")
    assert trees.made == [] and (workdir / "links.txt").exists()
    assert (await _events(host, two.commission_id, "tree_not_made"))[0]["reason"] == "too_many"


async def test_a_member_in_a_worktree_writes_there_and_not_in_its_projects_directory(hosts, tmp_path):
    # (Where the project's directory will be: known before anybody is at work.)
    there = tmp_path / "Shop site-files"
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        [_write(str(there / "links.txt"), "straight into the project"), _write("links.txt", "in my own"), _report("links repaired")],
    )
    assert workdir == there
    host.release.set()
    await wait_status(host, two.commission_id, "done")

    refused = _said_to(members["broken links"])
    assert "which you only read" in refused and "write the file at the same place below your own directory" in refused
    # What it wrote in its own directory is what landed.
    assert (workdir / "links.txt").read_text() == "in my own"


async def test_nobody_writes_in_the_worktree_of_another_and_what_is_there_is_read_without_asking(hosts, tmp_path):
    kept: dict[str, Any] = {}

    def into_the_other() -> list[Any]:
        return _write(str(kept["tree"] / "links.txt"), "mine now")

    def read_the_other() -> list[Any]:
        return [call("read", {"file_path": str(kept["tree"] / "links.txt")}), finish("tool_calls")]

    looked = asyncio.Event()

    async def until_looked() -> None:
        await looked.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "repaired"), [until_looked, *_report("links repaired")]],
        first=lambda wait: [
            [wait, call("glob", {"pattern": "*.html"}), finish("tool_calls")], into_the_other, read_the_other,
            _report("header fixed"),
        ],
    )
    await wait_until(lambda: len(members["broken links"].requests) == 2, what="the second member having written")
    kept["tree"] = host.paths.worktree_dir(two.commission_id[:12])
    # Reading outside the working directory is asked about by default: not what another task agent left.
    assert await host.permission_store.mode("outside_read") != "allow"
    host.release.set()
    await wait_status(host, one.commission_id, "done")
    looked.set()
    await wait_status(host, two.commission_id, "done")

    told = _said_to(members["the header"])
    assert "in the directory another task agent works in" in told
    assert "repaired" in told and "mine now" not in (workdir / "links.txt").read_text()
    events = await host.ledger.events(one.commission_id)
    assert [event for event in events if event.type == "status" and event.payload.get("to") == "waiting_user"] == []


async def test_a_task_agent_a_member_in_a_worktree_starts_works_there_with_it(hosts, tmp_path):
    there = tmp_path / "Shop site-files"
    helper = ScriptedLLM([
        _write(str(there / "styles.css"), "straight into the project"), _write("styles.css", "tidy"), _report("styles tidied"),
    ])
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[
            [call("subagent", {"description": "styles", "prompt": "Tidy the stylesheet. PART-STYLES"}), finish("tool_calls")],
            [say("waiting for it"), finish("stop")],
            _report("links repaired"),
        ],
        **{"PART-STYLES": helper},
    )
    assert workdir == there
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")

    (child,) = await host.ledger.select(parent_id=two.commission_id)
    # In the worktree of the member that started it: no other is made, and it does not write in the project either.
    assert child.workdir == str(host.paths.worktree_dir(two.commission_id[:12])) and len(trees.made) == 1
    assert "which you only read" in _said_to(helper)
    assert (workdir / "styles.css").read_text() == "tidy" and "styles.css (new)" in done.result_summary
    # Neither of the two was held to the turn of whoever writes in the project's directory.
    assert await _events(host, child.id, "queued") == []


async def test_a_commission_whose_worktree_is_gone_when_it_is_taken_up_again_gets_another(hosts, tmp_path):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "half"), _report("which pages?", status="blocked"), _write("more.txt"), _report("all of them")],
    )
    host.release.set()
    await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="its worktree being seen to")
    (tree,) = await host.ledger.trees()
    # Somebody removed its directory by hand, and the host was started anew since.
    shutil.rmtree(tree.path)
    host.task_agents._tree_handles.clear()

    assert await host.tell(two.commission_id, "every page")
    done = await wait_status(host, two.commission_id, "done")

    assert await _events(host, two.commission_id, "tree_lost") == [{"tree": tree.id, "path": tree.path}]
    assert len(trees.made) == 2 and (workdir / "more.txt").exists() and not (workdir / "links.txt").exists()
    assert done.result_summary.startswith("all of them") and await host.ledger.trees() == []


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", [("glob", {"pattern": "*.html"}), ("list_agents", {})], ids=["a file tool", "a tool nobody rules on"])
async def test_a_landing_waits_until_whoever_works_in_the_directory_is_between_two_steps(hosts, tmp_path, tool):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "repaired"), _report("links repaired")],
        # (Its first request: it is in the middle of a step until it is let go on, and then uses a tool:
        # one the host rules on, or one it is not asked about, which is held before the next request.)
        first=lambda wait: [
            [wait, call(*tool), finish("tool_calls")], [say("going on"), finish("stop")],
        ],
    )
    trees.gate = asyncio.Event()
    await wait_until(lambda: two.commission_id in host.task_agents._dones, what="the second one saying it is done")
    await asyncio.sleep(0.4)
    # In the middle of the first one's step nothing is merged.
    assert trees.merged == [] and len(members["the header"].requests) == 1

    host.release.set()
    await asyncio.wait_for(trees.merging.wait(), 10)
    # The merge is under way, and the first one is held before its next request.
    await asyncio.sleep(0.4)
    assert len(members["the header"].requests) == 1
    cards = {card["id"]: card for card in (await host.board())["projects"][0]["members"]}
    landing = cards[(await _session_of(host, two.commission_id)).id]
    assert (landing["state"], landing["tree"]["state"], landing["commission"]["status"]) == ("landing", "landing", "working")
    # What this process made is not taken for something an earlier one left.
    assert await host.task_agents.reconcile_trees() == {"removed": [], "kept": []}
    trees.gate.set()
    await wait_status(host, two.commission_id, "done")
    await wait_status(host, one.commission_id, "done")
    # It went on then, and was told what was merged into the directory it works in.
    told = _said_to(members["the header"])
    assert len(members["the header"].requests) >= 2
    assert "merged its changes into the directory you work in" in told and "links.txt (new)" in told
    assert await _events(host, one.commission_id, "told_of_landing")


async def test_what_clashes_goes_back_to_the_task_agent_and_lands_once_it_is_merged(hosts, tmp_path):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "mine"), _report("links repaired"), _write("links.txt", "both"), _report("merged")],
    )
    trees.results = [MergeResult("conflict", conflicts=("links.txt",))]
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")

    assert trees.merged == [two.commission_id[:12]] * 2 and trees.prepared == [two.commission_id[:12]]
    (clash,) = await _events(host, two.commission_id, "landing_conflict")
    assert (clash["conflicts"], clash["round"]) == (["links.txt"], 1)
    told = _said_to(members["broken links"])
    assert "these files clash with what is there by now: links.txt" in told and "try 1 of 3" in told
    assert "it is not the user's words" in told
    assert (workdir / "links.txt").read_text() == "both" and done.result_summary.startswith("merged")
    assert await host.ledger.trees() == []


async def _not_landed(hosts: list[Any], tmp_path: Path, results: list[Any], turns: int = 1) -> tuple[Any, ...]:
    """The shop with a second member whose changes are not landed: every merge comes to what ``results`` say."""
    script: list[list[Any]] = [_write("links.txt", "mine")]
    for _turn in range(turns):
        script.append(_report("links repaired"))
    host, trees, workdir, one, two, members = await _two_members(hosts, tmp_path, second=script)
    trees.results = list(results)
    host.release.set()
    await wait_status(host, one.commission_id, "done")
    stuck = await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: host.task_agents._held_by == {}, what="every session being let go")
    return host, trees, workdir, two, members, stuck


async def test_after_three_tries_the_changes_stay_in_the_worktree_and_the_user_decides(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt", "footer.html"))
    host, trees, workdir, two, members, stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)

    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason, tree.rounds, tree.conflicts) == ("unlanded", "conflict", 3, ("links.txt", "footer.html"))
    assert len(trees.merged) == 4 and len(trees.prepared) == 3 and trees.removed == []
    assert [item["round"] for item in await _events(host, two.commission_id, "landing_conflict")] == [1, 2, 3]
    (left,) = await _events(host, two.commission_id, "not_landed")
    assert (left["reason"], left["conflicts"], left["rounds"], left["path"]) == (
        "conflict", ["links.txt", "footer.html"], 3, tree.workdir,
    )
    # Nothing of it is in the project, and the commission says why, and where the changes are.
    assert not (workdir / "links.txt").exists() and (Path(tree.workdir) / "links.txt").exists()
    assert stuck.status_reason == "what it changed was not landed in its project's directory"
    assert "3 tries at merging did not settle it" in stuck.result_summary and tree.workdir in stuck.result_summary
    # Its session is let go, and its project is in the middle of something until the user decided.
    assert await host._project_busy("Shop site") == "tree_not_landed"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ProjectBusy, match="tree_not_landed"):
        await host.set_project_workdir("Shop site", str(elsewhere))
    # Nor is it put away meanwhile, and that says why as well.
    with pytest.raises(ProjectBusy, match="tree_not_landed"):
        await host.archive_project("Shop site")
    listed = await host.worktrees(project="Shop site")
    assert [(item["id"], item["state"], item["title"]) for item in listed] == [(tree.id, "unlanded", stuck.title)]
    # What this process made is not gone through as something an earlier one left.
    assert await host.task_agents.reconcile_trees() == {"removed": [], "kept": []}
    # What the user is shown is in the language of the dialogue.
    words = brain_texts.TREE_NOT_LANDED_TEXTS
    assert set(words) == {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    assert len({frozenset(texts) for texts in words.values()}) == 1 and "猫爪" not in json.dumps(words, ensure_ascii=False)


async def test_the_user_has_a_worktree_landed_once_more_and_its_commission_is_done(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()

    answer = await host.land_worktree(tree.id)

    assert answer["status"] == "landed" and answer["files"][0]["path"] == "links.txt"
    assert (workdir / "links.txt").read_text() == "mine" and await host.ledger.trees() == []
    done = await wait_status(host, two.commission_id, "done")
    assert "merge the changes into the project's directory after all" in done.result_summary
    assert [item.get("by") for item in await _events(host, two.commission_id, "landed")] == ["user"]
    assert await host._project_busy("Shop site") is None
    with pytest.raises(LookupError):
        await host.land_worktree(tree.id)


async def test_tried_again_at_the_users_word_what_clashes_goes_back_to_the_task_agent_counted_anew(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    members["broken links"].turns.extend([[wait, *_write("links.txt", "both")], _report("merged by hand")])
    trees.results = [clash]

    answer = await host.land_worktree(tree.id)

    assert (answer["status"], answer["resolving"], answer["conflicts"]) == ("conflict", True, ["links.txt"])
    await wait_status(host, two.commission_id, "working")
    await wait_until(lambda: two.commission_id in host.task_agents._tree_of, what="it being back in its worktree")
    card = {item["id"]: item for item in (await host.board())["projects"][0]["members"]}[tree.session_id]
    assert (card["state"], card["tree"]["state"], card["tree"]["rounds"]) == ("resolving", "resolving", 1)
    hold.set()
    done = await wait_status(host, two.commission_id, "done")
    assert (workdir / "links.txt").read_text() == "both" and done.result_summary.startswith("merged by hand")
    told = _said_to(members["broken links"])
    assert "The user asked for the changes of this commission to be merged" in told
    # Counted from the first again: this was try 1, not a fourth.
    assert [item["round"] for item in await _events(host, two.commission_id, "landing_conflict")] == [1, 2, 3, 1]
    assert await host.ledger.trees() == []


async def test_the_user_discards_a_worktree_or_sees_to_it_personally(hosts, tmp_path):
    failed = MergeResult("failed", detail="links.txt could not be written", left_changed=("a.txt",), kept_copies="C:/kept")
    host, trees, workdir, two, _members, stuck = await _not_landed(hosts, tmp_path, [failed])
    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason) == ("unlanded", "failed")
    (left,) = await _events(host, two.commission_id, "not_landed")
    assert (left["detail"], left["left_changed"], left["kept_copies"]) == ("links.txt could not be written", ["a.txt"], "C:/kept")
    assert "could not be written (links.txt could not be written)" in stuck.result_summary and "C:/kept" in stuck.result_summary

    # Left to the user: it stays, reminds of nothing, and does not keep the project where it is.
    kept = await host.keep_worktree(tree.id)
    assert kept["state"] == "kept" and Path(tree.path).is_dir()
    assert await host._project_busy("Shop site") is None
    assert await _events(host, two.commission_id, "tree_kept")
    with pytest.raises(ValueError, match="at_work"):
        await host.keep_worktree(tree.id)

    # Discarded after all: gone with what it held, and its commission is put down as stopped.
    assert await host.discard_worktree(tree.id) == {"removed": True}
    assert await host.ledger.trees() == [] and not Path(tree.path).exists() and trees.removed == [tree.id]
    assert (await host.ledger.get(two.commission_id)).status == "cancelled"
    assert await _events(host, two.commission_id, "tree_discarded")
    assert not (workdir / "links.txt").exists()


async def test_a_merge_that_raises_leaves_the_changes_for_the_user(hosts, tmp_path):
    host, trees, _workdir, two, _members, stuck = await _not_landed(hosts, tmp_path, [WorktreeError("git went away")])
    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason) == ("unlanded", "failed") and "git went away" in stuck.result_summary


async def test_asked_first_nothing_lands_until_the_user_says_so(hosts, tmp_path):
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM([_write("links.txt", "repaired"), _report("links repaired")]),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))
    # (With how many worktrees a project may have, which is no setting of the user's: for a page to say so.)
    most = settings.WORKTREES_PER_PROJECT
    assert await host.landing_settings() == {"mode": "auto", "inspect": False, "worktrees_per_project": most}
    assert await host.set_landing_settings(mode="ask") == {"mode": "ask", "inspect": False, "worktrees_per_project": most}
    for wrong, code in (({"mode": "later"}, "bad_mode"), ({"inspect": "yes"}, "bad_inspect")):
        with pytest.raises(ValueError, match=code):
            await host.set_landing_settings(**wrong)

    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")
    two = await _dispatch(host, SECOND)
    held = await wait_status(host, two.commission_id, "blocked")

    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason) == ("held", "ask") and trees.merged == []
    assert not (workdir / "links.txt").exists()
    assert await _events(host, two.commission_id, "landing_held") and not await _events(host, two.commission_id, "not_landed")
    assert held.result_summary.startswith("links repaired")
    assert "nothing is merged into the project's directory before the user says so" in held.result_summary
    await wait_until(lambda: host.task_agents._held_by == {}, what="every session being let go")
    cards = {card["id"]: card for card in (await host.board())["projects"][0]["members"]}
    assert (cards[tree.session_id]["state"], cards[tree.session_id]["tree"]["state"]) == ("held", "held")

    assert (await host.land_worktree(tree.id))["status"] == "landed"
    assert (workdir / "links.txt").read_text() == "repaired"
    assert (await wait_status(host, two.commission_id, "done")).status == "done"
    # The setting is kept in a file next to the ledger.
    assert json.loads(host.paths.landing_json().read_text(encoding="utf-8"))["mode"] == "ask"


@pytest.mark.parametrize("changed", [True, False])
async def test_a_task_agent_stopped_at_work_leaves_what_it_changed_for_the_user(hosts, tmp_path, changed):
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    script = [_write("links.txt", "half")] if changed else []
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, second=[*script, [wait, *_report("links repaired")]]
    )
    await wait_until(lambda: len(members["broken links"].requests) == len(script) + 1, what="the second one at work")

    assert await host.abort(two.commission_id)
    await wait_until(lambda: two.commission_id not in host.task_agents._tree_of, what="its worktree being seen to")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="that being through")
    if changed:
        (tree,) = await host.ledger.trees()
        assert (tree.state, tree.reason) == ("unlanded", "stopped") and Path(tree.path).is_dir()
        assert (await _events(host, two.commission_id, "not_landed"))[0]["reason"] == "stopped"
    else:
        assert await host.ledger.trees() == [] and len(trees.removed) == 1
    assert trees.merged == [] and not (workdir / "links.txt").exists()
    hold.set()
    host.release.set()


async def test_a_commission_taken_up_again_goes_on_in_the_worktree_it_left(hosts, tmp_path):
    more = asyncio.Event()

    async def wait() -> None:
        await more.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "half"), _report("which pages?", status="blocked"),
                [wait, *_write("more.txt")], _report("all of them")],
    )
    host.release.set()
    await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="its worktree being seen to")
    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason) == ("unlanded", "stopped")

    assert await host.tell(two.commission_id, "every page")
    await wait_until(lambda: len(members["broken links"].requests) == 3, what="it being at work again")
    # At work in it again, and on record so: no longer the user's to decide on.
    again = await host.ledger.get_tree(tree.id)
    assert (again.state, again.reason, again.session_id) == ("active", "", tree.session_id)
    with pytest.raises(ValueError, match="at_work"):
        await host.land_worktree(tree.id)
    more.set()
    done = await wait_status(host, two.commission_id, "done")

    # The same worktree, with what it had changed before: both files landed.
    assert trees.made == [Path(tree.path)] and done.workdir == tree.workdir
    assert (workdir / "links.txt").read_text() == "half" and (workdir / "more.txt").exists()
    assert await host.ledger.trees() == []


async def test_a_member_is_told_where_an_earlier_commission_left_what_was_not_landed(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(description="second writer"), *_hand_over(to="Shop site/worker 2")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM([_write("links.txt", "mine"), *[_report("links repaired")] * 4]),
        "the sitemap": ScriptedLLM([_write("sitemap.txt"), _report("sitemap written")]),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))
    trees.results = [clash] * 4
    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")
    two = await _dispatch(host, SECOND)
    await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: host.task_agents._held_by == {}, what="every session being let go")
    (left,) = await host.ledger.trees()

    three = await _dispatch(host, "write the sitemap of the shop site anew from the pages it has")
    done = await wait_status(host, three.commission_id, "done")

    # A worktree of its own, made anew: the one that was not landed stays as it is.
    assert done.session_id == left.session_id and len(trees.made) == 2 and (workdir / "sitemap.txt").exists()
    assert [(tree.id, tree.state) for tree in await host.ledger.trees()] == [(left.id, "unlanded")]
    told = _said_to(members["the sitemap"])
    assert "was not merged into the project's directory" in told and json.dumps(left.workdir)[1:-1] in told
    assert "these files clashed: links.txt" in told


async def test_what_is_on_record_of_worktrees_an_earlier_process_left_is_gone_through(hosts, tmp_path):
    host, trees = _host(hosts, tmp_path, ScriptedLLM([]))
    home = tmp_path / "site"
    home.mkdir()
    await host.space.touch_project("Site", workdir=str(home))
    base = host.paths.worktrees_dir()

    async def left(name: str, state: str, changed: bool, there: bool = True) -> None:
        path = base / name
        if there:
            path.mkdir(parents=True)
            if changed:
                (path / "a.txt").write_text("x")
        await host.ledger.add_tree(
            id=name, project="Site", project_dir=str(home), path=str(path), workdir=str(path), state=state
        )

    await left("atwork-changed", "active", True)
    await left("atwork-clean", "landing", False)
    await left("waiting-changed", "unlanded", True)
    await left("waiting-clean", "held", False)
    await left("gone", "resolving", True, there=False)
    unknown = base / "unknown"
    unknown.mkdir()
    (unknown / "b.txt").write_text("x")
    trees.left_over = [unknown]

    found = await host.task_agents.reconcile_trees()

    rows = {tree.id: (tree.state, tree.reason) for tree in await host.ledger.trees()}
    assert rows == {
        "atwork-changed": ("unlanded", "restart"),
        "waiting-changed": ("unlanded", ""),
        "unknown": ("unlanded", "restart"),
    }
    assert sorted(found["removed"]) == ["atwork-clean", "gone", "waiting-clean"]
    assert sorted(found["kept"]) == ["atwork-changed", "unknown", "waiting-changed"]
    assert sorted(trees.removed) == ["atwork-clean", "waiting-clean"] and trees.reconciled == [str(home)]


async def test_no_git_is_run_for_a_host_that_never_made_a_worktree(hosts, tmp_path):
    host, trees = _host(hosts, tmp_path, ScriptedLLM([]))
    home = tmp_path / "site"
    home.mkdir()
    await host.space.touch_project("Site", workdir=str(home))
    assert await host.task_agents.reconcile_trees() == {"removed": [], "kept": []}
    assert trees.reconciled == []


async def test_a_run_that_ends_without_a_report_lands_what_it_changed_as_well(hosts, tmp_path):
    host, trees, workdir, one, two, _members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt", "repaired"), [say("the links are repaired"), finish("stop")]]
    )
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    assert (workdir / "links.txt").read_text() == "repaired" and done.result_summary.startswith("the links are repaired")
    assert done.status_reason == "the run completed" and await host.ledger.trees() == []


async def test_a_worktree_in_which_nothing_was_changed_is_removed_and_nothing_is_said_of_a_landing(hosts, tmp_path):
    host, trees, workdir, one, two, _members = await _two_members(hosts, tmp_path, second=[_report("nothing to repair")])
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.result_summary == "nothing to repair" and await _events(host, two.commission_id, "landed") == []
    assert len(trees.merged) == 1 and len(trees.removed) == 1 and await host.ledger.trees() == []


async def test_what_is_said_to_a_task_agent_while_its_changes_wait_to_land_is_done_first(hosts, tmp_path):
    more = asyncio.Event()

    async def wait() -> None:
        await more.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "repaired"), _report("links repaired"),
                [wait, *_write("sitemap.txt", "new")], [say("the sitemap is there as well"), finish("stop")]],
    )
    await wait_until(lambda: two.commission_id in host.task_agents._dones, what="the second one saying it is done")
    await wait_until(
        lambda: getattr(host.task_agents._sessions.get(two.commission_id), "idle", False), what="its run being over"
    )
    # The first one is in the middle of a step: nothing lands yet, and the user says more.
    assert trees.merged == []
    assert await host.tell(two.commission_id, "write the sitemap anew as well")
    await wait_until(lambda: len(members["broken links"].requests) == 3, what="it being at work again")
    # The directory is quiet now, and the task agent is in the middle of what was added: not landed under it.
    host.release.set()
    await wait_status(host, one.commission_id, "done")
    await asyncio.sleep(0.6)
    assert trees.merged == [] and (await host.ledger.get(two.commission_id)).status == "working"
    more.set()
    done = await wait_status(host, two.commission_id, "done")

    # One landing, of all it changed, told with what it said last.
    assert len(trees.merged) == 1 and (workdir / "links.txt").exists() and (workdir / "sitemap.txt").exists()
    assert done.result_summary.startswith("the sitemap is there as well")


@pytest.mark.parametrize("asks", ["a question", "a permission"])
async def test_a_landing_does_not_wait_for_one_that_waits_for_the_user(hosts, tmp_path, asks):
    hold = asyncio.Event()

    async def until_asked() -> None:
        await hold.wait()

    def first(wait: Any) -> list[list[Any]]:
        if asks == "a question":
            return [[wait, *_report("which colour is the header to be?", status="waiting")]]
        # Writing outside its directory is asked about: it waits in the middle of a run.
        return [[wait, *_write(str(tmp_path / "outside.txt"))]]

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "repaired"), [until_asked, *_report("links repaired")]],
        first=first,
    )
    await wait_until(lambda: len(members["broken links"].requests) == 2, what="the second member having written")
    host.release.set()
    await wait_status(host, one.commission_id, "waiting_user")
    hold.set()
    done = await wait_status(host, two.commission_id, "done")
    # The first one holds its place, and writes nothing while it waits to be answered.
    assert (await host.ledger.get(one.commission_id)).status == "waiting_user"
    assert (workdir / "links.txt").exists() and done.result_summary.startswith("links repaired")


async def test_of_two_that_got_no_worktree_the_one_that_came_first_goes_first(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    leader_llm = ScriptedLLM(
        [*_hand_over(), *_hand_over(description="second writer"), *_hand_over(description="third writer")]
    )
    order: list[str] = []
    members = {
        "the header": ScriptedLLM([[wait, *_report("header fixed")]]),
        "broken links": ScriptedLLM([_write("links.txt"), _report("links repaired")]),
        "the sitemap": ScriptedLLM([_write("sitemap.txt"), _report("sitemap written")]),
    }
    by_commission = scripts_by_commission(members, order)

    def pick(spec: Any) -> Any:
        return leader_llm if "hand_over" not in spec.disabled_tools else by_commission(spec)

    host, trees, workdir = await _shop(hosts, tmp_path, pick)
    trees.fail_create = WorktreeUnavailable("disk_space", "full")

    one = await _dispatch(host, FIRST)
    await wait_until(lambda: len(members["the header"].requests) == 1, what="the first member being at work")
    two = await _dispatch(host, SECOND)
    three = await _dispatch(host, "write the sitemap of the shop site anew from the pages it has")
    for receipt in (two, three):
        await wait_until(lambda receipt=receipt: _events(host, receipt.commission_id, "tree_not_made"), what="no worktree")
    assert members["broken links"].requests == [] and members["the sitemap"].requests == []

    release.set()
    for receipt in (one, two, three):
        await wait_status(host, receipt.commission_id, "done")
    # Neither waited for the other for good, and they went one after the other.
    assert order == ["the header", "broken links", "the sitemap"]
    assert (workdir / "links.txt").exists() and (workdir / "sitemap.txt").exists() and trees.made == []


async def test_a_member_that_has_its_worktree_is_not_held_to_the_directorys_turn(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKTREES_PER_PROJECT", 1)
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt"), [wait, *_report("links repaired")]]
    )
    await wait_until(lambda: len(members["broken links"].requests) == 2, what="the second member being at work")
    first, second = await _session_of(host, one.commission_id), await _session_of(host, two.commission_id)
    # The project has as many worktrees as it may, and this one is its own.
    await host.task_agents._note_writer(second.id)
    assert host.task_agents._writes_in == {first.id: ("Shop site", first.id)}
    hold.set()
    host.release.set()
    await wait_status(host, two.commission_id, "done")


async def test_a_project_given_another_directory_as_a_worktree_is_made_gets_none_from_the_old_one(hosts, tmp_path):
    host, trees, workdir, one, two, _members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt"), _report("links repaired")]
    )
    del one
    # (The first test of this file has it made; here the session is found elsewhere once the row is written.)
    await wait_status(host, two.commission_id, "done", "working")
    host.release.set()
    await wait_status(host, two.commission_id, "done")

    from nekopower.host.ledger import Ledger

    add = Ledger.add_tree
    moved: list[str] = []

    async def moved_meanwhile(self: Any, **values: Any) -> Any:
        made = await add(self, **values)
        await self.update_session(values["session_id"], workdir=str(tmp_path))
        moved.append(values["id"])
        return made

    commission = await host.ledger.get(two.commission_id)
    kept = await host.ledger.get_session(commission.session_id)
    Ledger.add_tree = moved_meanwhile  # type: ignore[method-assign]
    try:
        row, why, _detail = await host.task_agents._make_tree(commission, kept, str(workdir))
    finally:
        Ledger.add_tree = add  # type: ignore[method-assign]
    assert (row, why) == (None, "moved") and moved and await host.ledger.trees() == [] and len(trees.made) == 1


async def test_a_worktree_left_half_made_is_made_anew_when_its_commission_starts(hosts, tmp_path):
    host, trees, workdir, one, two, _members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt"), _report("links repaired")]
    )
    host.release.set()
    await wait_status(host, two.commission_id, "done")
    commission = await host.ledger.get(two.commission_id)
    kept = await host.ledger.get_session(commission.session_id)
    path = host.paths.worktrees_dir() / "halfmade"
    await host.ledger.add_tree(
        id="halfmade", commission_id=commission.id, session_id=kept.id, project="Shop site",
        project_dir=str(workdir), path=str(path), workdir=str(path),
    )

    row = await host.task_agents._tree_to_work_in(commission, kept, str(workdir), True)

    assert row is not None and row.id == commission.id[:12] and row.state == "active"
    assert [tree.id for tree in await host.ledger.trees()] == [row.id]
    # Made anew as one that was never finished: nothing is said of a worktree that was lost.
    assert await _events(host, commission.id, "tree_lost") == []
    # One whose session is to have none, or cannot change files, gets none.
    await host.task_agents._remove_tree(row)
    assert await host.task_agents._tree_to_work_in(commission, kept, str(workdir), False) is None
    first = await host.ledger.get_session((await host.ledger.get(one.commission_id)).session_id)
    assert await host.task_agents._tree_to_work_in(commission, first, str(workdir), True) is None

    # One left half made under another session's name (the commission went on to this one since),
    # where no other can be made: this session has none, and is not taken for one that works in one.
    await host.ledger.add_tree(
        id="halfmade2", commission_id=commission.id, session_id="another-session", project="Shop site",
        project_dir=str(workdir), path=str(path), workdir=str(path),
    )
    trees.fail_create = WorktreeUnavailable("disk_space", "1 bytes free, 2 needed")
    assert await host.task_agents._tree_to_work_in(commission, kept, str(workdir), True) is None
    assert kept.id not in host.task_agents._tree_sessions and commission.id not in host.task_agents._tree_of
    assert await host.ledger.trees() == []


async def test_one_decision_on_a_worktree_at_a_time_and_none_on_one_that_is_gone(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    trees.gate = asyncio.Event()
    trees.merging.clear()
    # (As if an earlier process had made it.)
    host.task_agents._trees_mine.clear()
    landing = asyncio.ensure_future(host.land_worktree(tree.id))
    await asyncio.wait_for(trees.merging.wait(), 10)
    for decide in (host.land_worktree, host.discard_worktree, host.keep_worktree):
        with pytest.raises(ValueError, match="busy"):
            await decide(tree.id)
    # Nor is it gone through meanwhile as one that was left at work.
    assert await host.task_agents.reconcile_trees() == {"removed": [], "kept": []}
    assert (await host.ledger.get_tree(tree.id)).state == "landing"
    trees.results = [MergeResult("failed", detail="the disk is full")]
    trees.gate.set()
    answer = await landing
    assert (answer["status"], answer["detail"]) == ("failed", "the disk is full")
    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason) == ("unlanded", "failed")

    # Removed by hand since, and the host started anew: it cannot be landed, and can be taken off the record.
    shutil.rmtree(tree.path)
    host.task_agents._tree_handles.clear()
    with pytest.raises(ValueError, match="gone"):
        await host.land_worktree(tree.id)
    assert await host.discard_worktree(tree.id) == {"removed": True} and await host.ledger.trees() == []
    for decide in (host.land_worktree, host.discard_worktree, host.keep_worktree):
        with pytest.raises(LookupError):
            await decide(tree.id)


async def test_what_was_left_over_is_looked_at_in_the_background_once_the_host_is_in_use(hosts, tmp_path):
    host, trees = _host(hosts, tmp_path, ScriptedLLM([]))
    home = tmp_path / "site"
    home.mkdir()
    await host.space.touch_project("Site", workdir=str(home))
    path = host.paths.worktrees_dir() / "leftclean"
    path.mkdir(parents=True)
    await host.ledger.add_tree(id="leftclean", project="Site", project_dir=str(home), path=str(path), workdir=str(path), state="active")
    assert host._trees_looked_at is None

    assert [item["id"] for item in await host.worktrees()] == ["leftclean"]
    await asyncio.wait_for(host._trees_looked_at, 10)
    assert await host.worktrees() == [] and trees.removed == ["leftclean"]


def test_files_outside_the_projects_directory_are_told_apart_and_long_lists_are_counted():
    from nekopower.host.tree_work import files_of, files_text, names_text, outside_of

    changed = [
        FileChange("site/a.txt", "modified", 2, 1), FileChange("site/new.txt", "added", 3, 0),
        FileChange("site/gone.txt", "deleted", 0, 4), FileChange("site/logo.png", "modified", None, None),
        FileChange("tools/build.py", "modified", 1, 1), FileChange("sitemap/x.txt", "added", 1, 0),
    ]
    assert outside_of(changed, "site") == ["tools/build.py", "sitemap/x.txt"]
    assert outside_of(changed, "") == []
    files, more = files_of(changed, 4)
    assert more == 2 and [item["path"] for item in files] == [item.path for item in changed[:4]]
    said = files_text(files, more, 300)
    assert said == "site/a.txt (+2 -1), site/new.txt (new), site/gone.txt (deleted), site/logo.png (binary) and 2 more"
    assert names_text(["a", "b", "c"], 2, 300) == "a, b and 1 more" and names_text(["a"], 2, 300) == "a"


async def test_every_new_kind_of_timeline_entry_says_what_a_page_shows(tmp_path):
    from nekopower.host.ledger import Ledger
    from nekopower.host.timeline import TIMELINE_KINDS

    ledger = Ledger(tmp_path / "ledger.db")
    try:
        commission, _created = await ledger.create(
            lanlan_name="Lan", title="redo the footer", dispatch_text="redo the footer", user_text="", source="dispatch",
            route="task_agent", project="Shop site",
        )
        said = [
            ("landed", {"tree": "abc", "files": [{"path": "a.txt", "status": "added", "added": 1, "deleted": 0}, "x"],
                        "more": 2, "lines_added": 1, "lines_deleted": 0, "skipped": ["big.bin"],
                        "outside": ["tools/build.py", 5], "by": "user"}),
            ("landing_conflict", {"tree": "abc", "conflicts": ["a.txt", 7], "round": 2}),
            ("not_landed", {"tree": "abc", "path": "C:/wt/abc", "reason": "conflict", "conflicts": ["a.txt"], "rounds": 3,
                            "detail": "secret detail", "left_changed": [], "kept_copies": ""}),
            ("landing_held", {"tree": "abc", "path": "C:/wt/abc", "reason": "ask"}),
            ("tree_discarded", {"tree": "abc", "path": "C:/wt/abc"}),
            ("tree_kept", {"tree": "abc", "path": "C:/wt/abc"}),
            ("team_formed", {"members": [{"name": "links", "preset": "worker", "commission_id": "c1", "writes": True}, 3],
                             "inspect": True}),
            ("inspected", {"tree": "abc", "verdict": "fail", "round": 1}),
            ("stall_reminded", {"members": 2}),
            # Kept in the ledger, and no line of the timeline.
            ("tree_made", {"tree": "abc", "path": "C:/wt/abc", "seconds": 1.0}),
            ("tree_not_made", {"reason": "not_a_repository", "detail": ""}),
            ("told_of_landing", {"tree": "abc", "text": "for your information"}),
            ("member_made", {"session": "s", "changes_files": True}),
        ]
        for kind, payload in said:
            await ledger.append_event(commission.id, kind, payload)
        page = await ledger.timeline(project="Shop site", limit=100)
    finally:
        await ledger.close()

    entries = {entry["kind"]: entry for entry in page["entries"]}
    assert [kind for kind, _payload in said[:9]] == [entry["kind"] for entry in page["entries"]][-9:]
    assert set(entries) <= set(TIMELINE_KINDS) and "tree_made" not in entries and "member_made" not in entries
    assert entries["landed"]["detail"] == {
        "tree": "abc", "files": [{"path": "a.txt", "status": "added", "added": 1, "deleted": 0}], "more": 2,
        "lines_added": 1, "lines_deleted": 0, "outside": ["tools/build.py"], "skipped": ["big.bin"], "by_user": True,
    }
    assert entries["landing_conflict"]["detail"] == {"tree": "abc", "conflicts": ["a.txt"], "round": 2, "by_user": False}
    assert entries["not_landed"]["detail"] == {
        "tree": "abc", "path": "C:/wt/abc", "reason": "conflict", "conflicts": ["a.txt"], "rounds": 3,
    }
    assert entries["not_landed"]["text"] == "C:/wt/abc" and "secret detail" not in json.dumps(page)
    assert entries["landing_held"]["detail"] == {"tree": "abc", "path": "C:/wt/abc", "reason": "ask"}
    for kind in ("tree_discarded", "tree_kept"):
        assert entries[kind]["peer"]["type"] == "user" and entries[kind]["detail"] == {"tree": "abc", "path": "C:/wt/abc"}
    assert entries["team_formed"]["detail"] == {
        "members": [{"name": "links", "preset": "worker", "commission_id": "c1", "writes": True}], "inspect": True,
    }
    assert entries["inspected"]["detail"] == {"tree": "abc", "verdict": "fail", "round": 1}
    assert entries["stall_reminded"]["detail"] == {"members": 2}


async def test_what_a_commission_that_is_over_changed_is_not_landed(hosts, tmp_path, monkeypatch):
    from nekopower.host.tree_work import _Done

    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt", "half"), [wait, *_report("links repaired")]]
    )
    await wait_until(lambda: len(members["broken links"].requests) == 2, what="the second one at work")

    async def not_yet(_commission: Any) -> None:
        return None

    # Stopped, and what sees to its worktree has not come by yet: a landing that was under way lands nothing.
    monkeypatch.setattr(host.task_agents, "_tree_left", not_yet)
    assert await host.abort(two.commission_id)
    done = _Done(text="links repaired", reason="the task agent reported ready", delivery=None, sent=1)
    done.run_over.set()
    host.task_agents._dones[two.commission_id] = done
    assert await host.task_agents._land_once(two.commission_id, done) == "over"
    assert trees.merged == [] and not (workdir / "links.txt").exists()
    assert two.commission_id not in host.task_agents._dones
    hold.set()
    host.release.set()


async def test_a_member_that_only_reads_gets_no_worktree_beside_another(hosts, tmp_path):
    leader_llm = ScriptedLLM([*_hand_over(), *_hand_over(preset="scout", description="looks things up")])
    members = {
        "the header": ScriptedLLM([_report("header fixed")]),
        "broken links": ScriptedLLM([_report("three of them are broken")]),
    }
    host, trees, workdir = await _shop(hosts, tmp_path, _by_role(leader_llm, members))
    await wait_status(host, (await _dispatch(host, FIRST)).commission_id, "done")
    two = await _dispatch(host, SECOND)
    done = await wait_status(host, two.commission_id, "done")

    scout = await host.ledger.get_session(done.session_id)
    # (Made as one that only reads, and on record as that.)
    assert (scout.preset, scout.seat) == ("scout", "reads") and trees.made == [] and done.workdir == str(workdir)
    assert await _events(host, two.commission_id, "tree_not_made") == []


@pytest.mark.parametrize("merged", ["some of it", "nothing else"])
async def test_what_is_never_carried_over_stays_in_its_worktree_for_the_user(hosts, tmp_path, merged):
    host, trees, workdir, one, two, _members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt", "repaired"), _report("links repaired")]
    )
    left = ("video.mp4", "vendor/")
    if merged == "some of it":
        trees.results = [MergeResult("landed", files=(FileChange("links.txt", "added", 1, 0),), lines_added=1, skipped=left)]
    else:
        trees.results = [MergeResult("unchanged", skipped=left)]
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")

    # The commission is done, and the worktree is kept: removed, what was not carried over would be gone.
    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason, tree.conflicts) == ("unlanded", "skipped", left)
    assert trees.removed == [] and Path(tree.path).is_dir() and two.commission_id not in host.task_agents._tree_of
    assert "Not merged, being too large or a repository of their own: video.mp4, vendor/" in done.result_summary
    assert tree.workdir in done.result_summary
    assert ("Merged into the project's directory" in done.result_summary) == (merged == "some of it")
    landed = await _events(host, two.commission_id, "landed")
    assert [item["skipped"] for item in landed] == ([list(left)] if merged == "some of it" else [])
    (kept,) = await _events(host, two.commission_id, "not_landed")
    assert (kept["reason"], kept["conflicts"], kept["path"]) == ("skipped", list(left), tree.workdir)
    # What the user is shown says so in the language of the dialogue.
    _told, shown = host.task_agents._landed_texts(
        tree, "zh", [{"path": "links.txt", "status": "added", "added": 1, "deleted": 0}], 2, ["tools/x.py"], left
    )
    assert shown.startswith("已合回项目目录：links.txt 等另外 2 个") and "其中在项目目录以外（仓库里别处）：tools/x.py" in shown
    assert f"有文件没合回（太大，或者本身是一个仓库）：video.mp4, vendor/。它们留在：{tree.workdir}" in shown
    assert set(brain_texts.TREE_LANDED_TEXTS) == set(brain_texts.TREE_NOT_LANDED_TEXTS)
    assert len({frozenset(words) for words in brain_texts.TREE_LANDED_TEXTS.values()}) == 1

    # Tried again at the user's word, what is left is still not carried over: it stays, until it is discarded.
    trees.results = [MergeResult("unchanged", skipped=("video.mp4",))]
    answer = await host.land_worktree(tree.id)
    assert (answer["status"], answer["skipped"]) == ("unchanged", ["video.mp4"])
    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason, tree.conflicts) == ("unlanded", "skipped", ("video.mp4",)) and trees.removed == []
    assert len(await _events(host, two.commission_id, "landed")) == len(landed)
    assert await host.discard_worktree(tree.id) == {"removed": True}
    assert (await host.ledger.get(two.commission_id)).status == "done" and await host.ledger.trees() == []


async def test_what_the_user_allows_during_a_landing_is_done_once_it_is_through(hosts, tmp_path):
    hold = asyncio.Event()

    async def until_asked() -> None:
        await hold.wait()

    outside = tmp_path / "outside.txt"
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "repaired"), [until_asked, *_report("links repaired")]],
        # Writing outside its directory is asked about: it waits for the user in the middle of a run.
        first=lambda wait: [[wait, *_write(str(outside), "allowed")], _report("header fixed")],
    )
    await wait_until(lambda: len(members["broken links"].requests) == 2, what="the second member having written")
    host.release.set()
    await wait_status(host, one.commission_id, "waiting_user")
    trees.gate = asyncio.Event()
    hold.set()
    await asyncio.wait_for(trees.merging.wait(), 10)

    # Answered while the merge runs: what was allowed is not done under it.
    assert await host.answer(one.commission_id, "", choice="once")
    await asyncio.sleep(0.6)
    assert not outside.exists() and (await host.ledger.get(two.commission_id)).status == "working"
    trees.gate.set()
    await wait_status(host, two.commission_id, "done")
    await wait_status(host, one.commission_id, "done")
    assert outside.read_text() == "allowed"


@pytest.mark.parametrize(
    "first", ["going through what was left", "going through what was left and removing it", "the commission going on"]
)
async def test_a_worktree_is_in_one_hand_at_a_time_as_the_host_starts(hosts, tmp_path, first):
    more = asyncio.Event()

    async def wait() -> None:
        await more.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "half"), _report("which pages?", status="blocked"),
                [wait, *_write("more.txt")], _report("all of them")],
    )
    host.release.set()
    await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="its worktree being seen to")
    (tree,) = await host.ledger.trees()
    # As after the host was started anew with the commission's worktree left at work.
    host.task_agents._trees_mine.clear()
    host.task_agents._tree_handles.clear()
    await host.ledger.update_tree(tree.id, state="active")

    removing = first.endswith("removing it")
    if removing:
        # Nothing is in it any more: gone through, it is removed.
        (Path(tree.path) / "links.txt").unlink()
    if first.startswith("going through what was left"):
        trees.changes_gate = asyncio.Event()
        trees.checking.clear()
        going = asyncio.ensure_future(host.task_agents.reconcile_trees())
        await asyncio.wait_for(trees.checking.wait(), 10)
        telling = asyncio.ensure_future(host.tell(two.commission_id, "every page"))
        await asyncio.sleep(0.6)
        # The commission does not go on in it while it is gone through.
        assert tree.id in host.task_agents._trees_in_hand and two.commission_id not in host.task_agents._tree_of
        assert len(members["broken links"].requests) == 2
        trees.changes_gate.set()
        assert await going == ({"removed": [tree.id], "kept": []} if removing else {"removed": [], "kept": [tree.id]})
        assert await telling
    else:
        assert await host.tell(two.commission_id, "every page")
        await wait_until(lambda: len(members["broken links"].requests) == 3, what="it being at work again")
        # At work in it: what goes through those left over passes it by.
        assert await host.task_agents.reconcile_trees() == {"removed": [], "kept": []}
        assert (await host.ledger.get_tree(tree.id)).state == "active"
    more.set()
    done = await wait_status(host, two.commission_id, "done")

    if removing:
        # It found its worktree gone from the record once that was through, and went on in one made anew.
        assert trees.made == [Path(tree.path)] * 2 and trees.removed == [tree.id] * 2
        assert not (workdir / "links.txt").exists() and (workdir / "more.txt").exists()
    else:
        # The same worktree, with what it had changed before: nothing was removed under it, and both files landed.
        assert trees.made == [Path(tree.path)] and trees.removed == [tree.id]
        assert (workdir / "links.txt").read_text() == "half" and (workdir / "more.txt").exists()
    assert done.result_summary.startswith("all of them") and await host.ledger.trees() == []


async def test_what_is_read_in_a_worktree_without_asking_is_the_projects_place_in_it(hosts, tmp_path):
    host, _trees = _host(hosts, tmp_path, ScriptedLLM([]))
    path = host.paths.worktrees_dir() / "abc"
    await host.ledger.add_tree(
        id="abc", session_id="s1", project="Site", project_dir=str(tmp_path / "repo" / "site"), path=str(path),
        workdir=str(path / "site"), state="active",
    )
    # A worktree is of the whole repository: what lies beside the project in it is no session's working directory.
    assert await host.task_agents._tree_places() == [("s1", str(path / "site"), None)]
    # Its own is not another's place to the session that works in it.
    assert await host.task_agents._tree_places(but="s1") == []


# ---------------------------------------------------------------------------
# The ledger, the board, the timeline
# ---------------------------------------------------------------------------


async def test_a_session_that_was_its_own_before_it_became_a_projects_stays_where_it_works(tmp_path):
    from nekopower.host.ledger import TREE_STATES, Ledger

    ledger = Ledger(tmp_path / "ledger.db")
    try:
        own = await ledger.create_session(name="report", lanlan_name="Lan", kind="single", workdir=str(tmp_path / "old"))
        fresh = await ledger.create_session(name="other", lanlan_name="Lan", kind="single")
        member = await ledger.create_session(
            name="Reports", lanlan_name="Lan", kind="line", project="Reports", workdir=str(tmp_path / "old")
        )
        await ledger.adopt([own.id, fresh.id], "Reports")
        seats = {item.id: item.seat for item in await ledger.members("Lan", "Reports")}
        assert seats == {own.id: "own", fresh.id: "", member.id: ""}

        # Whatever says that it moves: one on record as working in its own directory does not.
        before = await ledger.move_project("Reports", str(tmp_path / "new"), moves=lambda _id, _workdir: True)
        assert set(before) == {fresh.id, member.id}
        assert (await ledger.get_session(own.id)).workdir == str(tmp_path / "old")
        assert (await ledger.get_session(member.id)).workdir == str(tmp_path / "new")

        # A worktree that holds work not landed keeps the project where it is; one left to the user does not.
        tree = await ledger.add_tree(id="abc", project="Reports", project_dir="p", path="t", workdir="t", state="unlanded")
        assert TREE_STATES == ("making", "active", "landing", "resolving", "held", "unlanded", "kept", "discarding")
        assert await ledger.trees_pending("Reports")
        assert await ledger.move_project("Reports", str(tmp_path / "third"), moves=lambda _id, _workdir: True) is None
        assert (await ledger.update_tree(tree.id, state="kept", was=("unlanded",))).state == "kept"
        assert await ledger.update_tree(tree.id, state="active", was=("unlanded",)) is None
        assert not await ledger.trees_pending("Reports")
        assert await ledger.move_project("Reports", str(tmp_path / "third"), moves=lambda _id, _workdir: True) is not None
        with pytest.raises(ValueError):
            await ledger.add_tree(id="abc", project_dir="p", path="t", workdir="t")
        with pytest.raises(ValueError):
            await ledger.update_tree(tree.id, state="nowhere")
        with pytest.raises(ValueError):
            await ledger.create_session(name="x", lanlan_name="Lan", seat="window")
        assert await ledger.drop_tree(tree.id) and not await ledger.drop_tree(tree.id)
    finally:
        await ledger.close()


async def test_the_board_and_the_timeline_tell_of_landings(hosts, tmp_path):
    assert STATES[5:] == ("landing", "resolving", "held", "unlanded")
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()

    cards = {card["id"]: card for card in (await host.board())["projects"][0]["members"]}
    card = cards[tree.session_id]
    assert card["state"] == "unlanded" and card["workdir"] == str(workdir)
    assert card["tree"] == {
        "id": tree.id, "path": tree.path, "workdir": tree.workdir, "state": "unlanded", "rounds": 3, "checks": 0,
        "conflicts": ["links.txt"], "reason": "conflict", "project_dir": str(workdir), "commission_id": two.commission_id,
    }
    assert [other["tree"] for other in cards.values() if other["id"] != tree.session_id] == [None, None]

    await host.keep_worktree(tree.id)
    entries = (await host.timeline(project="Shop site"))["entries"]
    mine = [entry for entry in entries if entry["commission_id"] == two.commission_id]
    kinds = [entry["kind"] for entry in mine]
    assert [kind for kind in kinds if kind in ("landing_conflict", "not_landed", "tree_kept")] == [
        "landing_conflict", "landing_conflict", "landing_conflict", "not_landed", "tree_kept",
    ]
    left = next(entry for entry in mine if entry["kind"] == "not_landed")
    assert left["detail"] == {
        "tree": tree.id, "path": tree.workdir, "reason": "conflict", "conflicts": ["links.txt"], "rounds": 3,
    }
    first = next(entry for entry in mine if entry["kind"] == "landing_conflict")
    assert first["detail"] == {"tree": tree.id, "conflicts": ["links.txt"], "round": 1, "by_user": False}
    kept = next(entry for entry in mine if entry["kind"] == "tree_kept")
    assert kept["peer"]["type"] == "user" and kept["detail"] == {"tree": tree.id, "path": tree.workdir}
    # Kept by the user: the card says so, and is shown as the session stands.
    card = {item["id"]: item for item in (await host.board())["projects"][0]["members"]}[tree.session_id]
    assert card["tree"]["state"] == "kept" and card["state"] in ("idle", "asleep")


# ---------------------------------------------------------------------------
# What reviewers found
# ---------------------------------------------------------------------------


def test_conflict_markers_are_found_wherever_they_are_in_a_file(tmp_path, monkeypatch):
    from nekopower.host import tree_work

    monkeypatch.setattr(tree_work, "_MARKERS_CHUNK_BYTES", 16)

    def holds(data: bytes) -> bool:
        file = tmp_path / "f.txt"
        file.write_bytes(data)
        return tree_work._has_markers(file)

    assert holds(b"<<<<<<< ours\nmine\n")
    # Far into the file, beyond what is read at once.
    assert holds(b"line\n" * 40 + b">>>>>>> theirs\n")
    # The end of a line and the marker lie across two pieces, at every place they can.
    for before in range(8, 26):
        assert holds(b"a" * before + b"\n<<<<<<< ours\n"), before
    assert holds(b"one\r>>>>>>> theirs\rtwo")
    # Not at the start of a line: not a marker.
    assert not holds(b"see <<<<<<< here\n" * 5) and not holds(b"plain\n" * 30)
    assert not tree_work._has_markers(tmp_path / "missing.txt")


def _stand_ins(number: int, home: Path) -> tuple[Any, Any]:
    """A commission and its session, as far as making a worktree looks at them."""
    commission = types.SimpleNamespace(id=str(number) * 32, lanlan_name="Lan", project="Site")
    kept = types.SimpleNamespace(id=f"s{number}", project="Site", workdir=str(home))
    return commission, kept


async def test_two_that_start_at_once_do_not_both_take_the_last_room(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKTREES_PER_PROJECT", 1)
    host, trees = _host(hosts, tmp_path, ScriptedLLM([]))
    home = tmp_path / "site"
    home.mkdir()

    made = await asyncio.gather(
        *(host.task_agents._make_tree(*_stand_ins(number, home), str(home)) for number in (1, 2))
    )

    assert sorted(why for _row, why, _detail in made) == ["", "too_many"]
    assert len(await host.ledger.trees()) == 1 and len(trees.made) == 1


@pytest.mark.parametrize("first", ["one being made", "going through what was left"])
async def test_no_worktree_is_made_while_those_left_over_are_gone_through_nor_the_other_way_round(hosts, tmp_path, first):
    host, trees = _host(hosts, tmp_path, ScriptedLLM([]))
    home = tmp_path / "site"
    home.mkdir()
    await host.space.touch_project("Site", workdir=str(home))
    manager = host.task_agents
    if first == "one being made":
        trees.create_gate = asyncio.Event()
        making = asyncio.ensure_future(manager._make_tree(*_stand_ins(1, home), str(home)))
        await asyncio.wait_for(trees.creating.wait(), 10)
        going = asyncio.ensure_future(manager.reconcile_trees())
        await asyncio.sleep(0.6)
        # On record and not there yet: what goes through those left over waits until it stands.
        assert trees.reconciled == [] and not going.done()
        trees.create_gate.set()
        _row, why, _detail = await making
        assert why == "" and await going == {"removed": [], "kept": []}
        assert trees.reconciled == [str(home)] and [tree.state for tree in await host.ledger.trees()] == ["active"]
    else:
        left = host.paths.worktrees_dir() / "left"
        left.mkdir(parents=True)
        (left / "a.txt").write_text("x")
        await host.ledger.add_tree(
            id="left", project="Site", project_dir=str(home), path=str(left), workdir=str(left), state="active"
        )
        trees.changes_gate = asyncio.Event()
        going = asyncio.ensure_future(manager.reconcile_trees())
        await asyncio.wait_for(trees.checking.wait(), 10)
        making = asyncio.ensure_future(manager._make_tree(*_stand_ins(1, home), str(home)))
        await asyncio.sleep(0.6)
        assert trees.creates == 0 and not making.done()
        trees.changes_gate.set()
        assert (await going)["kept"] == ["left"]
        _row, why, _detail = await making
        assert why == "" and len(trees.made) == 1


async def test_a_landing_that_waits_for_a_run_goes_by_how_the_commission_said_it_is_done_last(hosts, tmp_path):
    from nekopower.host.tree_work import _Done

    host, _trees = _host(hosts, tmp_path, ScriptedLLM([]))
    manager = host.task_agents
    landed: list[Any] = []

    async def land_once(_commission_id: str, done: Any) -> str:
        landed.append(done)
        manager._dones.pop("c1", None)
        return "over"

    manager._land_once = land_once
    first = _Done(text="first", reason="", delivery=None, sent=1)
    manager._dones["c1"] = first
    landing = asyncio.ensure_future(manager._land("c1"))
    await asyncio.sleep(0.2)
    # More was said to it before the run that said so was over: that run's end says it is done anew.
    second = _Done(text="second", reason="", delivery=None, sent=2)
    manager._replace_done("c1", second)
    await asyncio.sleep(0.2)
    assert landed == [] and not landing.done()
    second.run_over.set()
    await asyncio.wait_for(landing, 10)
    assert landed == [second]


def _slow_to_stop(hold: asyncio.Event) -> Any:
    """A step of a run that is over a while after it was told to stop."""

    async def stubborn() -> None:
        try:
            await hold.wait()
        except asyncio.CancelledError:
            await asyncio.sleep(1.0)
            raise

    return stubborn


async def test_a_task_agent_a_member_started_in_its_worktree_is_stopped_before_what_they_changed_is_merged(hosts, tmp_path):
    hold = asyncio.Event()
    helper = ScriptedLLM([[_slow_to_stop(hold), *_write("styles.css", "half")]])

    async def until_it_works() -> None:
        await wait_until(lambda: len(helper.requests) >= 1, what="the task agent it started being at work")

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[
            [call("subagent", {"description": "styles", "prompt": "Tidy the stylesheet. PART-STYLES"}), finish("tool_calls")],
            [until_it_works, *_report("links repaired")],
        ],
        **{"PART-STYLES": helper},
    )
    seen: dict[str, Any] = {}
    merge_back = trees.merge_back

    async def merging(worktree: Any, landing: Any = None) -> Any:
        (child,) = await host.ledger.select(parent_id=two.commission_id)
        live = host.task_agents._sessions.get(child.id)
        seen.update(status=child.status, at_it=live is not None and not live.idle)
        return await merge_back(worktree, landing)

    trees.merge_back = merging
    host.release.set()
    await wait_status(host, two.commission_id, "done")
    # Stopped as the commission said it is done, and out of the worktree before that was read.
    assert seen == {"status": "cancelled", "at_it": False}
    hold.set()


async def test_held_back_for_the_user_the_files_that_clashed_last_stay_on_record(hosts, tmp_path):
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "mine"), _report("links repaired"), [wait, *_write("links.txt", "both")], _report("merged")],
    )
    trees.results = [MergeResult("conflict", conflicts=("links.txt",))]
    host.release.set()
    await wait_until(lambda: len(members["broken links"].requests) == 3, what="it merging what clashed")
    # The user wants to be asked first from now on.
    await host.set_landing_settings(mode="ask")
    hold.set()
    await wait_status(host, two.commission_id, "blocked")

    (tree,) = await host.ledger.trees()
    # What clashed is still looked at for markers when the user has it landed.
    assert (tree.state, tree.reason, tree.conflicts) == ("held", "ask", ("links.txt",))


async def test_a_worktree_is_looked_at_for_changes_once_the_run_that_was_stopped_is_over(hosts, tmp_path):
    hold = asyncio.Event()
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt", "half"), [_slow_to_stop(hold), *_report("links repaired")]]
    )
    await wait_until(lambda: len(members["broken links"].requests) == 2, what="the second one at work")
    seen: list[bool] = []
    has_changes = trees.has_changes

    async def looking(worktree: Any) -> bool:
        live = host.task_agents._sessions.get(two.commission_id)
        seen.append(live is not None and not live.idle)
        return await has_changes(worktree)

    trees.has_changes = looking
    assert await host.abort(two.commission_id)
    await wait_until(lambda: seen, what="its worktree being looked at")
    assert seen == [False]
    await wait_until(lambda: not host.task_agents._tree_tasks, what="that being through")
    hold.set()
    host.release.set()


async def test_a_worktree_that_cannot_be_opened_this_moment_is_not_taken_for_one_that_is_gone(hosts, tmp_path):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "half"), _report("which pages?", status="blocked"), _write("more.txt"), _report("all of them")],
    )
    host.release.set()
    await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="its worktree being seen to")
    (tree,) = await host.ledger.trees()
    # As after the host was started anew, with git not answering just now.
    manager = host.task_agents
    manager._trees_mine.clear()
    manager._tree_handles.clear()
    trees.fail_open = True

    # Gone through as the host starts, it stays on record as it is.
    assert await manager.reconcile_trees() == {"removed": [], "kept": [tree.id]}
    assert await host.ledger.get_tree(tree.id) == tree and trees.removed == []
    # It cannot be landed now, and is not said to be gone.
    with pytest.raises(ValueError, match="not_opened"):
        await host.land_worktree(tree.id)
    # Nor is it said to be discarded: nothing of it could be removed, and it stays on record.
    with pytest.raises(ValueError, match="not_opened"):
        await host.discard_worktree(tree.id)
    assert await host.ledger.get_tree(tree.id) == tree and await _events(host, two.commission_id, "tree_discarded") == []
    assert (await host.ledger.get(two.commission_id)).status == "blocked"
    # Its commission is not given another worktree in its place: it does not go on, and what it changed stays.
    await host.tell(two.commission_id, "every page")
    failed = await wait_status(host, two.commission_id, "failed")
    assert "cannot be opened just now" in failed.status_reason
    assert await _events(host, two.commission_id, "tree_lost") == [] and len(trees.made) == 1
    assert await host.ledger.get_tree(tree.id) is not None and (Path(tree.workdir) / "links.txt").exists()

    trees.fail_open = False
    await wait_until(lambda: host.task_agents._held_by == {}, what="every session being let go")
    assert await host.tell(two.commission_id, "every page")
    await wait_status(host, two.commission_id, "done")
    assert (workdir / "links.txt").read_text() == "half" and (workdir / "more.txt").exists()


async def test_a_worktree_its_commission_goes_on_in_is_not_decided_on(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    # Its commission was taken up again this moment, and its row does not say so yet.
    host.task_agents._tree_of[two.commission_id] = tree.id
    for decide in (host.land_worktree, host.discard_worktree, host.keep_worktree):
        with pytest.raises(ValueError, match="at_work"):
            await decide(tree.id)
    assert tree.id not in host.task_agents._trees_in_hand and trees.removed == []
    del host.task_agents._tree_of[two.commission_id]
    assert (await host.land_worktree(tree.id))["status"] == "landed"


async def test_a_commission_that_failed_goes_back_to_merge_what_clashes_at_the_users_word(hosts, tmp_path):
    host, trees, workdir, two, members, _stuck = await _not_landed(hosts, tmp_path, [WorktreeError("git went away")])
    (tree,) = await host.ledger.trees()
    # As one that a host which ended under it left: failed, with what it changed kept.
    await host.ledger.transition(two.commission_id, "failed", reason="the host ended while it was worked on")
    members["broken links"].turns.extend([_write("links.txt", "both"), _report("merged by hand")])
    trees.results = [MergeResult("conflict", conflicts=("links.txt",))]

    answer = await host.land_worktree(tree.id)

    assert (answer["status"], answer["resolving"]) == ("conflict", True)
    done = await wait_status(host, two.commission_id, "done")
    assert (workdir / "links.txt").read_text() == "both" and done.result_summary.startswith("merged by hand")
    assert await host.ledger.trees() == []


def _end_fails_once(host: Any, commission_id: str) -> None:
    """Have putting a commission down as done fail once, as with a ledger that cannot be written this moment."""
    manager = host.task_agents
    end = manager._end
    failed: list[bool] = []

    async def failing(ended: str, status: str, *args: Any, **kwargs: Any) -> Any:
        if ended == commission_id and status == "done" and not failed:
            failed.append(True)
            raise RuntimeError("the ledger cannot be written")
        return await end(ended, status, *args, **kwargs)

    manager._end = failing


@pytest.mark.parametrize("by", ["the host", "the user"])
async def test_a_commission_whose_end_cannot_be_put_on_record_once_it_landed_is_not_left_at_work(hosts, tmp_path, by):
    if by == "the host":
        host, trees, workdir, one, two, _members = await _two_members(
            hosts, tmp_path, second=[_write("links.txt", "repaired"), _report("links repaired")]
        )
        _end_fails_once(host, two.commission_id)
        host.release.set()
    else:
        clash = MergeResult("conflict", conflicts=("links.txt",))
        host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
        (tree,) = await host.ledger.trees()
        trees.merged.clear()
        _end_fails_once(host, two.commission_id)
        assert (await host.land_worktree(tree.id))["status"] == "landed"

    failed = await wait_status(host, two.commission_id, "failed")
    assert failed.status_reason == "what came of it could not be put on record"
    # What it changed is in the project, merged once, and its worktree is gone.
    assert (workdir / "links.txt").exists() and len(trees.merged) == 1 and await host.ledger.trees() == []
    await wait_until(lambda: two.commission_id not in host.task_agents._tree_of, what="its worktree being forgotten")


async def test_a_task_agent_stopped_while_its_worktree_cannot_be_opened_leaves_it_on_record(hosts, tmp_path):
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt", "half"), [wait, *_report("links repaired")]]
    )
    await wait_until(lambda: len(members["broken links"].requests) == 2, what="the second one at work")
    (tree,) = await host.ledger.trees()
    # The host has no hold of it any more, and git does not answer just now.
    host.task_agents._tree_handles.clear()
    trees.fail_open = True

    assert await host.abort(two.commission_id)
    await wait_until(lambda: two.commission_id not in host.task_agents._tree_of, what="its worktree being seen to")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="that being through")

    # Whether it holds changes could not be told: kept for the user, as one that does.
    (left,) = await host.ledger.trees()
    assert (left.state, left.reason) == ("unlanded", "stopped") and Path(tree.path).is_dir() and trees.removed == []
    hold.set()
    host.release.set()


async def test_a_worktree_the_user_sees_to_personally_is_not_taken_back_when_its_commission_goes_on(hosts, tmp_path):
    from nekopower.host.board import tree_of

    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    await host.keep_worktree(tree.id)
    mine = await host.ledger.get_tree(tree.id)
    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    members["broken links"].turns.extend([[wait, *_write("more.txt", "more")], _report("all of them")])

    assert await host.tell(two.commission_id, "the other pages as well")
    await wait_until(lambda: len(trees.made) == 2, what="another worktree being made for it")
    await wait_status(host, two.commission_id, "working")

    # It goes on in one of its own, under another name; the user's is as the user left it.
    other = await host.ledger.get_tree(f"{tree.id}-2")
    assert other is not None and (other.state, other.commission_id) == ("active", two.commission_id)
    assert await host.ledger.get_tree(tree.id) == mine and mine.state == "kept"
    # Its card shows the one it works in, whichever is read first.
    commission = await host.ledger.get(two.commission_id)
    assert tree_of([mine, other], commission) == other and tree_of([other, mine], commission) == other
    hold.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.result_summary.startswith("all of them") and (workdir / "more.txt").read_text() == "more"
    # Nothing of the user's was merged, or removed.
    assert not (workdir / "links.txt").exists() and (Path(mine.workdir) / "links.txt").read_text() == "mine"
    assert await host.ledger.trees() == [mine] and trees.removed == [other.id]


@pytest.mark.parametrize("how", ["stopped meanwhile", "an error"])
async def test_a_start_that_comes_to_nothing_leaves_a_worktree_as_the_user_was_to_decide_on_it(hosts, tmp_path, how):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    transition = host.ledger.transition
    refused: list[str] = []

    async def stopped_meanwhile(commission_id: str, status: str, *args: Any, **kwargs: Any) -> Any:
        if commission_id == two.commission_id and status == "working" and not refused:
            refused.append(status)
            if how == "an error":
                raise RuntimeError("the ledger cannot be written")
            return dataclasses.replace(await transition(commission_id, "blocked", reason="still"), applied=False)
        return await transition(commission_id, status, *args, **kwargs)

    host.ledger.transition = stopped_meanwhile
    await host.tell(two.commission_id, "try once more")
    await wait_until(lambda: refused and two.commission_id not in host.task_agents._tree_of, what="the start being given up")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="its worktree being seen to")

    # Why it was not landed, and what clashed, still stand: the user decides on it as before.
    await wait_status(host, two.commission_id, "failed" if how == "an error" else "blocked")
    assert await host.ledger.get_tree(tree.id) == tree
    assert (tree.state, tree.reason, tree.rounds, tree.conflicts) == ("unlanded", "conflict", 3, ("links.txt",))
    assert (await host.land_worktree(tree.id))["status"] == "landed"


async def test_a_decision_that_fails_after_the_merge_does_not_leave_the_worktree_as_being_landed(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    remove = host.task_agents._remove_tree

    async def failing(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("the ledger cannot be written")

    host.task_agents._remove_tree = failing
    with pytest.raises(RuntimeError, match="cannot be written"):
        await host.land_worktree(tree.id)

    # Merged, and not taken off the record: the user's to decide on again, at once.
    left = await host.ledger.get_tree(tree.id)
    assert (left.state, left.reason) == ("unlanded", "failed") and (workdir / "links.txt").exists()
    host.task_agents._remove_tree = remove
    assert await host.discard_worktree(tree.id) == {"removed": True}


async def test_a_worktree_left_to_the_user_as_its_commission_was_about_to_go_on_is_the_users(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    members["broken links"].turns.extend([_write("more.txt", "more"), _report("all of them")])
    # The user's decision on it is being carried out as the commission is taken up again.
    host.task_agents._trees_in_hand.add(tree.id)
    telling = asyncio.ensure_future(host.tell(two.commission_id, "the other pages as well"))
    await asyncio.sleep(0.6)
    assert trees.made == [Path(tree.path)] and len(members["broken links"].requests) == 5
    await host.ledger.update_tree(tree.id, state="kept")
    host.task_agents._trees_in_hand.discard(tree.id)

    assert await telling
    await wait_status(host, two.commission_id, "done")
    # It went on in another, and the user's was not merged.
    assert len(trees.made) == 2 and (workdir / "more.txt").exists() and not (workdir / "links.txt").exists()
    assert [(item.id, item.state) for item in await host.ledger.trees()] == [(tree.id, "kept")]


async def test_a_worktree_being_landed_at_the_users_word_keeps_its_commission_from_being_stopped_with_its_parent(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    manager = host.task_agents
    trees.gate = asyncio.Event()
    trees.merging.clear()
    landing = asyncio.ensure_future(host.land_worktree(tree.id))
    await asyncio.wait_for(trees.merging.wait(), 10)

    # On record as being landed, which is not waiting for the user any more: still one of those
    # that are not ended with what started them.
    assert (await host.ledger.get_tree(tree.id)).state == "landing"
    assert two.commission_id in await manager._waits_in_tree()
    trees.gate.set()
    assert (await landing)["status"] == "landed"
    assert two.commission_id not in await manager._waits_in_tree()


async def test_a_worktree_its_commission_is_finding_again_is_passed_by_as_the_host_starts(hosts, tmp_path):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "half"), _report("which pages?", status="blocked"), _write("more.txt"), _report("all of them")],
    )
    host.release.set()
    await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="its worktree being seen to")
    (tree,) = await host.ledger.trees()
    # As after the host was started anew: nothing of it is in hand, and git is slow to open it.
    manager = host.task_agents
    manager._trees_mine.clear()
    manager._tree_handles.clear()
    trees.open_gate = asyncio.Event()

    telling = asyncio.ensure_future(host.tell(two.commission_id, "every page"))
    await asyncio.wait_for(trees.opening.wait(), 10)
    # Its commission took it and is still finding it again: not gone through as one that was left.
    assert await asyncio.wait_for(manager.reconcile_trees(), 5) == {"removed": [], "kept": []}
    trees.open_gate.set()
    assert await telling
    await wait_status(host, two.commission_id, "done")
    assert (workdir / "links.txt").read_text() == "half" and (workdir / "more.txt").exists()


async def test_a_card_shows_that_its_commission_waits_for_the_user_though_its_worktree_is_at_work(hosts, tmp_path):
    from nekopower.host.board import card_of

    hold = asyncio.Event()

    async def wait() -> None:
        await hold.wait()

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt", "mine"), _report("links repaired"), [wait, *_report("merged")]]
    )
    trees.results = [MergeResult("conflict", conflicts=("links.txt",))]
    host.release.set()
    await wait_until(lambda: len(members["broken links"].requests) == 3, what="it merging what clashed")
    (tree,) = await host.ledger.trees()
    commission = await host.ledger.get(two.commission_id)
    session = await host.ledger.get_session(commission.session_id)
    assert tree.state == "resolving" and card_of(session, commission, (), None, None, tree=tree)["state"] == "resolving"
    # It asks the user something while it merges: the card says that it waits.
    asking = dataclasses.replace(commission, status="waiting_user")
    assert card_of(session, asking, (), None, None, tree=tree)["state"] == "waiting_user"
    hold.set()
    await wait_status(host, two.commission_id, "done")


async def test_a_worktree_being_seen_to_after_its_run_is_not_gone_on_in_until_that_is_through(hosts, tmp_path):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "half"), _report("which pages?", status="blocked"), _write("more.txt"), _report("all of them")],
    )
    # Looking whether it holds changes takes a while.
    trees.changes_gate = asyncio.Event()
    host.release.set()
    await wait_status(host, two.commission_id, "blocked")
    await asyncio.wait_for(trees.checking.wait(), 10)
    (tree,) = await host.ledger.trees()
    manager = host.task_agents
    assert tree.id in manager._trees_in_hand and tree.state == "active"

    # The user says more to it meanwhile: it does not go on in the worktree under the look.
    telling = asyncio.ensure_future(host.tell(two.commission_id, "every page"))
    await asyncio.sleep(0.8)
    assert len(members["broken links"].requests) == 2 and two.commission_id not in manager._tree_of
    trees.changes_gate.set()
    assert await telling
    done = await wait_status(host, two.commission_id, "done")

    # The same worktree, as the look left it: nothing was removed under the commission, and both files landed.
    assert trees.made == [Path(tree.path)] and trees.removed == [tree.id]
    assert (workdir / "links.txt").read_text() == "half" and (workdir / "more.txt").exists()
    assert done.result_summary.startswith("all of them") and await host.ledger.trees() == []
    assert [item["reason"] for item in await _events(host, two.commission_id, "not_landed")] == ["stopped"]


async def test_a_worktree_taken_in_hand_as_its_commission_reads_it_again_is_waited_for(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    members["broken links"].turns.extend([_write("more.txt", "more"), _report("all of them")])
    manager = host.task_agents
    get_tree = host.ledger.get_tree
    taken: list[str] = []

    async def taken_meanwhile(tree_id: str) -> Any:
        row = await get_tree(tree_id)
        if not taken:
            # Somebody takes it in hand while the commission that is taken up again reads its row.
            taken.append(tree_id)
            manager._trees_in_hand.add(tree_id)
        return row

    host.ledger.get_tree = taken_meanwhile
    telling = asyncio.ensure_future(host.tell(two.commission_id, "the other pages as well"))
    await wait_until(lambda: taken, what="its row being read")
    await asyncio.sleep(0.6)
    # Not its own while it is in another hand: it has not gone on in it.
    assert two.commission_id not in manager._tree_of and len(members["broken links"].requests) == 5
    manager._trees_in_hand.discard(tree.id)
    assert await telling
    await wait_status(host, two.commission_id, "done")
    assert (workdir / "more.txt").exists() and await host.ledger.trees() == []


async def test_a_worktree_that_stands_otherwise_once_it_was_looked_at_after_its_run_is_not_written_over(hosts, tmp_path):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt", "half"), _report("which pages?", status="blocked")]
    )
    trees.changes_gate = asyncio.Event()
    host.release.set()
    await wait_status(host, two.commission_id, "blocked")
    await asyncio.wait_for(trees.checking.wait(), 10)
    (tree,) = await host.ledger.trees()
    # It was put on record otherwise while the look took its time.
    await host.ledger.update_tree(tree.id, state="kept")
    trees.changes_gate.set()
    await wait_until(lambda: not host.task_agents._tree_tasks, what="its worktree being seen to")

    assert (await host.ledger.get_tree(tree.id)).state == "kept" and tree.id not in host.task_agents._trees_in_hand
    assert await _events(host, two.commission_id, "not_landed") == []


async def test_what_was_left_over_is_looked_at_though_the_host_was_first_used_with_no_loop_running(hosts, tmp_path):
    host, trees = _host(hosts, tmp_path, ScriptedLLM([]))
    home = tmp_path / "site"
    home.mkdir()
    await host.space.touch_project("Site", workdir=str(home))
    path = host.paths.worktrees_dir() / "leftclean"
    path.mkdir(parents=True)
    await host.ledger.add_tree(id="leftclean", project="Site", project_dir=str(home), path=str(path), workdir=str(path), state="active")
    # As after it was first used before any loop ran: in use, and nothing gone through yet.
    host._used = True
    assert host._trees_looked_at is None

    assert [item["id"] for item in await host.worktrees()] == ["leftclean"]
    looked = host._trees_looked_at
    await asyncio.wait_for(looked, 10)
    assert await host.worktrees() == [] and trees.removed == ["leftclean"]
    # Once: used again, it is not gone through anew.
    assert host._trees_looked_at is looked and trees.reconciled == [str(home)]


async def test_asked_first_what_was_said_to_it_meanwhile_is_done_before_it_is_held_back(hosts, tmp_path):
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[_write("links.txt", "repaired"), _report("links repaired"),
                _write("sitemap.txt", "new"), _report("the sitemap is there as well")],
    )
    await host.set_landing_settings(mode="ask")
    manager = host.task_agents
    get = manager.landing.get
    reading, read = asyncio.Event(), asyncio.Event()

    async def slow() -> Any:
        # The setting takes a while to read, the first time.
        if not reading.is_set():
            reading.set()
            await read.wait()
        return await get()

    manager.landing.get = slow
    await asyncio.wait_for(reading.wait(), 10)
    assert await host.tell(two.commission_id, "write the sitemap anew as well")
    await wait_until(lambda: len(members["broken links"].requests) >= 3, what="it being at work again")
    read.set()
    held = await wait_status(host, two.commission_id, "blocked")

    # Held back once, with all it changed, told with what it said last.
    assert held.result_summary.startswith("the sitemap is there as well")
    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason) == ("held", "ask") and (Path(tree.workdir) / "sitemap.txt").exists()
    assert len(await _events(host, two.commission_id, "landing_held")) == 1 and trees.merged == []
    host.release.set()


async def test_a_worktree_is_not_the_users_to_decide_on_before_its_commission_is_put_down_as_stuck(hosts, tmp_path):
    host, trees, workdir, one, two, _members = await _two_members(
        hosts, tmp_path, second=[_write("links.txt", "repaired"), _report("links repaired")]
    )
    trees.results = [MergeResult("failed", detail="the disk is full")]
    manager = host.task_agents
    end = manager._end
    ending, go = asyncio.Event(), asyncio.Event()

    async def slow(commission_id: str, status: str, *args: Any, **kwargs: Any) -> Any:
        if commission_id == two.commission_id and status == "blocked":
            ending.set()
            await go.wait()
        return await end(commission_id, status, *args, **kwargs)

    manager._end = slow
    host.release.set()
    await asyncio.wait_for(ending.wait(), 10)
    (tree,) = await host.ledger.trees()
    # On record as not landed, with its commission still at work: no decision is taken on it yet.
    assert tree.state == "unlanded" and (await host.ledger.get(two.commission_id)).status == "working"
    for decide in (host.land_worktree, host.discard_worktree, host.keep_worktree):
        with pytest.raises(ValueError, match="busy"):
            await decide(tree.id)
    go.set()
    await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: tree.id not in manager._trees_in_hand, what="its worktree being out of hand")
    assert (await host.land_worktree(tree.id))["status"] == "landed"
    await wait_status(host, two.commission_id, "done")


@pytest.mark.parametrize("decision", ["landed", "clashes", "discarded"])
async def test_what_is_decided_on_a_worktree_the_user_kept_does_not_settle_a_commission_stuck_over_another(
    hosts, tmp_path, decision
):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (first,) = await host.ledger.trees()
    await host.keep_worktree(first.id)
    await host.set_landing_settings(mode="ask")
    members["broken links"].turns.extend([_write("more.txt", "more"), _report("all of them")])
    assert await host.tell(two.commission_id, "the other pages as well")

    async def second_held() -> Any:
        row = await host.ledger.get_tree(f"{first.id}-2")
        return row if row is not None and row.state == "held" else None

    await wait_until(second_held, what="its other worktree being held back")
    await wait_status(host, two.commission_id, "blocked")
    await wait_until(lambda: not host.task_agents._trees_in_hand, what="nothing being in hand")
    second = await second_held()

    # The commission is stuck over the second one: what becomes of the first settles the first alone.
    if decision == "landed":
        assert (await host.land_worktree(first.id))["status"] == "landed" and (workdir / "links.txt").exists()
        assert await host.ledger.get_tree(first.id) is None
    elif decision == "clashes":
        trees.results = [clash]
        answer = await host.land_worktree(first.id)
        assert (answer["status"], answer["resolving"]) == ("conflict", False)
        assert (await host.ledger.get_tree(first.id)).state == "unlanded"
    else:
        assert await host.discard_worktree(first.id) == {"removed": True}
    assert (await host.ledger.get(two.commission_id)).status == "blocked"
    assert await host.ledger.get_tree(second.id) == second and len(members["broken links"].requests) == 7

    # Landed, the second one does: the commission is done.
    assert (await host.land_worktree(second.id))["status"] == "landed"
    await wait_status(host, two.commission_id, "done")
    assert (workdir / "more.txt").read_text() == "more"


async def test_a_worktree_in_which_a_run_cannot_be_stopped_in_time_is_kept_as_it_is(hosts, tmp_path, monkeypatch):
    from nekopower.host import tree_work

    monkeypatch.setattr(tree_work, "_GONE_SECONDS", 0.3)
    hold = asyncio.Event()
    # (It changed nothing: looked at, the worktree would be removed.)
    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path, second=[[_slow_to_stop(hold), *_report("links repaired")]]
    )
    await wait_until(lambda: len(members["broken links"].requests) == 1, what="the second one at work")
    assert await host.abort(two.commission_id)
    await wait_until(lambda: two.commission_id not in host.task_agents._tree_of, what="its worktree being seen to")
    await wait_until(lambda: not host.task_agents._tree_tasks, what="that being through")

    # Still at it when the wait was over: not looked at, and kept for the user.
    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason) == ("unlanded", "stopped") and trees.removed == [] and not trees.checking.is_set()
    hold.set()
    host.release.set()


async def test_what_a_member_changed_is_not_merged_while_a_task_agent_it_started_cannot_be_stopped(hosts, tmp_path, monkeypatch):
    from nekopower.host import tree_work

    # (Not waited for at all: it is still at it when it is first looked at.)
    monkeypatch.setattr(tree_work, "_GONE_SECONDS", 0.0)
    hold = asyncio.Event()
    helper = ScriptedLLM([[_slow_to_stop(hold), *_write("styles.css", "half")]])

    async def until_it_works() -> None:
        await wait_until(lambda: len(helper.requests) >= 1, what="the task agent it started being at work")

    host, trees, workdir, one, two, members = await _two_members(
        hosts, tmp_path,
        second=[
            [call("subagent", {"description": "styles", "prompt": "Tidy the stylesheet. PART-STYLES"}), finish("tool_calls")],
            _write("links.txt", "repaired"),
            [until_it_works, *_report("links repaired")],
        ],
        **{"PART-STYLES": helper},
    )
    host.release.set()
    stuck = await wait_status(host, two.commission_id, "blocked")

    (tree,) = await host.ledger.trees()
    assert (tree.state, tree.reason) == ("unlanded", "failed") and trees.merged == [] and not (workdir / "links.txt").exists()
    assert "could not be stopped in its worktree" in stuck.result_summary
    hold.set()


async def test_a_card_of_a_session_that_is_summed_up_says_so_though_a_worktree_of_its_waits(hosts, tmp_path):
    from nekopower.host.board import card_of

    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    session = await host.ledger.get_session(tree.session_id)
    assert card_of(session, None, (), None, None, tree=tree)["state"] == "unlanded"
    summed_up = dataclasses.replace(session, status="compacting")
    assert card_of(summed_up, None, (), None, None, tree=tree)["state"] == "compacting"


@pytest.mark.parametrize("later", ["git removes it", "git cannot open it any more"])
async def test_a_worktree_the_user_discards_that_cannot_be_removed_then_does_not_come_back(hosts, tmp_path, monkeypatch, later):
    monkeypatch.setattr(settings, "WORKTREES_PER_PROJECT", 1)
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    manager = host.task_agents
    assert not await manager.tree_possible("Shop site", str(workdir))
    # A file of it is in use (an editor has it open).
    trees.fail_remove = WorktreeError("a file of it is in use")

    assert await host.discard_worktree(tree.id) == {"removed": False}

    # Discarded for the user: shown nowhere, in nobody's way, and no room taken; on record as one to remove.
    assert await host.worktrees() == [] and await host._project_busy("Shop site") is None
    assert (await host.ledger.get(two.commission_id)).status == "cancelled"
    assert (await host.ledger.get_tree(tree.id)).state == "discarding" and Path(tree.path).is_dir()
    assert await manager.tree_possible("Shop site", str(workdir))
    cards = [card for project in (await host.board())["projects"] for card in project["members"]]
    assert [card["tree"] for card in cards if card["tree"] is not None] == []
    for decide in (host.land_worktree, host.discard_worktree, host.keep_worktree):
        with pytest.raises(ValueError, match="at_work"):
            await decide(tree.id)

    # As the host starts anew: removed, whatever it holds, and not taken for one that was left with changes.
    manager._trees_mine.clear()
    manager._tree_handles.clear()
    trees.fail_remove = None
    trees.fail_open = later != "git removes it"
    assert await manager.reconcile_trees() == {"removed": [tree.id], "kept": []}
    assert await host.ledger.trees() == [] and not Path(tree.path).exists()
    assert trees.removed == ([tree.id] if later == "git removes it" else [])


async def test_a_worktree_the_user_kept_is_not_landed_where_its_project_no_longer_works(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    await host.keep_worktree(tree.id)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # Seen to personally, it does not keep the project where it is.
    await host.set_project_workdir("Shop site", str(elsewhere))
    trees.merged.clear()

    with pytest.raises(ValueError, match="moved"):
        await host.land_worktree(tree.id)
    # Nothing was merged into the directory the project left, and it can still be discarded.
    assert trees.merged == [] and not (workdir / "links.txt").exists() and not (elsewhere / "links.txt").exists()
    assert (await host.ledger.get_tree(tree.id)).state == "kept"
    assert await host.discard_worktree(tree.id) == {"removed": True}


async def test_nothing_is_said_to_be_landed_where_the_user_has_a_worktree_landed_that_changes_nothing(hosts, tmp_path):
    clash = MergeResult("conflict", conflicts=("links.txt",))
    host, trees, workdir, two, _members, _stuck = await _not_landed(hosts, tmp_path, [clash] * 4, turns=4)
    (tree,) = await host.ledger.trees()
    trees.results = [MergeResult("unchanged")]

    answer = await host.land_worktree(tree.id)

    assert (answer["status"], answer["files"]) == ("unchanged", []) and await host.ledger.trees() == []
    assert await _events(host, two.commission_id, "landed") == []
    await wait_status(host, two.commission_id, "done")


async def test_a_member_that_changes_files_gets_a_worktree_beside_one_that_does_and_none_beside_one_that_only_reads(
    hosts, tmp_path
):
    host, trees, workdir = await _shop(hosts, tmp_path, ScriptedLLM([]))
    # The first member only reads: nobody waits for it, and nobody needs a worktree because of it.
    reader = await host.open_member("Lan", "Shop site", preset="scout")
    first = await host.open_member("Lan", "Shop site", preset="general")
    second = await host.open_member("Lan", "Shop site", preset="general")
    assert (reader["seat"], first["seat"], second["seat"]) == ("", "", "tree")


async def test_a_project_that_cannot_be_given_another_directory_after_all_says_why(hosts, tmp_path, monkeypatch):
    host, trees, workdir = await _shop(hosts, tmp_path, ScriptedLLM([]))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    busy = host._project_busy
    looks: list[str | None] = []

    async def in_the_way_since(project: str) -> str | None:
        # Nothing is in the way when it is first looked at; a worktree waits for the user by the time it is moved.
        looks.append(None if not looks else "tree_not_landed")
        return looks[-1] if looks[-1] is not None or len(looks) > 1 else await busy(project)

    async def not_moved(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(host, "_project_busy", in_the_way_since)
    monkeypatch.setattr(host.ledger, "move_project", not_moved)
    with pytest.raises(ProjectBusy, match="tree_not_landed"):
        await host.set_project_workdir("Shop site", str(elsewhere))
    assert looks == [None, "tree_not_landed"]


async def test_a_member_made_as_one_that_only_reads_is_on_record_as_that_with_its_session(hosts, tmp_path, monkeypatch):
    host, trees, workdir, one, two, _members = await _two_members(hosts, tmp_path, second=[_report("links repaired")])
    host.release.set()
    await wait_status(host, one.commission_id, "done")
    await wait_status(host, two.commission_id, "done")
    manager = host.task_agents
    answers = [False, True]
    asked: list[str] = []

    async def check(commission_id: str, _category: str, detail: Any = None) -> Any:
        asked.append(detail["preset"])
        return types.SimpleNamespace(allow=answers.pop(0), reason="")

    monkeypatch.setattr(manager._permissions, "check", check)
    commission = await host.ledger.get(two.commission_id)
    # One made as a writer, which the user was asked about then (or made before such a record was kept): not asked about.
    await manager._ask_about_member(commission, await _session_of(host, one.commission_id))
    assert asked == []
    # One made as a reader, with nothing else on record of it (the hand-over that made it was cut off): asked about.
    made = await host.ledger.create_session(
        name="Shop site/scout", lanlan_name="Lan", kind="line", project="Shop site", workdir=str(workdir),
        preset="scout", duty="looks things up", seat="reads",
    )
    with pytest.raises(RuntimeError, match="did not allow"):
        await manager._ask_about_member(commission, made)
    # Refused, it is still one that only reads: asked about again with the next commission, and not after it was allowed.
    assert (await host.ledger.get_session(made.id)).seat == "reads"
    await manager._ask_about_member(commission, await host.ledger.get_session(made.id))
    assert asked == ["scout", "scout"] and (await host.ledger.get_session(made.id)).seat == ""
    await manager._ask_about_member(commission, await host.ledger.get_session(made.id))
    assert asked == ["scout", "scout"]

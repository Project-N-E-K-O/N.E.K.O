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
"""scripts/ci/main_red_reporter.py: log parsing, issue lifecycle, and its workflow.

The reporter runs from workflow_run and therefore never on a PR, so these
tests are the only place its decisions get exercised before it acts on main.
GitHub is replaced by an in-memory fake that records every write.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "main_red_reporter.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "main-red-reporter.yml"
UNIT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "unit-tests.yml"
REPO = "owner/repo"


def _load():
    spec = importlib.util.spec_from_file_location("main_red_reporter_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mr = _load()

LOG = """\
2026-11-22T22:40:01.5844098Z ##[group]Run uv run pytest tests/unit -q
2026-11-22T22:40:01.5850000Z Write-Host "NEKO_LF_RERUN_RESULT=$verdict (pytest exit $rc)"
2026-11-22T23:23:33.7318236Z FAILED tests/unit/x/test_host.py::test_cleared[as a runtime is asked - to open it] - AssertionError: assert 'running' == 'closed'
2026-11-22T23:23:33.7320666Z FAILED tests/unit/test_audio.py::test_flush - ValueError: substring not found
2026-11-22T23:23:33.7320700Z ERROR tests/unit/test_broken.py - ImportError: nope
2026-11-22T23:23:34.0000000Z some FAILED text that is not a result line
2026-11-22T23:30:00.0000000Z FAILED tests/unit/test_audio.py::test_flush - ValueError: substring not found
2026-11-22T23:30:01.0000000Z NEKO_LF_RERUN_RESULT=passed (pytest exit 0)
"""


def test_failed_lines_are_parsed_deduplicated_and_stripped():
    assert mr.parse_failed_tests(LOG) == [
        "FAILED tests/unit/x/test_host.py::test_cleared[as a runtime is asked - to open it]",
        "FAILED tests/unit/test_audio.py::test_flush",
        "ERROR tests/unit/test_broken.py",
    ]


def test_rerun_verdict_ignores_the_echoed_step_source():
    assert mr.parse_rerun_verdict(LOG) == "passed"
    source_only = "\n".join(line for line in LOG.splitlines() if "exit 0" not in line)
    assert mr.parse_rerun_verdict(source_only) is None


def test_rerun_marker_matches_the_unit_workflow():
    workflow = UNIT_WORKFLOW.read_bytes().decode("utf-8")
    assert f"{mr.RERUN_MARKER}=$verdict" in workflow
    assert "--lf --lfnf none" in workflow


@pytest.mark.parametrize(
    "message, number",
    [
        ("fix(x): thing (#3954)", 3954),
        ("fix(x): thing (#3954)\n\nbody (#12)", 3954),
        ("Merge branch 'main'", None),
        ("", None),
    ],
)
def test_pr_number_comes_from_the_squash_title(message, number):
    assert mr.pr_number_from_message(message) == number


def _run(run_id, conclusion, created, sha="a" * 40, name="Unit Tests", event="push", branch="main"):
    return {
        "id": run_id,
        "run_number": run_id,
        "name": name,
        "conclusion": conclusion,
        "status": "completed",
        "created_at": created,
        "head_sha": sha,
        "html_url": f"https://example.invalid/runs/{run_id}",
        "event": event,
        "head_branch": branch,
        "workflow_id": 7,
        "head_repository": {"full_name": REPO},
    }


def test_run_ordering_ignores_cancelled_runs():
    current = _run(5, "failure", "2026-01-05T00:00:00Z")
    runs = [
        _run(6, "cancelled", "2026-01-06T00:00:00Z"),
        current,
        _run(4, "failure", "2026-01-04T00:00:00Z"),
        _run(3, "success", "2026-01-03T00:00:00Z", sha="b" * 40),
        _run(2, "success", "2026-01-02T00:00:00Z"),
    ]
    assert mr.newer_decisive_run(current, runs) is None
    assert mr.last_success_before(current, runs)["id"] == 3
    runs.append(_run(7, "success", "2026-01-07T00:00:00Z"))
    assert mr.newer_decisive_run(current, runs)["id"] == 7


def test_tracking_issue_is_matched_by_marker_not_title():
    issues = [
        {"number": 1, "body": mr.issue_marker("Unit Tests"), "pull_request": {}},
        {"number": 2, "body": mr.issue_marker("Plugin Tests")},
        {"number": 3, "body": "renamed by a human\n" + mr.issue_marker("Unit Tests")},
    ]
    assert mr.find_tracking_issue(issues, "Unit Tests")["number"] == 3
    assert mr.find_tracking_issue(issues, "Unit Test Shards") is None


class FakeGitHub:
    """Serves canned API responses and records every write."""

    def __init__(self, run, runs, issues=(), log=LOG, commits=None, failed_jobs=None):
        self.repo = REPO
        self.run = run
        self.runs = runs
        self.issues = list(issues)
        self.log = log
        self.commits = commits if commits is not None else [
            self._commit("c1", "feat: one (#10)", "merger"),
            self._commit("c2", "fix: two (#11)", "merger"),
            self._commit("c3", "chore: direct push", "pusher"),
        ]
        self.failed_jobs = failed_jobs if failed_jobs is not None else [
            {"id": 99, "name": "Unit pytest (Windows)", "conclusion": "failure", "html_url": "https://example.invalid/job/99"}
        ]
        self.writes: list[tuple[str, str, dict | None]] = []
        self.pages_read: list[str] = []

    @staticmethod
    def _commit(sha, message, login):
        return {"sha": sha * 20, "commit": {"message": message}, "author": {"login": login}}

    def get(self, path):
        if path == f"/repos/{REPO}/actions/runs/{self.run['id']}":
            return self.run
        if path.startswith(f"/repos/{REPO}/actions/workflows/"):
            return {"workflow_runs": self._page(path, self.runs)}
        if path.startswith(f"/repos/{REPO}/actions/runs/{self.run['id']}/jobs"):
            return {"jobs": [*self.failed_jobs, {"id": 1, "name": "ok", "conclusion": "success", "html_url": "x"}]}
        if path.startswith(f"/repos/{REPO}/issues?"):
            return self.issues
        if path.startswith(f"/repos/{REPO}/compare/"):
            return {"commits": self._page(path, self.commits), "total_commits": len(self.commits)}
        if path.startswith(f"/repos/{REPO}/commits/"):
            return self.commits[-1]
        if path.startswith(f"/repos/{REPO}/issues/"):
            number = int(path.rsplit("/", 1)[1])
            return {"user": {"login": {10: "alice", 11: "dependabot[bot]"}[number]}}
        raise AssertionError(f"unexpected GET {path}")

    def _page(self, path, items):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(path).query))
        size, page = int(query.get("per_page", 30)), int(query.get("page", 1))
        self.pages_read.append(path)
        return items[(page - 1) * size : page * size]

    def call(self, method, path, body=None, ok=(200, 201)):
        if method == "GET":
            return self.get(path)
        self.writes.append((method, path, body))
        if method == "POST" and path == f"/repos/{REPO}/issues":
            return {"number": 42, "html_url": "https://example.invalid/issues/42"}
        return {}

    def job_log(self, job_id):
        return self.log


def _history(current):
    return [current, _run(1, "success", "2026-01-01T00:00:00Z", sha="b" * 40)]


def _failure():
    return _run(5, "failure", "2026-01-05T00:00:00Z")


def test_failure_without_open_issue_opens_one_with_suspects_and_flaky_label():
    current = _failure()
    gh = FakeGitHub(current, _history(current))
    mr.run_reporter(gh, "5", dry_run=False, discord_url=None)
    created = [w for w in gh.writes if w[0] == "POST" and w[1] == f"/repos/{REPO}/issues"]
    assert len(created) == 1
    body = created[0][2]["body"]
    assert created[0][2]["labels"] == ["main-red", "flaky"]
    assert mr.issue_marker("Unit Tests") in body
    assert "FAILED tests/unit/test_audio.py::test_flush" in body
    assert "嫌疑范围" in body and "#10" in body and "#11" in body
    # PR authors come from the PR, not from the squash commit; bots are not pinged.
    assert "@alice" in body and "@pusher" in body
    assert "@merger" not in body and "@dependabot[bot]" not in body
    assert "重跑通过" in body
    labels = [w[2]["name"] for w in gh.writes if w[1] == f"/repos/{REPO}/labels"]
    assert labels == ["main-red", "flaky"]


def test_failure_with_open_issue_comments_instead_of_opening_another():
    current = _failure()
    issue = {"number": 8, "body": mr.issue_marker("Unit Tests"), "labels": [{"name": "main-red"}], "html_url": "u"}
    gh = FakeGitHub(current, _history(current), issues=[issue], log=LOG.replace("=passed", "=failed"))
    mr.run_reporter(gh, "5", dry_run=False, discord_url=None)
    assert not [w for w in gh.writes if w[1] == f"/repos/{REPO}/issues"]
    comments = [w for w in gh.writes if w[1] == f"/repos/{REPO}/issues/8/comments"]
    assert len(comments) == 1 and "又失败了" in comments[0][2]["body"]
    assert not [w for w in gh.writes if w[1].endswith("/labels")]


def test_flaky_label_is_removed_when_a_later_rerun_reproduces():
    current = _failure()
    issue = {"number": 8, "body": mr.issue_marker("Unit Tests"), "labels": [{"name": "flaky"}], "html_url": "u"}
    gh = FakeGitHub(current, _history(current), issues=[issue], log=LOG.replace("=passed", "=failed"))
    mr.run_reporter(gh, "5", dry_run=False, discord_url=None)
    assert ("DELETE", f"/repos/{REPO}/issues/8/labels/flaky", None) in gh.writes


def test_job_without_rerun_is_not_called_flaky():
    current = _failure()
    jobs = [
        {"id": 99, "name": "full", "conclusion": "failure", "html_url": "x"},
        {"id": 98, "name": "other", "conclusion": "failure", "html_url": "y"},
    ]
    gh = FakeGitHub(current, _history(current), failed_jobs=jobs)
    gh.job_log = lambda job_id: LOG if job_id == 99 else "FAILED tests/unit/test_other.py::test_x - boom\n"
    report = mr.collect_failure(gh, current, _history(current))
    assert report.rerun_verdict == "failed"
    assert not report.flaky


def test_success_closes_the_open_issue():
    current = _run(5, "success", "2026-01-05T00:00:00Z")
    issue = {"number": 8, "body": mr.issue_marker("Unit Tests"), "labels": [], "html_url": "u"}
    gh = FakeGitHub(current, _history(current), issues=[issue])
    mr.run_reporter(gh, "5", dry_run=False, discord_url=None)
    assert [w[:2] for w in gh.writes] == [
        ("POST", f"/repos/{REPO}/issues/8/comments"),
        ("PATCH", f"/repos/{REPO}/issues/8"),
    ]
    assert gh.writes[1][2]["state"] == "closed"


def test_success_without_issue_and_cancelled_runs_write_nothing():
    green = _run(5, "success", "2026-01-05T00:00:00Z")
    gh = FakeGitHub(green, _history(green))
    mr.run_reporter(gh, "5", dry_run=False, discord_url=None)
    cancelled = _run(5, "cancelled", "2026-01-05T00:00:00Z")
    gh2 = FakeGitHub(cancelled, _history(cancelled))
    assert mr.run_reporter(gh2, "5", dry_run=False, discord_url=None)[0].startswith("skip")
    assert gh.writes == [] and gh2.writes == []


@pytest.mark.parametrize("override", [{"event": "pull_request"}, {"head_branch": "feature"}, {"head_repository": {"full_name": "fork/repo"}}])
def test_only_pushes_to_this_repos_main_are_reported(override):
    current = {**_failure(), **override}
    gh = FakeGitHub(current, _history(current))
    assert mr.run_reporter(gh, "5", dry_run=False, discord_url=None)[0].startswith("skip")
    assert gh.writes == []


def test_stale_event_is_skipped_unless_replayed_by_hand():
    current = _failure()
    runs = [*_history(current), _run(9, "success", "2026-01-09T00:00:00Z")]
    gh = FakeGitHub(current, runs)
    assert mr.run_reporter(gh, "5", dry_run=False, discord_url=None)[0].startswith("skip")
    assert gh.writes == []
    replay = mr.run_reporter(gh, "5", dry_run=True, discord_url=None, check_newer=False)
    assert replay[0].startswith("note") and any("open issue" in a for a in replay)


def test_dry_run_writes_nothing():
    current = _failure()
    gh = FakeGitHub(current, _history(current))
    actions = mr.run_reporter(gh, "5", dry_run=True, discord_url="https://discord.invalid/hook")
    assert gh.writes == []
    assert any("FAILED tests/unit/test_audio.py::test_flush" in a for a in actions)


def test_discord_gets_one_line_per_report(monkeypatch):
    sent = []
    monkeypatch.setattr(mr, "_post_discord", lambda url, content: sent.append((url, content)))
    current = _failure()
    gh = FakeGitHub(current, _history(current))
    mr.run_reporter(gh, "5", dry_run=False, discord_url="https://discord.invalid/hook")
    assert len(sent) == 1 and "Unit Tests" in sent[0][1] and "\n" not in sent[0][1]


def test_missing_last_success_lists_only_the_failing_commit():
    current = _failure()
    gh = FakeGitHub(current, [current])
    report = mr.collect_failure(gh, current, [current])
    assert report.last_success is None
    assert [s.sha for s in report.suspects] == [gh.commits[-1]["sha"]]
    assert "没找到" in mr.render_failure(report, for_comment=False)


def test_long_red_streak_pages_back_to_the_last_success_and_every_commit():
    current = _failure()
    failures = [
        _run(1000 - i, "failure", f"2026-01-04T{i // 60:02d}:{i % 60:02d}:00Z") for i in range(150)
    ]
    failures.sort(key=lambda r: r["created_at"], reverse=True)
    good = _run(1, "success", "2026-01-01T00:00:00Z", sha="b" * 40)
    commits = [FakeGitHub._commit(f"{i:02d}", f"change {i} (#10)", "merger") for i in range(250)]
    gh = FakeGitHub(current, [current, *failures, good], commits=commits)
    runs = mr.run_history(gh, current)
    assert mr.last_success_before(current, runs)["id"] == 1
    report = mr.collect_failure(gh, current, runs)
    assert len(report.suspects) == 250 and not report.suspects_truncated
    assert sum("/compare/" in p for p in gh.pages_read) == 3


def test_history_stops_paging_once_the_last_success_is_found():
    current = _failure()
    # 250 older runs, so every page is full; the success sits on the first page.
    older = [
        _run(900 - i, "success" if i == 240 else "failure", f"2026-01-04T{i // 60:02d}:{i % 60:02d}:00Z")
        for i in range(250)
    ]
    older.sort(key=lambda r: r["created_at"], reverse=True)
    gh = FakeGitHub(current, [current, *older])
    mr.run_history(gh, current)
    assert sum("/actions/workflows/" in p for p in gh.pages_read) == 1


def test_log_download_failure_still_reports(monkeypatch):
    gh = mr.GitHub("token", REPO)
    monkeypatch.setattr(gh, "_request", lambda *a, **k: (302, b"", {"Location": "https://blob.invalid/log"}))

    def broken(*args, **kwargs):
        raise urllib.error.URLError("storage unavailable")

    monkeypatch.setattr(mr.urllib.request, "urlopen", broken)
    assert gh.job_log(1) == ""


# ---- workflow wiring ----


def test_live_and_replayed_reports_share_one_lock_per_workflow():
    workflow = yaml.safe_load(WORKFLOW.read_bytes().decode("utf-8"))
    assert "concurrency" not in workflow  # a workflow-level group would differ between live and replay
    report = workflow["jobs"]["report"]
    assert report["needs"] == "resolve"
    assert report["concurrency"]["group"] == "main-red-reporter-${{ needs.resolve.outputs.workflow_id }}"
    assert report["concurrency"]["cancel-in-progress"] is False


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_bytes().decode("utf-8"))


def test_reporter_watches_the_test_workflows_by_their_names(workflow):
    triggers = workflow.get("on", workflow.get(True))
    watched = triggers["workflow_run"]["workflows"]
    assert triggers["workflow_run"]["types"] == ["completed"]
    assert triggers["workflow_run"]["branches"] == ["main"]
    for name in watched:
        files = [p for p in WORKFLOW.parent.glob("*.yml") if yaml.safe_load(p.read_bytes().decode("utf-8")).get("name") == name]
        assert files, f"no workflow is named {name!r}; workflow_run would never fire"
    assert set(watched) == {"Unit Tests", "Unit Test Shards", "Plugin Tests"}


def test_reporter_permissions_and_dispatch_defaults(workflow):
    assert workflow["permissions"] == {"issues": "write", "actions": "read", "contents": "read"}
    inputs = workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]
    assert inputs["run_id"]["required"] is True
    assert inputs["dry_run"]["default"] is True
    gate = workflow["jobs"]["resolve"]["if"]
    assert "conclusion == 'failure'" in gate and "conclusion == 'success'" in gate
    assert "cancelled" not in gate
    job = workflow["jobs"]["report"]
    env = job["steps"][-1]["env"]
    assert env["DISCORD_WEBHOOK_URL"] == "${{ secrets.DISCORD_HEALTH_WEBHOOK_URL }}"

#!/usr/bin/env python3
"""Open, update or close the tracking issue for a red workflow on main.

Driven by .github/workflows/main-red-reporter.yml, once per completed
``push`` run of a watched workflow on main:

* conclusion ``failure``: collect the failed jobs, the ``FAILED`` / ``ERROR``
  lines from their logs and the ``--lf`` re-run verdict, list every commit
  since the workflow last passed on main as the suspect range, then open one
  issue per workflow (label ``main-red``) or comment on the one already open.
* conclusion ``success``: comment on and close the open issue, if any.
* anything else (``cancelled`` above all): ignored.

A run that is older than the newest decisive (success or failure) run of the
same workflow on main is ignored too, so a late event never reopens or closes
an issue against newer evidence.

Standard library only; talks to the REST API with ``GITHUB_TOKEN``.

Environment:
    GITHUB_TOKEN, GITHUB_REPOSITORY  required
    RUN_ID                           the workflow run to report on
    DRY_RUN                          "true" prints the plan and writes nothing
    DISCORD_WEBHOOK_URL              optional; a one-line notice is posted
    GITHUB_STEP_SUMMARY              optional; the plan is appended to it
    GITHUB_EVENT_NAME                "workflow_dispatch" disables the newer-run guard
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API = "https://api.github.com"
LABEL = "main-red"
FLAKY_LABEL = "flaky"
LABEL_COLORS = {LABEL: ("b60205", "A watched workflow is failing on main"), FLAKY_LABEL: ("fbca04", "Failure passed on re-run")}
MAX_TESTS = 80
MAX_COMMITS = 60
RERUN_MARKER = "NEKO_LF_RERUN_RESULT"

_TIMESTAMP_RE = re.compile(r"^﻿?\d{4}-\d{2}-\d{2}T[\d:.]+Z ")
_RESULT_RE = re.compile(r"^(FAILED|ERROR) (\S+?(?:\[.*?\])?)(?: - .*)?$")
_RERUN_RE = re.compile(rf"{RERUN_MARKER}=(passed|failed)\b")
_PR_RE = re.compile(r"\(#(\d+)\)\s*$")


def _strip(line: str) -> str:
    return _TIMESTAMP_RE.sub("", line.rstrip("\r\n"))


def parse_failed_tests(log_text: str) -> list[str]:
    """``FAILED <id>`` / ``ERROR <id>`` lines from a pytest log, deduplicated in order."""
    seen: dict[str, None] = {}
    for raw in log_text.splitlines():
        match = _RESULT_RE.match(_strip(raw))
        if match:
            seen.setdefault(f"{match.group(1)} {match.group(2)}", None)
    return list(seen)


def parse_rerun_verdict(log_text: str) -> str | None:
    verdicts = _RERUN_RE.findall(log_text)
    return verdicts[-1] if verdicts else None


def pr_number_from_message(message: str) -> int | None:
    match = _PR_RE.search(message.splitlines()[0] if message else "")
    return int(match.group(1)) if match else None


def issue_marker(workflow_name: str) -> str:
    return f"<!-- main-red:workflow={workflow_name} -->"


def find_tracking_issue(issues: list[dict], workflow_name: str) -> dict | None:
    marker = issue_marker(workflow_name)
    for issue in issues:
        if "pull_request" in issue:
            continue
        if marker in (issue.get("body") or ""):
            return issue
    return None


def _decisive(runs: list[dict]) -> list[dict]:
    return sorted(
        (r for r in runs if r.get("conclusion") in ("success", "failure")),
        key=lambda r: (r["created_at"], r["id"]),
        reverse=True,
    )


def newer_decisive_run(run: dict, runs: list[dict]) -> dict | None:
    """The newest success/failure run on main that started after ``run``, if any."""
    key = (run["created_at"], run["id"])
    for other in _decisive(runs):
        if (other["created_at"], other["id"]) > key:
            return other
    return None


def last_success_before(run: dict, runs: list[dict]) -> dict | None:
    key = (run["created_at"], run["id"])
    for other in _decisive(runs):
        if other["conclusion"] == "success" and (other["created_at"], other["id"]) < key:
            return other
    return None


@dataclass
class Suspect:
    sha: str
    title: str
    pr: int | None
    author: str | None


@dataclass
class FailureReport:
    workflow: str
    run: dict
    failed_jobs: list[dict]
    failed_tests: list[str]
    rerun_verdict: str | None
    last_success: dict | None
    suspects: list[Suspect] = field(default_factory=list)
    suspects_truncated: bool = False

    @property
    def flaky(self) -> bool:
        return self.rerun_verdict == "passed"


def mentions(suspects: list[Suspect]) -> list[str]:
    seen: dict[str, None] = {}
    for suspect in suspects:
        if suspect.author and not suspect.author.endswith("[bot]"):
            seen.setdefault(suspect.author, None)
    return list(seen)


def render_failure(report: FailureReport, *, for_comment: bool) -> str:
    run = report.run
    sha = run["head_sha"][:8]
    lines: list[str] = []
    if not for_comment:
        lines += [issue_marker(report.workflow), ""]
        lines.append(f"**{report.workflow}** 在 main 上失败了。这个 issue 由 main-red-reporter 自动维护：")
        lines.append("同一个 workflow 再失败会在这里追加评论，在 main 上变绿后自动关闭。")
    else:
        lines.append(f"**{report.workflow}** 在 main 上又失败了。")
    lines += [
        "",
        f"- 运行：[#{run.get('run_number', run['id'])}]({run['html_url']})，提交 `{sha}`，开始于 {run['created_at']}",
        "- 失败的 job：",
    ]
    for job in report.failed_jobs:
        lines.append(f"  - [{job['name']}]({job['html_url']})（{job.get('conclusion')}）")
    if report.rerun_verdict == "passed":
        lines.append("- 偶发判断：失败的测试用 `--lf` 原样重跑了一次，**重跑通过**，疑似偶发失败（已加 `flaky` 标签）。")
    elif report.rerun_verdict == "failed":
        lines.append("- 偶发判断：失败的测试用 `--lf` 原样重跑了一次，重跑仍然失败，不像偶发。")
    else:
        lines.append("- 偶发判断：这个 workflow 没有做失败重跑，无法判断是否偶发。")

    lines += ["", f"### 失败的测试（{len(report.failed_tests)} 条）", ""]
    if report.failed_tests:
        shown = report.failed_tests[:MAX_TESTS]
        lines += ["```", *shown, "```"]
        if len(report.failed_tests) > len(shown):
            lines.append(f"……另有 {len(report.failed_tests) - len(shown)} 条，见运行日志。")
    else:
        lines.append("日志里没有抓到 `FAILED` / `ERROR` 行（可能是安装、超时或非 pytest 步骤失败），请看运行日志。")

    lines += ["", "### 嫌疑范围", ""]
    if report.last_success is None:
        lines.append("没找到这个 workflow 最近在 main 上成功的运行，下面只列出本次运行的提交。")
    else:
        good = report.last_success
        lines.append(
            f"上次在 main 上成功：[#{good.get('run_number', good['id'])}]({good['html_url']})（提交 `{good['head_sha'][:8]}`）。"
            f"从那之后到本次提交共 {len(report.suspects)} 个提交{'（只列出最近的一部分）' if report.suspects_truncated else ''}。"
        )
    lines.append(
        "main 上排队中的运行会被后来的提交顶掉（取消），所以一次失败可能来自下列任意一个提交。"
        "下面的作者只是**嫌疑范围**，不代表就是谁弄坏的。"
    )
    lines += ["", "| 提交 | PR | 作者 | 标题 |", "| --- | --- | --- | --- |"]
    for suspect in report.suspects[-MAX_COMMITS:]:
        pr = f"#{suspect.pr}" if suspect.pr else "-"
        if not suspect.author:
            author = "-"
        elif suspect.author.endswith("[bot]"):
            author = f"`{suspect.author}`"  # do not ping bots
        else:
            author = f"@{suspect.author}"
        title = suspect.title.replace("|", "\\|")
        lines.append(f"| `{suspect.sha[:8]}` | {pr} | {author} | {title} |")
    people = mentions(report.suspects)
    if people:
        lines += ["", "嫌疑范围内的作者：" + " ".join(f"@{p}" for p in people)]
    return "\n".join(lines) + "\n"


def render_recovery(workflow: str, run: dict) -> str:
    return (
        f"**{workflow}** 在 main 上恢复绿色：[#{run.get('run_number', run['id'])}]({run['html_url']})，"
        f"提交 `{run['head_sha'][:8]}`。自动关闭。\n"
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


class GitHub:
    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes, dict]:
        url = path if path.startswith("http") else f"{API}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() or b"", dict(exc.headers or {})

    def call(self, method: str, path: str, body: dict | None = None, ok: tuple[int, ...] = (200, 201)):
        status, raw, _ = self._request(method, path, body)
        if status not in ok:
            raise RuntimeError(f"{method} {path} -> HTTP {status}: {raw[:300]!r}")
        return json.loads(raw) if raw else None

    def get(self, path: str):
        return self.call("GET", path)

    def job_log(self, job_id: int) -> str:
        status, raw, headers = self._request("GET", f"/repos/{self.repo}/actions/jobs/{job_id}/logs")
        if status in (301, 302, 303, 307, 308):
            location = headers.get("Location") or headers.get("location")
            # The redirect target is a pre-signed blob URL: sending the API
            # token along would leak it and makes the storage host reject us.
            with urllib.request.urlopen(location, timeout=120) as resp:
                raw = resp.read()
        elif status != 200:
            return ""
        return raw.decode("utf-8", errors="replace")


def _post_discord(url: str, content: str) -> None:
    data = json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).close()
    except (urllib.error.URLError, OSError) as exc:  # a broken webhook must not hide the issue update
        print(f"warning: Discord notification failed: {exc}", file=sys.stderr)


def collect_failure(gh: GitHub, run: dict, runs: list[dict]) -> FailureReport:
    repo = gh.repo
    jobs = gh.get(f"/repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100")["jobs"]
    failed_jobs = [j for j in jobs if j.get("conclusion") in ("failure", "timed_out")]
    tests: list[str] = []
    verdicts: list[str] = []
    for job in failed_jobs:
        log = gh.job_log(job["id"])
        tests += [t for t in parse_failed_tests(log) if t not in tests]
        verdict = parse_rerun_verdict(log)
        if verdict:
            verdicts.append(verdict)
    # Flaky only when every job that re-ran came back green and no failed job skipped the re-run.
    rerun = None
    if verdicts:
        rerun = "passed" if all(v == "passed" for v in verdicts) and len(verdicts) == len(failed_jobs) else "failed"

    good = last_success_before(run, runs)
    suspects: list[Suspect] = []
    truncated = False
    if good is not None:
        compare = gh.get(f"/repos/{repo}/compare/{good['head_sha']}...{run['head_sha']}")
        commits = compare.get("commits", [])
        truncated = compare.get("total_commits", len(commits)) > len(commits)
    else:
        commits = [gh.get(f"/repos/{repo}/commits/{run['head_sha']}")]
    authors: dict[int, str | None] = {}
    for commit in commits:
        message = commit["commit"]["message"]
        pr = pr_number_from_message(message)
        author = (commit.get("author") or {}).get("login")
        if pr is not None:
            if pr not in authors:
                try:
                    authors[pr] = gh.get(f"/repos/{repo}/issues/{pr}")["user"]["login"]
                except RuntimeError:
                    authors[pr] = None
            author = authors[pr] or author
        suspects.append(Suspect(commit["sha"], message.splitlines()[0], pr, author))
    return FailureReport(run["name"], run, failed_jobs, tests, rerun, good, suspects, truncated)


def _ensure_label(gh: GitHub, name: str) -> None:
    color, description = LABEL_COLORS[name]
    gh.call(
        "POST",
        f"/repos/{gh.repo}/labels",
        {"name": name, "color": color, "description": description},
        ok=(201, 422),  # 422: already exists
    )


def run_reporter(
    gh: GitHub, run_id: str, dry_run: bool, discord_url: str | None, check_newer: bool = True
) -> list[str]:
    """Do (or, in dry-run mode, describe) the work for one run; return the log of actions."""
    repo = gh.repo
    actions: list[str] = []
    run = gh.get(f"/repos/{repo}/actions/runs/{run_id}")
    name = run["name"]
    head_repo = (run.get("head_repository") or {}).get("full_name")
    if run.get("event") != "push" or run.get("head_branch") != "main" or head_repo != repo:
        return [f"skip: run {run_id} is not a push to main of {repo} (event={run.get('event')}, branch={run.get('head_branch')})"]
    if run.get("conclusion") not in ("success", "failure"):
        return [f"skip: conclusion is {run.get('conclusion')!r}"]
    runs = gh.get(
        f"/repos/{repo}/actions/workflows/{run['workflow_id']}/runs?branch=main&event=push&status=completed&per_page=100"
    )["workflow_runs"]
    newer = newer_decisive_run(run, runs)
    if newer is not None:
        note = f"a newer run #{newer.get('run_number')} ({newer['conclusion']}) already decided main's state"
        if check_newer:
            return [f"skip: {note}"]
        actions.append(f"note: {note}; continuing because this is a manual replay")

    open_issues = gh.get(f"/repos/{repo}/issues?state=open&labels={urllib.parse.quote(LABEL)}&per_page=100")
    issue = find_tracking_issue(open_issues, name)

    if run["conclusion"] == "success":
        if issue is None:
            return [f"noop: {name} is green and no issue is open"]
        actions.append(f"comment and close issue #{issue['number']}")
        if not dry_run:
            gh.call("POST", f"/repos/{repo}/issues/{issue['number']}/comments", {"body": render_recovery(name, run)})
            gh.call("PATCH", f"/repos/{repo}/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
            if discord_url:
                _post_discord(discord_url, f"🟢 main 恢复：{name}（{run['head_sha'][:8]}） {issue['html_url']}")
        return actions

    report = collect_failure(gh, run, runs)
    if issue is None:
        title = f"main 红了：{name}"
        body = render_failure(report, for_comment=False)
        actions.append(f"open issue {title!r}{' with flaky label' if report.flaky else ''}")
        actions.append(body)
        if not dry_run:
            _ensure_label(gh, LABEL)
            labels = [LABEL]
            if report.flaky:
                _ensure_label(gh, FLAKY_LABEL)
                labels.append(FLAKY_LABEL)
            issue = gh.call("POST", f"/repos/{repo}/issues", {"title": title, "body": body, "labels": labels})
    else:
        body = render_failure(report, for_comment=True)
        actions.append(f"comment on issue #{issue['number']}")
        actions.append(body)
        has_flaky = any(lbl.get("name") == FLAKY_LABEL for lbl in issue.get("labels", []))
        if not dry_run:
            gh.call("POST", f"/repos/{repo}/issues/{issue['number']}/comments", {"body": body})
            if report.flaky and not has_flaky:
                _ensure_label(gh, FLAKY_LABEL)
                gh.call("POST", f"/repos/{repo}/issues/{issue['number']}/labels", {"labels": [FLAKY_LABEL]})
            elif report.rerun_verdict == "failed" and has_flaky:
                gh.call(
                    "DELETE",
                    f"/repos/{repo}/issues/{issue['number']}/labels/{FLAKY_LABEL}",
                    ok=(200, 404),
                )
    if not dry_run and discord_url and issue is not None:
        flaky = "（疑似偶发）" if report.flaky else ""
        _post_discord(discord_url, f"🔴 main 红了{flaky}：{name}（{run['head_sha'][:8]}） {issue['html_url']}")
    return actions


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("RUN_ID", "").strip()
    if not token or not repo or not run_id:
        print("GITHUB_TOKEN, GITHUB_REPOSITORY and RUN_ID are required", file=sys.stderr)
        return 2
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() == "true"
    # A manual replay of a historical run is the point of workflow_dispatch,
    # so the "newer run already decided" guard only applies to live events.
    check_newer = os.environ.get("GITHUB_EVENT_NAME", "") != "workflow_dispatch"
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip() or None
    actions = run_reporter(GitHub(token, repo), run_id, dry_run, discord_url, check_newer)
    header = f"main-red-reporter run={run_id} dry_run={dry_run}"
    text = "\n\n".join([header, *actions])
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "ab") as fh:
            fh.write(("## " + header + "\n\n" + "\n\n".join(actions) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

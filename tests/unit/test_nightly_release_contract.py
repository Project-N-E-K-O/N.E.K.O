"""Contracts that keep the nightly desktop release publishable.

Two failure modes left the repository without any nightly release
(2026-10-27..29): the old release was deleted before the new one finished
uploading, and macOS assets crossed GitHub's 2 GiB per-asset limit because
upload-artifact expanded the Chrome framework symlinks into three copies.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop.yml"


def _load() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _names(workflow: dict, job: str) -> list[str]:
    return [step.get("name") for step in _steps(workflow, job)]


def _step(workflow: dict, job: str, name: str) -> dict:
    return next(step for step in _steps(workflow, job) if step.get("name") == name)


def test_unix_backend_is_uploaded_as_tarball_to_keep_symlinks() -> None:
    workflow = _load()
    names = _names(workflow, "build-python")
    assert names.index("Pack Python backend (Unix)") < names.index(
        "Upload Python backend artifact"
    )
    # Packing must come after the last re-sign, or the tarball holds a stale seal.
    assert names.index("Re-sign macOS backend after post-processing") < names.index(
        "Pack Python backend (Unix)"
    )

    pack = _step(workflow, "build-python", "Pack Python backend (Unix)")
    assert pack["if"] == "runner.os != 'Windows'"
    assert "COPYFILE_DISABLE=1 tar -C dist/Xiao8 -czf dist/python-backend.tar.gz ." in (
        pack["run"]
    )

    upload = _step(workflow, "build-python", "Upload Python backend artifact")
    assert upload["with"]["path"] == (
        "${{ runner.os == 'Windows' && 'dist/Xiao8/' || 'dist/python-backend.tar.gz' }}"
    )


def test_electron_build_unpacks_backend_before_using_it() -> None:
    workflow = _load()
    names = _names(workflow, "build-electron")
    download = names.index("Download Python backend artifact")
    unpack = names.index("Unpack Python backend (Unix)")
    assert download < unpack < names.index("Make server binary executable (Unix)")

    step = _step(workflow, "build-electron", "Unpack Python backend (Unix)")
    assert step["if"] == "runner.os != 'Windows'"
    assert "tar -xzf electron-app/bin/python-backend.tar.gz -C electron-app/bin" in (
        step["run"]
    )
    assert "rm -f electron-app/bin/python-backend.tar.gz" in step["run"]


def test_nightly_backend_zip_keeps_symlinks_from_tarball() -> None:
    organize = _step(_load(), "nightly", "Organize release files")["run"]
    assert 'if [ -f "$dir/python-backend.tar.gz" ]; then' in organize
    assert 'tar -xzf "$dir/python-backend.tar.gz" -C "$staging"' in organize
    assert "zip -qry" in organize


def test_asset_sizes_are_checked_before_any_release_is_touched() -> None:
    workflow = _load()
    names = _names(workflow, "nightly")
    check = names.index("Check release asset sizes")
    assert check < names.index("Create nightly release")
    assert check < names.index("Create or update Windows nightly release")

    step = _step(workflow, "nightly", "Check release asset sizes")
    # Shared by the full and Windows-only nightly.
    assert "if" not in step
    assert "limit=$((2 * 1024 * 1024 * 1024))" in step["run"]
    assert "(( size >= limit ))" in step["run"]


# --- Behavioural tests: run the real "Create nightly release" script against a fake gh ---

# Stands in for the GitHub CLI. Keeps releases and tags in a JSON file and only
# understands the calls the publish script makes.
_FAKE_GH = r'''
import json, os, re, sys

state_path = os.environ["FAKE_GH_STATE"]
with open(state_path, encoding="utf-8") as fh:
    state = json.load(fh)
args = sys.argv[1:]
state["log"].append(" ".join(args))


def save(code=0):
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    sys.exit(code)


def release(rid):
    return next(r for r in state["releases"] if r["id"] == rid)


def jq_condition(expr):
    # Supports the select(...) shapes the script builds on top of the staging prefix.
    cond = re.search(r'startswith\("nightly-staging-"\)\) and \((.*)\)\) \| \.id$', expr).group(1)
    cond = cond.replace(".draft | not", "not r['draft']").replace(".draft", "r['draft']")
    cond = re.sub(r'\.tag_name == "([^"]*)"', r"r['tag_name'] == '\1'", cond)
    cond = re.sub(r"\.id != (\d+)", r"r['id'] != \1", cond)
    return lambda r: r["tag_name"].startswith("nightly-staging-") and eval(cond)


def listed(r):
    # "stale": the list endpoint still shows the tag a release had before its last PATCH.
    return {**r, "tag_name": r.get("listed_tag", r["tag_name"])} if "stale" in fail else r


def fields(rest):
    out = {}
    for flag, kv in zip(rest[::2], rest[1::2]):
        key, _, value = kv.partition("=")
        out[key] = {"true": True, "false": False}.get(value, value) if flag == "-F" else value
    return out


fail = set(os.environ.get("FAKE_GH_FAIL", "").split(","))
if args[:2] == ["api", "--paginate"]:
    if "list" in fail:
        save(1)
    keep = jq_condition(args[args.index("--jq") + 1])
    print("\n".join(str(r["id"]) for r in state["releases"] if keep(listed(r))))
    save()
if args[0] == "api" and args[1] != "-X":
    print(release(int(args[1].rsplit("/", 1)[1]))["tag_name"])
    save()
if args[:3] == ["api", "-X", "DELETE"]:
    path = args[3]
    if "/git/refs/tags/" in path:
        tag = path.rsplit("/", 1)[1]
        if tag not in state["tags"]:
            save(1)
        state["tags"].remove(tag)
        save()
    rid = int(path.rsplit("/", 1)[1])
    state["releases"] = [r for r in state["releases"] if r["id"] != rid]
    save()
if args[:3] == ["api", "-X", "PATCH"]:
    r = release(int(args[3].rsplit("/", 1)[1]))
    patch = fields(args[4:])
    if "publish" in fail and patch.get("draft") is False:
        save(1)
    if "retag" in fail and patch.get("tag_name") == "nightly":
        save(1)
    if "stale" in fail and "tag_name" in patch:
        r.setdefault("listed_tag", r["tag_name"])
    r.update({k: v for k, v in patch.items() if k in ("tag_name", "draft")})
    if not r["draft"] and r["tag_name"] not in state["tags"]:
        state["tags"].append(r["tag_name"])
    save()
if args[:2] == ["release", "create"]:
    state["next_id"] += 1
    # gh leaves a draft behind when an asset upload fails.
    state["releases"].append({"id": state["next_id"], "tag_name": args[2], "draft": True})
    save(1 if "upload" in fail else 0)
if args[:2] == ["release", "view"]:
    save(0 if any(r["tag_name"] == args[2] for r in state["releases"]) else 1)
if args[:2] == ["release", "delete"]:
    state["releases"] = [r for r in state["releases"] if r["tag_name"] != args[2]]
    if "--cleanup-tag" in args and args[2] in state["tags"]:
        state["tags"].remove(args[2])
    save()
print("fake gh: unexpected call: " + " ".join(args), file=sys.stderr)
save(3)
'''

OLD_NIGHTLY = {"id": 1, "tag_name": "nightly", "draft": False}


def _bash() -> str | None:
    if os.name == "nt":
        # System32\bash.exe is WSL, which cannot run this Windows Python.
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


def _publish(tmp_path: Path, releases: list[dict], fail: str = "") -> tuple[int, dict]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available")
    script = _step(_load(), "nightly", "Create nightly release")["run"]
    (tmp_path / "publish.sh").write_text(
        script.replace("${{ github.sha }}", "abc123"), encoding="utf-8", newline="\n"
    )
    (tmp_path / "fake_gh.py").write_text(_FAKE_GH, encoding="utf-8")
    (tmp_path / "release").mkdir()
    (tmp_path / "release" / "app.zip").write_bytes(b"x")
    state = {
        "releases": releases,
        "tags": [r["tag_name"] for r in releases if not r["draft"]],
        "next_id": 100,
        "log": [],
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    python = Path(sys.executable).as_posix()
    fake = (tmp_path / "fake_gh.py").as_posix()
    # A function wins over any real gh on PATH; sleep is skipped to keep retries fast.
    (tmp_path / "env.sh").write_text(
        f'gh() {{ "{python}" "{fake}" "$@"; }}\nsleep() {{ :; }}\n',
        encoding="utf-8",
        newline="\n",
    )
    env = {
        **os.environ,
        "BASH_ENV": (tmp_path / "env.sh").as_posix(),
        "FAKE_GH_STATE": state_path.as_posix(),
        "FAKE_GH_FAIL": fail,
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "7",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "abc123",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_EVENT_NAME": "schedule",
        "RELEASE_VERSION": "20261030-abc123",
        "REQUESTED_SKIP_SIGNING": "",
    }
    result = subprocess.run(
        [bash, "publish.sh"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert "unexpected call" not in result.stderr, result.stderr
    return result.returncode, json.loads(state_path.read_text(encoding="utf-8"))


def _tags(state: dict) -> set[str]:
    return {r["tag_name"] for r in state["releases"]}


def test_successful_publish_replaces_old_nightly_and_cleans_staging(tmp_path: Path) -> None:
    leftovers = [
        {"id": 50, "tag_name": "nightly-staging-5-1", "draft": True},
        # Published by an earlier run whose final retag failed.
        {"id": 60, "tag_name": "nightly-staging-6-1", "draft": False},
    ]
    code, state = _publish(tmp_path, [OLD_NIGHTLY, *leftovers])

    assert code == 0
    assert state["releases"] == [{"id": 101, "tag_name": "nightly", "draft": False}]
    assert state["tags"] == ["nightly"]


def test_cleanup_with_stale_release_list_keeps_new_nightly(tmp_path: Path) -> None:
    # Right after the retag, the list endpoint may still show the new release under
    # its staging tag; the cleanup must not mistake it for a leftover.
    leftover = {"id": 60, "tag_name": "nightly-staging-6-1", "draft": False}
    code, state = _publish(tmp_path, [OLD_NIGHTLY, leftover], fail="stale")

    assert code == 0
    assert [(r["id"], r["tag_name"], r["draft"]) for r in state["releases"]] == [
        (101, "nightly", False)
    ]
    assert "nightly" in state["tags"]
    assert not any(call == "api -X DELETE repos/owner/repo/releases/101" for call in state["log"])


def test_successful_publish_without_previous_nightly(tmp_path: Path) -> None:
    code, state = _publish(tmp_path, [])

    assert code == 0
    assert state["releases"] == [{"id": 101, "tag_name": "nightly", "draft": False}]


@pytest.mark.parametrize("fail", ["upload", "publish"])
def test_failure_before_replacing_keeps_old_nightly(tmp_path: Path, fail: str) -> None:
    code, state = _publish(tmp_path, [OLD_NIGHTLY], fail=fail)

    assert code != 0
    assert state["releases"] == [OLD_NIGHTLY]
    assert not any("release delete nightly" in call for call in state["log"])


def test_retag_failure_leaves_a_published_staging_release(tmp_path: Path) -> None:
    code, state = _publish(tmp_path, [OLD_NIGHTLY], fail="retag")

    assert code != 0
    assert state["releases"] == [{"id": 101, "tag_name": "nightly-staging-7-1", "draft": False}]
    assert sum("-f tag_name=nightly" in call for call in state["log"]) == 3


def test_release_listing_failure_never_widens_deletion(tmp_path: Path) -> None:
    leftovers = [
        {"id": 50, "tag_name": "nightly-staging-5-1", "draft": True},
        {"id": 60, "tag_name": "nightly-staging-6-1", "draft": False},
    ]
    code, state = _publish(tmp_path, [OLD_NIGHTLY, *leftovers], fail="list")

    assert code != 0
    assert OLD_NIGHTLY in state["releases"]
    assert all(r in state["releases"] for r in leftovers)
    assert not any(call.startswith("api -X DELETE") for call in state["log"])


def test_later_failed_run_keeps_the_only_published_staging_release(tmp_path: Path) -> None:
    # The state a retag failure leaves behind: no nightly, only the staging release.
    stranded = {"id": 60, "tag_name": "nightly-staging-6-1", "draft": False}
    for fail in ("upload", "publish", "retag"):
        run_dir = tmp_path / fail
        run_dir.mkdir()
        code, state = _publish(run_dir, [dict(stranded)], fail=fail)

        assert code != 0
        assert stranded in state["releases"], fail

"""Contracts that keep the nightly desktop release publishable.

Two failure modes left the repository without any nightly release
(2026-10-27..29): the old release was deleted before the new one finished
uploading, and macOS assets crossed GitHub's 2 GiB per-asset limit because
upload-artifact expanded the Chrome framework symlinks into three copies.
"""

from pathlib import Path

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


def test_old_nightly_is_deleted_only_after_the_draft_upload_succeeds() -> None:
    run = _step(_load(), "nightly", "Create nightly release")["run"]

    create = run.index('if ! gh release create "$STAGING_TAG" release/*')
    assert "--draft" in run[create:run.index("--notes", create)]
    failure = run.index("Uploading the nightly draft failed", create)
    delete_old = run.index("gh release delete nightly --yes --cleanup-tag")
    publish = run.index('gh api -X PATCH "repos/${GITHUB_REPOSITORY}/releases/${staging_id}"')

    assert create < failure < delete_old < publish
    # The upload-failure branch must leave the old nightly alone.
    assert "gh release delete nightly" not in run[failure:run.index("fi", failure)]
    assert "-f tag_name=nightly" in run[publish:]
    assert "-F draft=false" in run[publish:]

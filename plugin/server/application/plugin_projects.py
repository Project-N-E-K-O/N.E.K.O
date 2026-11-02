"""Create plugin source projects without loading them into the runtime."""

from __future__ import annotations

import base64
import sys
from tempfile import TemporaryDirectory
from pathlib import Path

from plugin.neko_plugin_cli.core.project import (
    ProjectCreationError,
    create_project,
    prepare_project,
    _run_git,
)


def source_repo_root() -> Path | None:
    # Frozen executables are not Python interpreters or source checkouts.
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return None
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").is_file() else None


def project_spec(
    *,
    plugin_id: str,
    name: str,
    description: str,
    author: str,
    plugin_type: str,
):
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    return PluginSpec(
        plugin_id=plugin_id,
        name=name,
        description=description,
        author_name=author,
        plugin_type=plugin_type,
        quick_start=True,
        features=["entry_point"],
        create_github_actions=True,
    )


def project_request(*, parent_dir: str, **identity):
    parent = Path(parent_dir).expanduser()
    if not parent.is_absolute():
        raise ProjectCreationError(
            "INVALID_PARENT", "Select an absolute parent directory on this machine"
        )
    try:
        parent = parent.resolve(strict=True)
    except OSError as exc:
        raise ProjectCreationError(
            "INVALID_PARENT", "The parent directory is unavailable"
        ) from exc
    if not parent.is_dir():
        raise ProjectCreationError(
            "INVALID_PARENT", "The parent path must be a directory"
        )
    spec = project_spec(**identity)
    # The caller supplies a parent, never an executable entry or output filename.
    target = parent / spec.plugin_id
    files = prepare_project(spec, target, repo_root=source_repo_root())
    return spec, target, files


def create_or_preview(*, create: bool, **request) -> dict:
    spec, target, files = project_request(**request)
    if create:
        create_project(spec, target, repo_root=source_repo_root())
    return project_result(spec, str(target), files, created=create)


def project_result(spec, project_dir: str, files, *, created: bool) -> dict:
    return {
        "plugin_id": spec.plugin_id,
        "name": spec.name,
        "description": spec.description,
        "author": spec.author_name,
        "plugin_type": spec.plugin_type,
        "version": spec.version,
        "entry": spec.entry_point,
        "project_dir": project_dir,
        "files": [path.as_posix() for path in files] + [".git/"],
        "created": created,
    }


def browser_project(*, create: bool, **identity) -> dict:
    """Return a portable scaffold for writing through a browser directory handle.

    The browser's chosen directory has no server-visible absolute path. Never
    render source-checkout-relative tasks against the temporary staging path.
    """
    from plugin.neko_plugin_cli.templates.generator import render_plugin_files

    spec = project_spec(**identity)
    files = render_plugin_files(spec, Path(spec.plugin_id), repo_root=None)
    result = project_result(spec, spec.plugin_id, files, created=False)
    if not create:
        return result

    def git_without_templates(command, *, cwd):
        # Git's optional sample hooks are browser-protected on Windows. An
        # empty template still produces a real, portable, uncommitted repo.
        _run_git(["init", "--template="] if command == ["init"] else command, cwd=cwd)

    with TemporaryDirectory(prefix="neko-plugin-project-") as staging:
        target = Path(staging) / spec.plugin_id
        create_project(spec, target, repo_root=None, git_runner=git_without_templates)
        result["directories"] = []
        result["contents"] = []
        for path in sorted(target.rglob("*")):
            relative = path.relative_to(target).as_posix()
            # Chromium protects .git/config writes. Repository-local settings
            # are optional: HEAD, objects/ and refs/ form the empty repository.
            # Leave config creation to the user's native Git tool.
            if relative == ".git/config":
                continue
            if path.is_dir():
                result["directories"].append(relative)
            else:
                result["contents"].append(
                    {
                        "path": relative,
                        "content_base64": base64.b64encode(path.read_bytes()).decode(
                            "ascii"
                        ),
                    }
                )
    # Staging a bundle does not mean it has been saved to the user's disk.
    return result

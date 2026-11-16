"""Local ruff lint invocation matching the Market CI workflows.

The CI workflows (``.github/workflows/plugin-market-verify.yml`` and
``plugin-market-release.yml``) run ruff with a pinned version and a fixed
argument list. The CLI ``lint`` command uses :func:`ruff_command`, and
``precheck`` runs that command as its lint stage. A unit test checks that
the local invocation matches the commands in both workflows.

Publishing shares the version and rules, while retaining its gitignore
behavior and extra exclusions for dependency-sync work directories.

The library layer (``core/``) never imports the commands layer, so this
module stays free of CLI dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuffLintSpec:
    """Pinned ruff version and rule arguments shared by lint and publish."""

    version: str
    args: tuple[str, ...]


RUFF_LINT_SPEC = RuffLintSpec(
    version="0.12.4",
    args=(
        "--target-version",
        "py311",
        "--line-length",
        "120",
        "--select",
        "E4,E7,E9,F,I",
    ),
)


def ruff_command(
    target: str | Path,
    *,
    respect_gitignore: bool = False,
    extra_excludes: tuple[str, ...] = (),
) -> list[str]:
    """Use the shared rules with the caller's existing file-selection policy."""
    return [
        "uvx",
        f"ruff=={RUFF_LINT_SPEC.version}",
        "check",
        "--ignore-noqa",
        "--isolated",
        *([] if respect_gitignore else ["--no-respect-gitignore"]),
        *RUFF_LINT_SPEC.args,
        "--exclude",
        ",".join(("vendor", *extra_excludes)),
        str(target),
    ]

"""Single source of truth for the repository ruff lint invocation.

The CI workflows (``.github/workflows/plugin-market-verify.yml`` and
``plugin-market-release.yml``) run ruff with a pinned version and a fixed
argument list.  Both the workflows and the CLI read the values from here:
the workflows invoke ``neko-plugin lint`` (a unit test cross-checks that
they no longer hard-code the version or flags), and the CLI ``precheck``
command builds its subprocess from :func:`ruff_command`.

The library layer (``core/``) never imports the commands layer, so this
module stays free of CLI dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuffLintSpec:
    """Pinned ruff version and argument list for the lint stage."""

    version: str
    args: tuple[str, ...]


RUFF_LINT_SPEC = RuffLintSpec(
    version="0.12.4",
    args=(
        "check",
        "--ignore-noqa",
        "--isolated",
        "--no-respect-gitignore",
        "--target-version",
        "py311",
        "--line-length",
        "120",
        "--select",
        "E4,E7,E9,F,I",
        "--exclude",
        "vendor",
    ),
)


def ruff_command(target: str | Path) -> list[str]:
    """Return the pinned ``uvx ruff`` invocation for *target*."""
    return ["uvx", f"ruff=={RUFF_LINT_SPEC.version}", *RUFF_LINT_SPEC.args, str(target)]

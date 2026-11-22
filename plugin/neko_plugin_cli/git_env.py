"""The environment every git subprocess of the CLI runs with."""

from __future__ import annotations

import os

# Inherited from a git hook or wrapper, these point git at another
# repository than the one around ``cwd``. Every git call drops them, so the
# checks and the commands that act (tag, push, remote) see the same repository.
REPOSITORY_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_PREFIX",
)


def git_env() -> dict[str, str]:
    """``os.environ`` without the repository-locating variables."""
    env = dict(os.environ)
    for name in REPOSITORY_ENV_VARS:
        env.pop(name, None)
    return env

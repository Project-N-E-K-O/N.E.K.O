"""The environment every git subprocess of the CLI runs with."""

from __future__ import annotations

import os

# Inherited from a git hook or wrapper (``git -c ...`` included), these point
# git at another repository, or another configuration, than the one around
# ``cwd``. Every git call drops them, so the checks and the commands that act
# (tag, push, remote) see the same repository. The list is what
# ``git rev-parse --local-env-vars`` prints (git 2.43); without
# GIT_CONFIG_COUNT git reads no GIT_CONFIG_KEY_<n> / _VALUE_<n>.
REPOSITORY_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)


def git_env() -> dict[str, str]:
    """``os.environ`` without the repository-locating variables."""
    env = dict(os.environ)
    for name in REPOSITORY_ENV_VARS:
        env.pop(name, None)
    return env

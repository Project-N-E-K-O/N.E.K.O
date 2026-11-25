"""The environment every git subprocess of the CLI runs with."""

from __future__ import annotations

import os

# Inherited from a git hook or wrapper, these point git at another
# repository (or that repository's files) than the one around ``cwd``. Every
# git call drops them, so the checks and the commands that act (tag, push,
# remote) see the same repository.
#
# Configuration passed through the environment (GIT_CONFIG_COUNT with
# GIT_CONFIG_KEY_<n> / _VALUE_<n>, GIT_CONFIG_PARAMETERS) is kept on purpose:
# CI uses it for credentials and safe.directory. So are the behaviour switches
# GIT_NO_REPLACE_OBJECTS and GIT_REPLACE_REF_BASE. Plain GIT_CONFIG carries no
# settings but redirects `git config` to another file, so it goes, as git
# itself drops it when it switches repositories.
REPOSITORY_ENV_VARS = (
    "GIT_CONFIG",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_PREFIX",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_GRAFT_FILE",
    "GIT_SHALLOW_FILE",
)


def git_env() -> dict[str, str]:
    """``os.environ`` without the repository-locating variables."""
    env = dict(os.environ)
    for name in REPOSITORY_ENV_VARS:
        env.pop(name, None)
    return env

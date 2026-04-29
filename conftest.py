from __future__ import annotations

import sys

import pytest


_REQUIRED_PYTHON_MAJOR = 3
_REQUIRED_PYTHON_MINOR = 11


def pytest_configure(config: pytest.Config) -> None:
    current = sys.version_info
    if (current.major, current.minor) == (_REQUIRED_PYTHON_MAJOR, _REQUIRED_PYTHON_MINOR):
        return

    pytest.exit(
        "N.E.K.O tests must run on Python 3.11.x because pyproject.toml declares "
        f"requires-python ==3.11.*; current interpreter is "
        f"{current.major}.{current.minor}.{current.micro} at {sys.executable}",
        returncode=4,
    )

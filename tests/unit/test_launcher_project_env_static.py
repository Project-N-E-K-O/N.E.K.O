# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""The source launcher must apply project-local development overrides first."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_launcher_loads_project_env_before_runtime_imports() -> None:
    source = (ROOT / "launcher.py").read_text(encoding="utf-8")
    load = source.index('_load_dotenv(_Path(__file__).resolve().with_name(".env"), override=False)')
    bootstrap = source.index("_ensure_utf8_filesystem_encoding()")
    runtime = source.index("from launcher_core.runtime import start_launcher")
    assert load < bootstrap < runtime

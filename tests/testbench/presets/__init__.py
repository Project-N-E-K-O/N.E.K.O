"""Built-in character presets for the testbench.

This directory houses **git-tracked** seed data so a tester can one-click bring
a brand-new session to a known good state (and also reset a messy session by
re-importing the same preset).

Layout
------
::

    tests/testbench/presets/
    ├── __init__.py                    (this file — exposes PRESETS_ROOT)
    └── <preset_id>/
        ├── meta.json                  (id, display_name, description, language,
        │                               character_name)
        ├── characters.json            (full {主人, 猫娘, 当前猫娘} dict)
        └── memory/<character_name>/
            ├── persona.json
            ├── facts.json
            └── recent.json

The preset is consumed by :mod:`tests.testbench.routers.persona_router` via
``GET /api/persona/builtin_presets`` (listing) and
``POST /api/persona/import_builtin_preset/{preset_id}`` (apply).
"""
from __future__ import annotations

from pathlib import Path

PRESETS_ROOT: Path = Path(__file__).resolve().parent

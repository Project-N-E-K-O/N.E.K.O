from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any


CHARACTER_DATA_DIR = Path(
    resources.files("plugin.plugins.galgame_plugin").joinpath("character_data")
)
SENREN_BANKA_DATA: dict[str, Any] = json.loads(
    (CHARACTER_DATA_DIR / "senren_banka.json").read_text(encoding="utf-8")
)
MURASAME_PROFILE: dict[str, object] = SENREN_BANKA_DATA["characters"]["叢雨"]

# Ported from claudian/src/utils/subagentJsonl.ts
# Original author: Claudian contributors
# License: MIT

"""
Subagent JSONL utilities.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def read_subagent_jsonl(file_path: Path) -> List[Dict[str, Any]]:
    """Read subagent data from JSONL file."""
    entries = []
    if not file_path.exists():
        return entries

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception:
        pass

    return entries


def write_subagent_jsonl(file_path: Path, entries: List[Dict[str, Any]]) -> bool:
    """Write subagent data to JSONL file."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False

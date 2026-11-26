"""Render the saved palette before the deferred theme loader takes over."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import configured_data_dir
from .service import _coerce_bool, _validate_palette


@lru_cache(maxsize=1)
def _read_palette(path: Path, fingerprint: tuple[int, int, int]) -> tuple[str | None, tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None, ()
    if _coerce_bool(payload.get("enabled", True)) is False:
        return "", ()

    light, dark, _ = _validate_palette(payload.get("vars"), payload.get("dark_vars"))
    blocks = []
    gates = []
    for mode, selector, palette in (
        ("l", ':root:not([data-theme="dark"])', light),
        ("d", ':root[data-theme="dark"]', dark),
    ):
        declarations = []
        for name, value in palette.items():
            # Only known keys reach this point. Raw style text must also be safe
            # for the HTML parser when a user has edited theme.json by hand.
            if any(char in value for char in "<>{};\\\r\n"):
                continue
            declarations.append(f"{name}:{value};")
            gates.append(f"data-nts-{mode}-{name.removeprefix('--nts-')}")
        if declarations:
            blocks.append(f"{selector}{{{''.join(declarations)}}}")
    css = "@layer nts-theme{" + "".join(blocks) + "}" if blocks else ""
    return css, tuple(gates)


def boot_palette(data_dir: Path | None = None) -> tuple[str | None, tuple[str, ...]]:
    path = (data_dir if data_dir is not None else configured_data_dir()) / "theme.json"
    try:
        stat = path.stat()
        return _read_palette(path, (stat.st_mtime_ns, stat.st_size, stat.st_ino))
    except (OSError, ValueError, RecursionError):
        return None, ()

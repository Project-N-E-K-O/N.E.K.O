from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as file_obj:
        data = tomllib.load(file_obj)
    if not isinstance(data, dict):
        raise ValueError(f"TOML root must be a table: {path}")
    return data


def require_table(data: dict[str, object], key: str, source_path: Path) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"required TOML table [{key}] missing in {source_path}")
    return value


def require_string(data: dict[str, object], key: str, source_path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"required string '{key}' missing in {source_path}")
    return value.strip()


def optional_string(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def toml_bool(value: bool) -> str:
    return "true" if value else "false"


_TOML_STRING_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}

_BARE_KEY_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def escape_string(value: str) -> str:
    result: list[str] = []
    for ch in value:
        escaped = _TOML_STRING_ESCAPES.get(ch)
        if escaped is not None:
            result.append(escaped)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            result.append(f"\\u{ord(ch):04X}")
        else:
            result.append(ch)
    return "".join(result)


def toml_bare_or_quoted_key(key: str) -> str:
    if key and all(ch in _BARE_KEY_CHARS for ch in key):
        return key
    return f'"{escape_string(key)}"'


def dump_mapping(mapping: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for key, value in mapping.items():
        lines.extend(dump_value_assignment(key, value))
    return lines


def dump_value_assignment(key: str, value: object) -> list[str]:
    rendered = render_toml_value(value)
    return [f"{toml_bare_or_quoted_key(key)} = {rendered}"]


def render_toml_value(value: object) -> str:
    if isinstance(value, bool):
        return toml_bool(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return f'"{escape_string(value)}"'
    if isinstance(value, list):
        rendered_items = ", ".join(render_toml_value(item) for item in value)
        return f"[{rendered_items}]"
    if isinstance(value, dict):
        pairs = []
        for item_key, item_value in value.items():
            pairs.append(f"{toml_bare_or_quoted_key(str(item_key))} = {render_toml_value(item_value)}")
        return "{ " + ", ".join(pairs) + " }"
    if value is None:
        return '""'
    return f'"{escape_string(str(value))}"'

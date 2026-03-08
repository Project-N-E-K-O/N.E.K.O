from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO, Protocol, cast

from fastapi import HTTPException


class TomlReader(Protocol):
    def load(self, fp: BinaryIO) -> object: ...

    def loads(self, text: str) -> object: ...


class TomlWriter(Protocol):
    def dump(self, obj: object, fp: BinaryIO) -> None: ...


_toml_reader: TomlReader | None
try:
    import tomllib as _tomllib

    _toml_reader = _tomllib
except ImportError:
    try:
        import tomli as _tomli

        _toml_reader = _tomli
    except ImportError:
        _toml_reader = None

_toml_writer: TomlWriter | None
try:
    import tomli_w as _tomli_w

    _toml_writer = _tomli_w
except ImportError:
    _toml_writer = None


def require_toml_reader() -> TomlReader:
    if _toml_reader is None:
        raise HTTPException(status_code=500, detail="TOML library not available")
    return _toml_reader


def require_toml_writer() -> TomlWriter:
    if _toml_writer is None:
        raise HTTPException(status_code=500, detail="TOML library not available")
    return _toml_writer


def _coerce_string_key_mapping(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=400,
            detail=f"{context} must be a TOML table at the root",
        )

    normalized: dict[str, object] = {}
    for key_obj, item in value.items():
        if not isinstance(key_obj, str):
            continue
        normalized[key_obj] = item
    return normalized


def load_toml_from_file(path: Path) -> dict[str, object]:
    reader = require_toml_reader()
    try:
        with path.open("rb") as file_obj:
            raw = reader.load(file_obj)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load config from {path}: {str(exc)}",
        ) from exc
    return _coerce_string_key_mapping(raw, context=f"{path}")


def load_toml_from_stream(stream: BinaryIO, *, context: str) -> dict[str, object]:
    reader = require_toml_reader()
    try:
        raw = reader.load(stream)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load TOML for {context}: {str(exc)}",
        ) from exc
    return _coerce_string_key_mapping(raw, context=context)


def parse_toml_text(text: str, *, context: str) -> dict[str, object]:
    reader = require_toml_reader()
    try:
        raw = reader.loads(text)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid TOML format: {str(exc)}") from exc
    return _coerce_string_key_mapping(raw, context=context)


def render_toml_text(payload: Mapping[str, object]) -> str:
    writer = require_toml_writer()
    buf = io.BytesIO()
    try:
        writer.dump(payload, buf)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to render TOML: {str(exc)}") from exc
    return buf.getvalue().decode("utf-8")


def dump_toml_bytes(payload: Mapping[str, object]) -> bytes:
    writer = require_toml_writer()
    buf = io.BytesIO()
    try:
        writer.dump(cast(object, payload), buf)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to serialize TOML: {str(exc)}") from exc
    return buf.getvalue()

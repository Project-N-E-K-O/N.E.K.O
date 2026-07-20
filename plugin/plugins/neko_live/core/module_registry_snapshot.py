"""Safe module metadata projection for dashboards and hosted UI."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any

from .contracts_public import is_sensitive_public_key

_MAX_TEXT = 240
_MAX_DEPTH = 4
_SENSITIVE_AUTH_RE = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*[^,;]+")
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"cookie|authorization|x-tt-token|ttwid|odin_tt|sessionid|sessionid_ss|sid_tt|uid_tt|"
    r"webcast_sign|signature|sign|token|sessdata|bili_jct"
    r")\b\s*[:=]\s*[^;&\s]+"
)


@dataclass
class ModuleRecord:
    id: str
    title: str
    version: str
    enabled: bool
    status: dict[str, Any]
    domain: str = ""
    config_schema: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    error: str = ""


def module_snapshot(
    modules: dict[str, Any],
    degraded: dict[str, str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for module in modules.values():
        status, domain, schema = safe_meta(module)
        module_id = _safe_text(getattr(module, "id", ""))
        error = _safe_text(degraded.get(module.id, ""))
        records.append(
            ModuleRecord(
                id=module_id,
                title=_safe_text(getattr(module, "title", module_id) or module_id),
                version=_safe_text(getattr(module, "version", "") or ""),
                enabled=getattr(module, "enabled", False) is True,
                status=status,
                domain=domain,
                config_schema=schema,
                degraded=bool(error),
                error=error,
            ).__dict__
        )
    return records


def safe_meta(module: Any) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    try:
        status = module.status()
        if not isinstance(status, dict):
            status = {"value": status}
    except Exception as exc:  # noqa: BLE001
        status = {"error": _safe_text(str(exc).strip() or type(exc).__name__)}
    else:
        status = _safe_public_dict(status)

    domain = _safe_text(getattr(module, "domain", "") or "")
    schema: list[dict[str, Any]] = []
    getter = getattr(module, "config_schema", None)
    if callable(getter):
        try:
            raw = getter()
            if isinstance(raw, list):
                schema = [_safe_public_dict(item) for item in raw if isinstance(item, dict)]
        except Exception:  # noqa: BLE001
            schema = []
    return status, domain, schema


def _safe_public_dict(value: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    named_sensitive_field = is_sensitive_public_key(value.get("name"))
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if is_sensitive_public_key(key) or (
            named_sensitive_field and key in {"default", "value"}
        ):
            safe[key] = "[redacted]"
        else:
            safe[key] = _safe_public_value(item, depth=0)
    return safe


def _safe_public_value(value: Any, *, depth: int) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, str):
        return _safe_text(value)
    if depth >= _MAX_DEPTH:
        return None
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        named_sensitive_field = is_sensitive_public_key(value.get("name"))
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if is_sensitive_public_key(key) or (
                named_sensitive_field and key in {"default", "value"}
            ):
                safe[key] = "[redacted]"
            else:
                safe[key] = _safe_public_value(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_public_value(item, depth=depth + 1) for item in value]
    return ""


def _safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    text = _SENSITIVE_AUTH_RE.sub("[redacted]", text)
    text = _SENSITIVE_TEXT_RE.sub("[redacted]", text)
    return text[:_MAX_TEXT]

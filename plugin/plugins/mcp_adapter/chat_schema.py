"""Convert MCP JSON schemas to the portable chat-tool schema subset."""
from __future__ import annotations

import copy
from typing import Any


def portable_chat_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Expand local references and reject schemas that cannot be represented safely."""
    annotations = {"$schema", "$defs", "definitions", "title", "default", "examples", "$id", "$comment"}
    scalar_keys = {"type", "description", "enum", "format", "nullable"}

    def convert(node: Any, refs: tuple[str, ...] = (), depth: int = 0) -> dict[str, Any]:
        if depth > 32 or not isinstance(node, dict):
            raise ValueError("Unsupported or excessively nested MCP schema")
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/") or ref in refs:
                raise ValueError("External or recursive MCP schema reference")
            target: Any = schema
            for part in ref[2:].split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or key not in target:
                    raise ValueError("Unresolved MCP schema reference")
                target = target[key]
            # Constraint siblings require intersection semantics, not dict merging.
            siblings = set(node) - annotations - {"$ref", "description"}
            if siblings:
                raise ValueError("Unsupported constraint beside MCP schema reference")
            result = convert(target, (*refs, ref), depth + 1)
            if "description" in node:
                result["description"] = node["description"]
            return result
        result = {}
        for key, value in node.items():
            if key in annotations:
                continue
            if key in scalar_keys:
                if key == "type" and value not in ("object", "array", "string", "integer", "number", "boolean"):
                    raise ValueError("Unsupported MCP schema type")
                if key in {"description", "format"} and not isinstance(value, str):
                    raise ValueError("Invalid MCP schema text field")
                if key == "nullable" and not isinstance(value, bool):
                    raise ValueError("Invalid MCP schema nullable field")
                if key == "enum" and (
                    not isinstance(value, list) or not all(isinstance(item, str) for item in value)
                ):
                    raise ValueError("Chat providers require string MCP schema enums")
                result[key] = copy.deepcopy(value)
            elif key == "properties" and isinstance(value, dict):
                result[key] = {name: convert(child, refs, depth + 1) for name, child in value.items()}
            elif key == "items":
                result[key] = convert(value, refs, depth + 1)
            elif key == "required" and isinstance(value, list) and all(isinstance(name, str) for name in value):
                result[key] = list(value)
            else:
                raise ValueError(f"Unsupported MCP schema keyword: {key}")
        return result

    return convert(schema)

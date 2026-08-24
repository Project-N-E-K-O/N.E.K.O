# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Collection

from fastapi import HTTPException, UploadFile

from utils.document_parser import (
    MAX_DOCUMENT_BYTES,
    DocumentParseError,
    parse_document,
)


@dataclass(frozen=True)
class ParsedUpload:
    filename: str
    content_type: str
    size: int
    document_type: str
    content: str
    truncated: bool
    meta: dict[str, Any]


def safe_document_filename(value: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f-\x9f<>]+", "", str(value or "")).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        return "document"
    if len(name) <= 160:
        return name
    suffix_match = re.search(r"(\.[A-Za-z0-9]{1,16})$", name)
    if not suffix_match:
        return name[:160]
    suffix = suffix_match.group(1)
    stem = name[: 160 - len(suffix)].rstrip(" .")
    return (stem or "document") + suffix


async def read_upload_limited(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_DOCUMENT_BYTES:
            raise DocumentParseError("document_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def parse_uploaded_document(
    file: UploadFile,
    *,
    allowed_document_types: Collection[str] | None = None,
    parser: Callable[[str, str, bytes], dict[str, Any]] | None = None,
    run_in_thread: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> ParsedUpload:
    filename = safe_document_filename(file.filename or "")
    content_type = file.content_type or ""
    selected_parser = parser or parse_document
    selected_runner = run_in_thread or asyncio.to_thread
    try:
        _reject_disallowed_declared_type(filename, allowed_document_types)
        data = await read_upload_limited(file)
        parsed = await selected_runner(selected_parser, filename, content_type, data)
        document_type = str(parsed["document_type"])
        if allowed_document_types is not None and document_type not in allowed_document_types:
            raise DocumentParseError("unsupported_document")
    except DocumentParseError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code}) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"code": "document_parse_failed"}) from exc
    finally:
        await file.close()

    content = str(parsed["content"])
    return ParsedUpload(
        filename=filename,
        content_type=content_type,
        size=len(data),
        document_type=document_type,
        content=content,
        truncated=bool(parsed.get("truncated")),
        meta=parsed.get("meta") or {},
    )


def _reject_disallowed_declared_type(
    filename: str,
    allowed_document_types: Collection[str] | None,
) -> None:
    if allowed_document_types is None:
        return
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if extension in {"doc", "xls", "ppt"}:
        raise DocumentParseError("legacy_office_unsupported")
    if extension in {"docm", "xlsm", "pptm"}:
        raise DocumentParseError("macro_document_unsupported")
    if extension and extension not in allowed_document_types:
        raise DocumentParseError("unsupported_document")

# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, File, HTTPException, UploadFile

from utils.document_parser import (
    MAX_DOCUMENT_BYTES,
    DocumentParseError,
    parse_document,
)


router = APIRouter(prefix="/api/avatar-drop", tags=["avatar-drop"])


def _safe_filename(value: str) -> str:
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


async def _read_upload_limited(file: UploadFile) -> bytes:
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


@router.post("/parse-document")
async def parse_avatar_drop_document(file: UploadFile = File(...)):
    filename = _safe_filename(file.filename or "")
    try:
        data = await _read_upload_limited(file)
        parsed = await asyncio.to_thread(parse_document, filename, file.content_type or "", data)
    except DocumentParseError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code}) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"code": "document_parse_failed"}) from exc
    finally:
        await file.close()

    document_type = parsed["document_type"]
    content = parsed["content"]

    return {
        "ok": True,
        "item": {
            "type": "text",
            "name": filename,
            "mime": file.content_type or f"application/{document_type}",
            "size": len(data),
            "chars": len(content),
            "encoding": "document-parser",
            "documentType": document_type,
            "truncated": bool(parsed.get("truncated")),
            "content": content,
            "meta": parsed.get("meta") or {},
        },
    }

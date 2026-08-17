# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from utils.document_upload import parse_uploaded_document


router = APIRouter(prefix="/api/documents", tags=["documents"])

_STUDY_DOCUMENT_TYPES = frozenset({"pdf", "docx"})
_CANONICAL_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_PUBLIC_PARSE_ERROR_CODES = frozenset({
    "unsupported_document",
    "document_too_large",
    "invalid_pdf",
    "invalid_ooxml",
    "encrypted_pdf_unsupported",
    "legacy_office_unsupported",
    "macro_document_unsupported",
    "no_readable_text",
    "garbled_text",
    "document_parse_failed",
})


@router.post("/parse")
async def parse_document_upload(file: UploadFile = File(...)):
    try:
        parsed = await parse_uploaded_document(
            file,
            allowed_document_types=_STUDY_DOCUMENT_TYPES,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "document_parse_failed")
        if code not in _PUBLIC_PARSE_ERROR_CODES:
            code = "document_parse_failed"
        raise HTTPException(status_code=exc.status_code, detail={"code": code}) from exc
    return {
        "ok": True,
        "document": {
            "name": parsed.filename,
            "sourceType": parsed.document_type,
            "mime": _CANONICAL_MIME_TYPES[parsed.document_type],
            "originalSize": parsed.size,
            "chars": len(parsed.content),
            "encoding": "document-parser",
            "truncated": parsed.truncated,
            "content": parsed.content,
            "meta": parsed.meta,
        },
    }

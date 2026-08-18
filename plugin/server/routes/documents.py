# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from utils.document_upload import parse_uploaded_document
from utils.document_parser import MAX_DOCUMENT_BYTES
from utils.host_origin_guard import is_http_browser_origin_allowed


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


class _DocumentUploadTooLarge(MultiPartException):
    pass


class _LimitedDocumentMultipartParser(MultiPartParser):
    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self._current_file_bytes += end - start
            if self._current_file_bytes > MAX_DOCUMENT_BYTES:
                raise _DocumentUploadTooLarge("Document upload exceeded 16 MiB.")
        super().on_part_data(data, start, end)


async def _parse_document_form(request: Request) -> FormData:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise HTTPException(status_code=422, detail={"code": "missing_file"})
    try:
        parser = _LimitedDocumentMultipartParser(
            request.headers,
            request.stream(),
            max_files=1,
            max_fields=0,
        )
        return await parser.parse()
    except _DocumentUploadTooLarge as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "document_too_large"},
        ) from exc
    except MultiPartException as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "document_parse_failed"},
        ) from exc


@router.post("/parse")
async def parse_document_upload(request: Request):
    if not is_http_browser_origin_allowed(request.scope):
        raise HTTPException(status_code=403, detail={"code": "untrusted_origin"})
    form = await _parse_document_form(request)
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(status_code=422, detail={"code": "missing_file"})
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
            raise HTTPException(
                status_code=exc.status_code, detail={"code": code}
            ) from exc
    finally:
        await form.close()
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

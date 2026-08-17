# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

import asyncio

from fastapi import APIRouter, File, UploadFile

from utils.document_parser import parse_document
from utils.document_upload import parse_uploaded_document


router = APIRouter(prefix="/api/avatar-drop", tags=["avatar-drop"])


@router.post("/parse-document")
async def parse_avatar_drop_document(file: UploadFile = File(...)):
    parsed = await parse_uploaded_document(
        file,
        parser=parse_document,
        run_in_thread=asyncio.to_thread,
    )

    return {
        "ok": True,
        "item": {
            "type": "text",
            "name": parsed.filename,
            "mime": parsed.content_type or f"application/{parsed.document_type}",
            "size": parsed.size,
            "chars": len(parsed.content),
            "encoding": "document-parser",
            "documentType": parsed.document_type,
            "truncated": parsed.truncated,
            "content": parsed.content,
            "meta": parsed.meta,
        },
    }

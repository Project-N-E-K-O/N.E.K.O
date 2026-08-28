"""Read-only HTTP adapter for temporary plugin images."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from starlette.responses import Response

from plugin.core.image_store import get_image_store


router = APIRouter(tags=["plugin-media"])


@router.get("/media/{image_id}")
async def get_temporary_image(image_id: str) -> Response:
    record = get_image_store().get(image_id)
    if record is None:
        raise HTTPException(status_code=404, detail="temporary image not found")
    return Response(
        content=record.data,
        media_type=record.mime,
        headers={"Cache-Control": "private, max-age=300"},
    )

"""
前端静态文件路由
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()


def _get_frontend_root_dir() -> Path:
    plugin_root = Path(__file__).resolve().parents[2]
    exported = plugin_root / "frontend" / "exported"
    return exported


_FRONTEND_ROOT_DIR = _get_frontend_root_dir()


def mount_static_files(app):
    app.mount(
        "/ui/assets",
        StaticFiles(directory=str(_FRONTEND_ROOT_DIR / "assets"), check_dir=False),
        name="frontend-assets",
    )


@router.get("/ui", response_class=HTMLResponse)
@router.get("/ui/", response_class=HTMLResponse)
async def frontend_index():
    index_file = _FRONTEND_ROOT_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Frontend index not found: {index_file}. Please export frontend first.",
        )
    return FileResponse(
        str(index_file),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/ui/{full_path:path}")
async def frontend_file(full_path: str):
    if full_path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not found")

    candidate = (_FRONTEND_ROOT_DIR / full_path).resolve()
    try:
        candidate.relative_to(_FRONTEND_ROOT_DIR.resolve())
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")

    if candidate.is_file():
        if candidate.suffix.lower() == ".html":
            return FileResponse(
                str(candidate),
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return FileResponse(str(candidate))

    index_file = _FRONTEND_ROOT_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Frontend index not found: {index_file}. Please export frontend first.",
        )
    return FileResponse(
        str(index_file),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from plugin.logging_config import get_logger
from .config_store import QQAutoReplyConfigStore

router = APIRouter(tags=["qq-auto-reply-ui-api"])
logger = get_logger("qq_auto_reply.ui_api")

_UI_I18N_DIR = Path(__file__).resolve().parent / "i18n" / "ui"
_ALLOWED_UI_LOCALES = {"zh-CN", "en-US"}


@router.get("/plugin/{plugin_id}/ui-api/locale")
async def get_qq_auto_reply_ui_locale(plugin_id: str) -> JSONResponse:
    _ensure_plugin(plugin_id)
    try:
        from utils.language_utils import get_global_language_full

        locale = _normalize_ui_locale(str(get_global_language_full() or "zh-CN"))
    except Exception:
        locale = "zh-CN"
    return JSONResponse({"locale": locale})


@router.get("/plugin/{plugin_id}/ui-api/i18n/ui/{locale}.json")
async def get_qq_auto_reply_ui_i18n(plugin_id: str, locale: str) -> Response:
    _ensure_plugin(plugin_id)
    normalized = _normalize_ui_locale(str(locale or ""))
    file = _UI_I18N_DIR / f"{normalized}.json"
    logger.warning(
        f"[qq_auto_reply ui_i18n debug] raw_locale={locale!r}, normalized={normalized!r}, dir={_UI_I18N_DIR}, file={file}, exists={file.exists()}, is_file={file.is_file()}"
    )
    if ".." in normalized or "/" in normalized or "\\" in normalized:
        return Response(status_code=404)
    if normalized not in _ALLOWED_UI_LOCALES:
        return Response(status_code=404)
    if not file.is_file():
        return Response(status_code=404)
    return FileResponse(file)


@router.get("/plugin/{plugin_id}/ui-api/qrcode")
async def get_qq_auto_reply_qrcode(plugin_id: str) -> Response:
    _ensure_plugin(plugin_id)
    config_store = QQAutoReplyConfigStore(Path(__file__).resolve().parent)
    config = await config_store.load()
    configured_root = str(config.get("napcat_directory") or "").strip()
    if configured_root:
        qrcode = Path(configured_root) / "cache" / "qrcode.png"
    else:
        qrcode = Path(__file__).resolve().parent / "NapCat.Shell" / "cache" / "qrcode.png"
    logger.warning(f"[qq_auto_reply ui_qrcode debug] qrcode={qrcode}, exists={qrcode.exists()}, is_file={qrcode.is_file()}")
    if not qrcode.is_file():
        return Response(status_code=404)
    return FileResponse(qrcode)


def _normalize_ui_locale(locale: str) -> str:
    normalized = str(locale or "").strip().replace("_", "-").lower()
    if normalized == "zh" or normalized.startswith("zh-"):
        return "zh-CN"
    if normalized.startswith("en"):
        return "en-US"
    return "zh-CN"


def _ensure_plugin(plugin_id: str) -> None:
    if plugin_id != "qq_auto_reply":
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' has no QQ UI i18n API")

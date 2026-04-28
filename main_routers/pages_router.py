# -*- coding: utf-8 -*-
"""
Pages Router

Handles HTML page rendering endpoints.
"""

import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .shared_state import get_templates

router = APIRouter(tags=["pages"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REACT_CHAT_ASSET_VERSION_PATHS = (
    _PROJECT_ROOT / "static/react/neko-chat/neko-chat-window.css",
    _PROJECT_ROOT / "static/react/neko-chat/neko-chat-window.iife.js",
    _PROJECT_ROOT / "static/app-react-chat-window.js",
    _PROJECT_ROOT / "static/app-chat-adapter.js",
    _PROJECT_ROOT / "static/app-buttons.js",
)
_REACT_CHAT_ASSET_CACHE_TTL = 30.0
_react_chat_asset_version_cache: tuple[float, str] = (0.0, "0")


def _vrm_defaults_ctx() -> dict:
    """返回 VRM 光照默认值，供 Jinja2 模板注入到 <script> 中。"""
    from config import DEFAULT_VRM_LIGHTING
    return {"vrm_defaults": dict(DEFAULT_VRM_LIGHTING)}


def _react_chat_assets_ctx() -> dict:
    """返回 React Chat 相关静态资源的统一缓存版本号。"""
    global _react_chat_asset_version_cache
    now = time.monotonic()
    cached_at, cached_version = _react_chat_asset_version_cache
    if now - cached_at < _REACT_CHAT_ASSET_CACHE_TTL:
        return {"react_chat_asset_version": cached_version}

    latest_mtime = 0
    for path in _REACT_CHAT_ASSET_VERSION_PATHS:
        try:
            latest_mtime = max(latest_mtime, int(path.stat().st_mtime))
        except OSError:
            continue

    version = str(latest_mtime or 0)
    _react_chat_asset_version_cache = (now, version)
    return {"react_chat_asset_version": version}


@router.get("/", response_class=HTMLResponse)
async def get_default_index(request: Request):
    templates = get_templates()
    return templates.TemplateResponse("templates/index.html", {
        "request": request,
        **_vrm_defaults_ctx(),
        **_react_chat_assets_ctx(),
    })


def _render_model_manager(request: Request):
    """渲染模型管理器页面的内部实现"""
    templates = get_templates()
    return templates.TemplateResponse("templates/model_manager.html", {
        "request": request,
        **_vrm_defaults_ctx(),
    })


@router.get("/l2d", response_class=HTMLResponse)
async def get_l2d_manager(request: Request):
    """渲染模型管理器页面(兼容旧路由)"""
    return _render_model_manager(request)


@router.get("/model_manager", response_class=HTMLResponse)
async def get_model_manager(request: Request):
    """渲染模型管理器页面"""
    return _render_model_manager(request)


@router.get("/live2d_parameter_editor", response_class=HTMLResponse)
async def live2d_parameter_editor(request: Request):
    """Live2D参数编辑器页面"""
    templates = get_templates()
    return templates.TemplateResponse("templates/live2d_parameter_editor.html", {
        "request": request
    })


@router.get("/soccer_demo", response_class=HTMLResponse)
async def soccer_demo(request: Request):
    """Soccer MVP demo (VRM + L2D avatars)"""
    templates = get_templates()
    return templates.TemplateResponse("templates/soccer_demo.html", {
        "request": request,
        **_vrm_defaults_ctx(),
    })


@router.get("/live2d_emotion_manager", response_class=HTMLResponse)
async def live2d_emotion_manager(request: Request):
    """Live2D情感映射管理器页面"""
    templates = get_templates()
    return templates.TemplateResponse("templates/live2d_emotion_manager.html", {
        "request": request
    })


@router.get("/vrm_emotion_manager", response_class=HTMLResponse)
async def vrm_emotion_manager(request: Request):
    """VRM情感映射管理器页面"""
    templates = get_templates()
    return templates.TemplateResponse("templates/vrm_emotion_manager.html", {
        "request": request
    })


@router.get("/mmd_emotion_manager", response_class=HTMLResponse)
async def mmd_emotion_manager(request: Request):
    """MMD情感映射管理器页面"""
    templates = get_templates()
    return templates.TemplateResponse("templates/mmd_emotion_manager.html", {
        "request": request
    })


@router.get('/voice_clone', response_class=HTMLResponse)
async def voice_clone_page(request: Request):
    templates = get_templates()
    return templates.TemplateResponse("templates/voice_clone.html", {"request": request})


@router.get("/api_key", response_class=HTMLResponse)
async def api_key_settings(request: Request):
    """API Key 设置页面"""
    templates = get_templates()
    return templates.TemplateResponse("templates/api_key_settings.html", {
        "request": request
    })


@router.get('/chara_manager')
async def chara_manager_redirect(request: Request):
    url = "/character_card_manager"
    if request.query_params:
        url += "?" + str(request.query_params)
    return RedirectResponse(url=url, status_code=307)


@router.get('/character_card_manager', response_class=HTMLResponse)
async def character_card_manager_page(request: Request, lanlan_name: str = ""):
    templates = get_templates()
    return templates.TemplateResponse("templates/character_card_manager.html", {"request": request, "lanlan_name": lanlan_name})


@router.get('/cloudsave_manager', response_class=HTMLResponse)
async def cloudsave_manager_page(request: Request, lanlan_name: str = ""):
    templates = get_templates()
    return templates.TemplateResponse("templates/cloudsave_manager.html", {"request": request, "lanlan_name": lanlan_name})


@router.get('/memory_browser', response_class=HTMLResponse)
async def memory_browser(request: Request):
    templates = get_templates()
    return templates.TemplateResponse('templates/memory_browser.html', {"request": request})


@router.get('/cookies_login', response_class=HTMLResponse)
async def cookies_login_page(request: Request):
    """媒体凭证获取页面"""
    templates = get_templates()
    return templates.TemplateResponse('templates/cookies_login.html', {"request": request})



@router.get("/chat", response_class=HTMLResponse)
async def get_chat_page(request: Request):
    """Chat 独立窗口页面"""
    templates = get_templates()
    return templates.TemplateResponse("templates/chat.html", {
        "request": request,
        **_vrm_defaults_ctx(),
        **_react_chat_assets_ctx(),
    })


@router.get("/subtitle", response_class=HTMLResponse)
async def get_subtitle_page(request: Request):
    """Subtitle 独立窗口页面"""
    templates = get_templates()
    return templates.TemplateResponse("templates/subtitle.html", {"request": request})


@router.get("/agenthud", response_class=HTMLResponse)
async def get_agenthud_page(request: Request):
    """AgentHUD 独立窗口页面"""
    templates = get_templates()
    return templates.TemplateResponse("templates/agenthud.html", {"request": request})


@router.get("/card_maker", response_class=HTMLResponse)
async def get_card_maker_page(request: Request):
    """卡面制作页面（独立加载模型并可调整构图）"""
    templates = get_templates()
    return templates.TemplateResponse("templates/card_maker.html", {
        "request": request,
        **_vrm_defaults_ctx(),
    })


@router.get("/jukebox", response_class=HTMLResponse)
async def get_jukebox_page(request: Request):
    """Jukebox 点歌台独立窗口页面（Electron 加载）"""
    templates = get_templates()
    return templates.TemplateResponse("templates/jukebox.html", {"request": request})


@router.get("/jukebox/manager", response_class=HTMLResponse)
async def get_jukebox_manager_page(request: Request):
    """Jukebox 管理器独立窗口页面 (从点歌台打开)"""
    templates = get_templates()
    return templates.TemplateResponse("templates/jukebox_manager.html", {"request": request})


@router.get("/toast", response_class=HTMLResponse)
async def get_toast_page(request: Request):
    """Toast 通知独立窗口页面（Electron 加载）"""
    templates = get_templates()
    return templates.TemplateResponse("templates/toast.html", {"request": request})



@router.get("/{lanlan_name}", response_class=HTMLResponse)
async def get_index(request: Request, lanlan_name: str):
    # lanlan_name 将从 URL 中提取，前端会通过 API 获取配置
    templates = get_templates()
    return templates.TemplateResponse("templates/index.html", {
        "request": request,
        **_vrm_defaults_ctx(),
        **_react_chat_assets_ctx(),
    })

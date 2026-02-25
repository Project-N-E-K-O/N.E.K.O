# -*- coding: utf-8 -*-
"""
Pages Router

Handles HTML page rendering endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .shared_state import get_templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def get_default_index(request: Request):
    templates = get_templates()
    return templates.TemplateResponse("templates/index.html", {
        "request": request
    })


def _render_model_manager(request: Request):
    """渲染模型管理器页面的内部实现"""
    templates = get_templates()
    return templates.TemplateResponse("templates/model_manager.html", {
        "request": request
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


@router.get('/chara_manager', response_class=HTMLResponse)
async def chara_manager(request: Request):
    """渲染主控制页面"""
    templates = get_templates()
    return templates.TemplateResponse('templates/chara_manager.html', {"request": request})


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


@router.get('/steam_workshop_manager', response_class=HTMLResponse)
async def steam_workshop_manager_page(request: Request, lanlan_name: str = ""):
    templates = get_templates()
    return templates.TemplateResponse("templates/steam_workshop_manager.html", {"request": request, "lanlan_name": lanlan_name})


@router.get('/memory_browser', response_class=HTMLResponse)
async def memory_browser(request: Request):
    templates = get_templates()
    return templates.TemplateResponse('templates/memory_browser.html', {"request": request})


@router.get('/cookies_login', response_class=HTMLResponse)
async def cookies_login_page(request: Request):
    """媒体凭证获取页面"""
    templates = get_templates()
    return templates.TemplateResponse('templates/cookies_login.html', {"request": request})



@router.get("/{lanlan_name}", response_class=HTMLResponse)
async def get_index(request: Request, lanlan_name: str):
    # lanlan_name 将从 URL 中提取，前端会通过 API 获取配置
    templates = get_templates()
    return templates.TemplateResponse("templates/index.html", {
        "request": request
    })
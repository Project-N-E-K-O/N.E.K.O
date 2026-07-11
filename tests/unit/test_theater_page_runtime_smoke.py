from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from main_routers import pages_router
from main_routers.shared_state import init_shared_state


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_pages_client() -> TestClient:
    """构造只挂页面路由的轻量客户端，用于验证 theater 不影响聊天/字幕页面。"""
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=Jinja2Templates(directory=PROJECT_ROOT),
        config_manager=None,
        logger=None,
        initialize_character_data=None,
    )
    app = FastAPI()
    app.include_router(pages_router.router)
    return TestClient(app)


def _text_for(client: TestClient, path: str) -> str:
    """请求指定页面并返回渲染后的 HTML，失败时让测试直接暴露路由问题。"""
    response = client.get(path)
    assert response.status_code == 200
    return response.text


def test_theater_chat_and_subtitle_pages_render_without_cross_injection():
    """验证 theater、chat、subtitle 可并行渲染，且小剧场资源不会注入聊天/字幕页面。"""
    with _build_pages_client() as client:
        theater_html = _text_for(client, "/theater")
        chat_html = _text_for(client, "/chat")
        subtitle_html = _text_for(client, "/subtitle")

    assert "data-theater-app" in theater_html
    assert "/static/js/theater.js" in theater_html
    assert "/static/css/theater.css" in theater_html

    assert "react-chat-window-root" in chat_html
    assert "/static/js/theater.js" not in chat_html
    assert "data-theater-app" not in chat_html

    assert "subtitle-display" in subtitle_html
    assert "/static/js/theater.js" not in subtitle_html
    assert "data-theater-app" not in subtitle_html

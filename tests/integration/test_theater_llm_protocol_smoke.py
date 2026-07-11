"""小剧场单次演绎的 OpenAI-compatible 协议纵向测试。"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

import pytest

from services.theater import llm


class _ProtocolConfigManager:
    """把 summary 档指向进程内兼容服务器。"""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def get_model_api_config(self, tier: str) -> dict[str, str]:
        """轻量小剧场只允许读取一次 summary 档配置。"""
        assert tier == "summary"
        return {
            "model": "theater-light-smoke",
            "base_url": self.base_url,
            "api_key": "test-key",
            "provider_type": "openai_compatible",
        }


@contextmanager
def _compatible_server() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """启动本地模型服务器并记录实际 wire 请求。"""
    requests: list[dict[str, Any]] = []

    class _Handler(BaseHTTPRequestHandler):
        """返回固定的旁白与猫娘对白 JSON。"""

        def do_POST(self) -> None:  # noqa: N802 - 标准库要求此方法名
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            requests.append(payload)
            response = {
                "id": "chatcmpl-light",
                "object": "chat.completion",
                "created": 1,
                "model": payload.get("model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"narration":"雨声轻了一点。","dialogue":"我听见你说的话了喵。"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }
            encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: Any) -> None:
            """关闭测试服务器访问日志。"""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", requests
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.asyncio
async def test_single_turn_uses_one_model_request():
    """一次剧情演绎只产生一个兼容协议请求。"""
    with _compatible_server() as (base_url, requests):
        result = await llm.generate_turn_async(
            config_manager=_ProtocolConfigManager(base_url),
            lanlan_name="兰兰",
            story={"background": "雨夜房间", "theme": "陪伴"},
            scene={"title": "窗边", "text": "窗外正在下雨。"},
            node={"title": "一起等灯", "summary": "两人留在窗边。"},
            user_message="我陪你等灯亮",
            progress_kind="graph_progress",
            callback="你把灯放在桌边。",
            state={},
            recent_turns=[],
        )
    assert result == {"narration": "雨声轻了一点。", "dialogue": "我听见你说的话了喵。", "choice_rewrites": []}
    assert len(requests) == 1
    assert requests[0]["model"] == "theater-light-smoke"

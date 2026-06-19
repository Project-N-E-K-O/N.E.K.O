import asyncio
import json
import socket

import pytest

from plugin.plugins.live2d_auto_layer.core.cubism.editor_api import (
    CubismEditorApiError,
    CubismEditorClient,
    is_cubism_port_open,
    make_request,
)


def test_make_request_uses_cubism_external_api_shape() -> None:
    request = make_request("GetCurrentModelUID", {}, request_id="req-1")

    assert request["Version"] == "1.0.0"
    assert request["RequestId"] == "req-1"
    assert request["Type"] == "Request"
    assert request["Method"] == "GetCurrentModelUID"
    assert request["Data"] == {}
    assert isinstance(request["Timestamp"], int)


def test_is_cubism_port_open_reports_false_for_unused_local_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        unused_port = sock.getsockname()[1]

    assert is_cubism_port_open("127.0.0.1", unused_port, timeout_seconds=0.05) is False


@pytest.mark.asyncio
async def test_register_plugin_saves_returned_token(monkeypatch, tmp_path) -> None:
    fake_ws = _FakeCubismWebSocket()
    monkeypatch.setattr(
        "plugin.plugins.live2d_auto_layer.core.cubism.editor_api.websockets.connect",
        lambda *_args, **_kwargs: fake_ws,
    )

    token_path = tmp_path / "cubism_editor_token.json"
    client = CubismEditorClient(token_path=token_path, plugin_path=tmp_path)

    result = await client.register_plugin()

    assert result["registered"] is True
    assert result["approved"] is True
    assert json.loads(token_path.read_text(encoding="utf-8")) == {"token": "token-123"}
    sent_methods = [message["Method"] for message in fake_ws.sent_messages]
    assert sent_methods == ["RegisterPlugin", "GetIsApproval"]


@pytest.mark.asyncio
async def test_register_plugin_keeps_socket_open_until_approval(monkeypatch, tmp_path) -> None:
    fake_ws = _FakeCubismWebSocket(approval_sequence=[False, False, True])
    monkeypatch.setattr(
        "plugin.plugins.live2d_auto_layer.core.cubism.editor_api.websockets.connect",
        lambda *_args, **_kwargs: fake_ws,
    )

    token_path = tmp_path / "cubism_editor_token.json"
    client = CubismEditorClient(token_path=token_path, plugin_path=tmp_path)

    result = await client.register_plugin(
        approval_wait_seconds=1,
        approval_poll_interval_seconds=0.01,
    )

    assert result["approved"] is True
    assert result["approval_wait_timed_out"] is False
    sent_methods = [message["Method"] for message in fake_ws.sent_messages]
    assert sent_methods == ["RegisterPlugin", "GetIsApproval", "GetIsApproval", "GetIsApproval"]


@pytest.mark.asyncio
async def test_send_log_requires_user_approval_before_api_call(monkeypatch, tmp_path) -> None:
    fake_ws = _FakeCubismWebSocket(approved=False)
    monkeypatch.setattr(
        "plugin.plugins.live2d_auto_layer.core.cubism.editor_api.websockets.connect",
        lambda *_args, **_kwargs: fake_ws,
    )

    token_path = tmp_path / "cubism_editor_token.json"
    token_path.write_text(json.dumps({"token": "token-123"}), encoding="utf-8")
    client = CubismEditorClient(token_path=token_path, plugin_path=tmp_path)

    with pytest.raises(CubismEditorApiError) as exc_info:
        await client.send_log("hello")

    assert exc_info.value.error_type == "ApprovalRequired"
    sent_methods = [message["Method"] for message in fake_ws.sent_messages]
    assert sent_methods == ["RegisterPlugin", "GetIsApproval"]


@pytest.mark.asyncio
async def test_editor_state_skips_current_document_when_no_document_is_open(monkeypatch, tmp_path) -> None:
    fake_ws = _FakeCubismWebSocket(
        response_data_by_method={
            "GetCurrentEditMode": {"EditMode": ""},
            "GetDocuments": {"PhysicsDocuments": [], "ModelingDocuments": [], "AnimationDocuments": []},
        },
    )
    monkeypatch.setattr(
        "plugin.plugins.live2d_auto_layer.core.cubism.editor_api.websockets.connect",
        lambda *_args, **_kwargs: fake_ws,
    )

    token_path = tmp_path / "cubism_editor_token.json"
    token_path.write_text(json.dumps({"token": "token-123"}), encoding="utf-8")
    client = CubismEditorClient(token_path=token_path, plugin_path=tmp_path, timeout_seconds=0.01)

    result = await client.editor_state()

    assert result["approved"] is True
    assert result["current_document"]["error"] == "NoOpenDocument"
    sent_methods = [message["Method"] for message in fake_ws.sent_messages]
    assert sent_methods == [
        "RegisterPlugin",
        "GetIsApproval",
        "GetCurrentEditMode",
        "GetDocuments",
    ]


@pytest.mark.asyncio
async def test_editor_state_keeps_optional_timeout_as_field_error(monkeypatch, tmp_path) -> None:
    fake_ws = _FakeCubismWebSocket(
        timeout_methods={"GetCurrentDocumentUID"},
        response_data_by_method={
            "GetCurrentEditMode": {"EditMode": "Modeling"},
            "GetDocuments": {
                "PhysicsDocuments": [],
                "ModelingDocuments": [
                    {
                        "DocumentUID": "document-1",
                        "DocumentFilePath": "D:/model/model.cmo3",
                        "Views": [{"ModelUID": "model-1"}],
                    }
                ],
                "AnimationDocuments": [],
            },
            "GetCurrentModelUID": {"ModelUID": "model-1"},
        },
    )
    monkeypatch.setattr(
        "plugin.plugins.live2d_auto_layer.core.cubism.editor_api.websockets.connect",
        lambda *_args, **_kwargs: fake_ws,
    )

    token_path = tmp_path / "cubism_editor_token.json"
    token_path.write_text(json.dumps({"token": "token-123"}), encoding="utf-8")
    client = CubismEditorClient(token_path=token_path, plugin_path=tmp_path, timeout_seconds=0.01)

    result = await client.editor_state()

    assert result["approved"] is True
    assert result["current_model"] == {"ModelUID": "model-1"}
    assert result["current_document"]["error"] == "Timeout"
    sent_methods = [message["Method"] for message in fake_ws.sent_messages]
    assert sent_methods == [
        "RegisterPlugin",
        "GetIsApproval",
        "GetCurrentEditMode",
        "GetDocuments",
        "GetCurrentModelUID",
        "GetCurrentDocumentUID",
    ]


@pytest.mark.asyncio
async def test_wait_for_moc_export_accepts_notify_response_event(monkeypatch, tmp_path) -> None:
    fake_ws = _FakeCubismWebSocket(
        event_after_methods={
            "NotifyMocFileExported": {
                "Version": "1.0.0",
                "Timestamp": 1,
                "Type": "Response",
                "Method": "NotifyMocFileExported",
                "Data": {"ModelFilePath": "D:/model/runtime/model3.json"},
            },
        },
    )
    monkeypatch.setattr(
        "plugin.plugins.live2d_auto_layer.core.cubism.editor_api.websockets.connect",
        lambda *_args, **_kwargs: fake_ws,
    )

    token_path = tmp_path / "cubism_editor_token.json"
    token_path.write_text(json.dumps({"token": "token-123"}), encoding="utf-8")
    client = CubismEditorClient(token_path=token_path, plugin_path=tmp_path)

    result = await client.wait_for_moc_export(timeout_seconds=1)

    assert result["event"] == "NotifyMocFileExported"
    assert result["data"]["ModelFilePath"] == "D:/model/runtime/model3.json"


class _FakeCubismWebSocket:
    def __init__(
        self,
        *,
        approved: bool = True,
        approval_sequence: list[bool] | None = None,
        timeout_methods: set[str] | None = None,
        event_after_methods: dict[str, dict[str, object]] | None = None,
        response_data_by_method: dict[str, dict[str, object]] | None = None,
    ):
        self.approved = approved
        self.approval_sequence = list(approval_sequence or [])
        self.timeout_methods = set(timeout_methods or set())
        self.event_after_methods = dict(event_after_methods or {})
        self.response_data_by_method = dict(response_data_by_method or {})
        self.sent_messages: list[dict[str, object]] = []
        self._responses: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    async def send(self, raw: str) -> None:
        request = json.loads(raw)
        self.sent_messages.append(request)
        method = request["Method"]
        if method in self.timeout_methods:
            return
        if method == "RegisterPlugin":
            data = {"Token": "token-123"}
        elif method == "GetIsApproval" and self.approval_sequence:
            data = {"Result": self.approval_sequence.pop(0)}
        elif method in self.response_data_by_method:
            data = self.response_data_by_method[str(method)]
        else:
            data = {"Result": self.approved}
        self._responses.append(
            json.dumps(
                {
                    "Version": request["Version"],
                    "Timestamp": request["Timestamp"],
                    "RequestId": request["RequestId"],
                    "Type": "Response",
                    "Method": method,
                    "Data": data,
                }
            )
        )
        event = self.event_after_methods.get(str(method))
        if event:
            self._responses.append(json.dumps(event))

    async def recv(self) -> str:
        if not self._responses:
            await asyncio.sleep(1)
        return self._responses.pop(0)



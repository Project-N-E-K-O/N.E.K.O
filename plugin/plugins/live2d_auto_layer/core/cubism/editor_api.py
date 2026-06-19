"""Client for Live2D Cubism Editor External Application Integration."""

from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets


DEFAULT_CUBISM_HOST = "127.0.0.1"
DEFAULT_CUBISM_PORT = 22033
DEFAULT_API_VERSION = "1.0.0"
DEFAULT_PLUGIN_NAME = "N.E.K.O Live2D Auto Layer"
DEFAULT_APPROVAL_WAIT_SECONDS = 120.0
DEFAULT_APPROVAL_POLL_INTERVAL_SECONDS = 1.0


class CubismEditorApiError(RuntimeError):
    def __init__(self, message: str, *, error_type: str = "", payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.payload = payload or {}


@dataclass(frozen=True, slots=True)
class CubismConnectionStatus:
    host: str
    port: int
    port_open: bool
    token_saved: bool
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "port_open": self.port_open,
            "token_saved": self.token_saved,
            "status": self.status,
        }


class CubismEditorClient:
    def __init__(
        self,
        *,
        host: str = DEFAULT_CUBISM_HOST,
        port: int = DEFAULT_CUBISM_PORT,
        token_path: str | Path | None = None,
        plugin_name: str = DEFAULT_PLUGIN_NAME,
        plugin_path: str | Path | None = None,
        timeout_seconds: float = 5.0,
    ):
        self.host = str(host or DEFAULT_CUBISM_HOST)
        self.port = int(port or DEFAULT_CUBISM_PORT)
        self.token_path = Path(token_path) if token_path is not None else None
        self.plugin_name = plugin_name
        self.plugin_path = str(plugin_path or "")
        self.timeout_seconds = float(timeout_seconds)

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def status(self) -> CubismConnectionStatus:
        port_open = is_cubism_port_open(self.host, self.port, timeout_seconds=0.5)
        token_saved = bool(self._load_token())
        if not port_open:
            status = "api_unavailable"
        elif token_saved:
            status = "api_available_token_saved"
        else:
            status = "api_available_no_token"
        return CubismConnectionStatus(
            host=self.host,
            port=self.port,
            port_open=port_open,
            token_saved=token_saved,
            status=status,
        )

    async def register_plugin(
        self,
        *,
        approval_wait_seconds: float = DEFAULT_APPROVAL_WAIT_SECONDS,
        approval_poll_interval_seconds: float = DEFAULT_APPROVAL_POLL_INTERVAL_SECONDS,
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "Name": self.plugin_name,
        }
        token = self._load_token()
        if token:
            data["Token"] = token
        if self.plugin_path:
            data["Path"] = self.plugin_path
        async with websockets.connect(self.url, open_timeout=self.timeout_seconds) as ws:
            response = await self._request(ws, "RegisterPlugin", data)
            response_data = _response_data(response)
            next_token = str(response_data.get("Token") or "")
            if next_token:
                self._save_token(next_token)
            approval = await self._safe_get_approval(ws)
            approval_wait_timed_out = False
            if not approval and approval_wait_seconds > 0:
                approval = await self._wait_for_approval(
                    ws,
                    wait_seconds=approval_wait_seconds,
                    poll_interval_seconds=approval_poll_interval_seconds,
                )
                approval_wait_timed_out = not approval
            return {
                "registered": bool(next_token),
                "token_saved": bool(next_token),
                "approved": approval,
                "approval_wait_timed_out": approval_wait_timed_out,
                "approval_wait_seconds": max(0.0, float(approval_wait_seconds)),
                "host": self.host,
                "port": self.port,
            }

    async def editor_state(self) -> dict[str, object]:
        async with websockets.connect(self.url, open_timeout=self.timeout_seconds) as ws:
            await self._register_on_socket(ws)
            approval = await self._safe_get_approval(ws)
            state: dict[str, object] = {
                "approved": approval,
                "host": self.host,
                "port": self.port,
            }
            state["current_edit_mode"] = await self._optional_request_data(ws, "GetCurrentEditMode", {})
            state["documents"] = await self._optional_request_data(ws, "GetDocuments", {})
            if _has_open_document(state["documents"]):
                state["current_model"] = await self._optional_request_data(ws, "GetCurrentModelUID", {})
                state["current_document"] = await self._optional_request_data(ws, "GetCurrentDocumentUID", {})
            else:
                state["current_model"] = {
                    "error": "NoOpenDocument",
                    "message": "No Live2D Cubism document is open.",
                }
                state["current_document"] = {
                    "error": "NoOpenDocument",
                    "message": "No Live2D Cubism document is open.",
                }
            return state

    async def send_log(self, message: str, *, log_type: str = "info", display: bool = True) -> dict[str, object]:
        clean_message = str(message or "").strip()
        if not clean_message:
            raise ValueError("message is required")
        if len(clean_message) > 5000:
            clean_message = clean_message[:5000]
        clean_type = str(log_type or "info").strip().lower()
        if clean_type not in {"info", "warning"}:
            clean_type = "info"
        async with websockets.connect(self.url, open_timeout=self.timeout_seconds) as ws:
            await self._register_on_socket(ws)
            await self._require_approval(ws)
            response = await self._request(
                ws,
                "SendCubismLog",
                {
                    "Type": clean_type,
                    "Message": clean_message,
                    "Display": bool(display),
                },
            )
            return {
                "sent": True,
                "response": _response_data(response),
            }

    async def wait_for_moc_export(self, *, timeout_seconds: float = 30.0) -> dict[str, object]:
        async with websockets.connect(self.url, open_timeout=self.timeout_seconds) as ws:
            await self._register_on_socket(ws)
            await self._require_approval(ws)
            accepted = await self._enable_moc_export_notifications(ws)
            deadline = max(0.1, float(timeout_seconds))
            try:
                async with asyncio.timeout(deadline):
                    while True:
                        raw = await ws.recv()
                        message = _loads_message(raw)
                        if message.get("Method") == "NotifyMocFileExported":
                            return {
                                "event": "NotifyMocFileExported",
                                "accepted": accepted,
                                "data": _response_data(message),
                            }
            except TimeoutError:
                return {
                    "event": "",
                    "accepted": accepted,
                    "timed_out": True,
                    "timeout_seconds": deadline,
                }

    async def _register_on_socket(self, ws: Any) -> dict[str, object]:
        data: dict[str, object] = {"Name": self.plugin_name}
        token = self._load_token()
        if token:
            data["Token"] = token
        if self.plugin_path:
            data["Path"] = self.plugin_path
        response = await self._request(ws, "RegisterPlugin", data)
        response_data = _response_data(response)
        next_token = str(response_data.get("Token") or "")
        if next_token:
            self._save_token(next_token)
        return response_data

    async def _safe_get_approval(self, ws: Any) -> bool:
        try:
            response = await self._request(ws, "GetIsApproval", {})
        except CubismEditorApiError:
            return False
        data = _response_data(response)
        return bool(data.get("Result"))

    async def _require_approval(self, ws: Any) -> None:
        if await self._safe_get_approval(ws):
            return
        raise CubismEditorApiError(
            "Cubism Editor has not approved this external application yet. Open External Application Integration Settings and enable Allow for N.E.K.O Live2D Auto Layer.",
            error_type="ApprovalRequired",
        )

    async def _wait_for_approval(self, ws: Any, *, wait_seconds: float, poll_interval_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        interval = max(0.01, float(poll_interval_seconds))
        while time.monotonic() < deadline:
            await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
            if await self._safe_get_approval(ws):
                return True
        return False

    async def _optional_request_data(self, ws: Any, method: str, data: dict[str, object]) -> dict[str, object]:
        try:
            response = await self._request(ws, method, data)
        except CubismEditorApiError as exc:
            return {
                "error": exc.error_type or "CubismEditorApiError",
                "message": str(exc),
            }
        return _response_data(response)

    async def _enable_moc_export_notifications(self, ws: Any) -> dict[str, object]:
        try:
            response = await self._request(ws, "NotifyMocFileExported", {"Enabled": True})
        except CubismEditorApiError as exc:
            if exc.error_type != "InvalidData":
                raise
            response = await self._request(ws, "NotifyMocFileExported", {"Enable": True})
        return _response_data(response)

    async def _request(self, ws: Any, method: str, data: dict[str, object]) -> dict[str, object]:
        request_id = uuid.uuid4().hex
        request = make_request(method, data, request_id=request_id)
        await ws.send(json.dumps(request, ensure_ascii=False))
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while True:
                    raw = await ws.recv()
                    message = _loads_message(raw)
                    if message.get("RequestId") != request_id:
                        continue
                    if message.get("Type") == "Error":
                        payload = _response_data(message)
                        error_type = str(payload.get("ErrorType") or "")
                        raise CubismEditorApiError(
                            f"Cubism Editor API error for {method}: {error_type or 'unknown'}",
                            error_type=error_type,
                            payload=message,
                        )
                    return message
        except TimeoutError as exc:
            raise CubismEditorApiError(
                f"Cubism Editor API timed out for {method}",
                error_type="Timeout",
            ) from exc

    def _load_token(self) -> str:
        if self.token_path is None or not self.token_path.is_file():
            return ""
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(data, dict):
            return ""
        return str(data.get("token") or "")

    def _save_token(self, token: str) -> None:
        if self.token_path is None:
            return
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(
            json.dumps({"token": token}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def make_request(method: str, data: dict[str, object], *, request_id: str | None = None) -> dict[str, object]:
    return {
        "Version": DEFAULT_API_VERSION,
        "Timestamp": int(time.time() * 1000),
        "RequestId": request_id or uuid.uuid4().hex,
        "Type": "Request",
        "Method": method,
        "Data": data,
    }


def is_cubism_port_open(host: str = DEFAULT_CUBISM_HOST, port: int = DEFAULT_CUBISM_PORT, *, timeout_seconds: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _loads_message(raw: Any) -> dict[str, object]:
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise CubismEditorApiError("Cubism Editor returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise CubismEditorApiError("Cubism Editor message must be an object")
    return data


def _response_data(message: dict[str, object]) -> dict[str, object]:
    data = message.get("Data")
    return dict(data) if isinstance(data, dict) else {}


def _has_open_document(documents: object) -> bool:
    if not isinstance(documents, dict):
        return False
    for key in ("PhysicsDocuments", "ModelingDocuments", "AnimationDocuments"):
        value = documents.get(key)
        if isinstance(value, list) and value:
            return True
    return False


__all__ = [
    "CubismConnectionStatus",
    "CubismEditorApiError",
    "CubismEditorClient",
    "DEFAULT_APPROVAL_WAIT_SECONDS",
    "DEFAULT_CUBISM_HOST",
    "DEFAULT_CUBISM_PORT",
    "DEFAULT_PLUGIN_NAME",
    "is_cubism_port_open",
    "make_request",
]


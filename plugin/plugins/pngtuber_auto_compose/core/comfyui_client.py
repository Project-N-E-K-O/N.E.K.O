"""Minimal ComfyUI HTTP client used by the pipeline engine."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


class ComfyUIClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.base_url = str(base_url or "http://127.0.0.1:8188").rstrip("/")
        self.timeout = timeout

    async def system_stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._json_request, "GET", "/system_stats")

    async def object_info(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._json_request, "GET", "/object_info")

    async def submit_prompt(self, prompt: dict[str, Any], *, client_id: str | None = None) -> dict[str, Any]:
        payload = {"prompt": prompt, "client_id": client_id or uuid.uuid4().hex}
        return await asyncio.to_thread(self._json_request, "POST", "/prompt", payload)

    async def history(self, prompt_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(str(prompt_id), safe="")
        return await asyncio.to_thread(self._json_request, "GET", f"/history/{encoded}")

    async def upload_image(self, filename: str, data: bytes, *, subfolder: str = "", image_type: str = "input") -> dict[str, Any]:
        return await asyncio.to_thread(self._upload_image, filename, data, subfolder, image_type)

    async def view_image(self, *, filename: str, subfolder: str = "", image_type: str = "output") -> bytes:
        query = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": image_type})
        return await asyncio.to_thread(self._bytes_request, "GET", f"/view?{query}")

    async def check(self) -> dict[str, Any]:
        target = f"{self.base_url}/system_stats"
        try:
            data = await self.system_stats()
            return {"ok": True, "status": 200, "url": target, "data": data}
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {"ok": False, "url": target, "error": str(exc)}

    def _json_request(self, method: str, path: str, payload: Any | None = None) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        data = self._request(method, path, body=body, headers=headers)
        if not data:
            return {}
        parsed = json.loads(data.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def _bytes_request(self, method: str, path: str) -> bytes:
        return self._request(method, path, headers={"Accept": "*/*"})

    def _request(self, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        context = ssl._create_unverified_context() if url.startswith("https://") else None
        with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
            return response.read()

    def _upload_image(self, filename: str, data: bytes, subfolder: str, image_type: str) -> dict[str, Any]:
        boundary = f"----NekoPNGTuber{uuid.uuid4().hex}"
        fields = {
            "subfolder": subfolder,
            "type": image_type,
            "overwrite": "true",
        }
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode("utf-8"),
                b"Content-Type: application/octet-stream\r\n\r\n",
                data,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        body = b"".join(chunks)
        response = self._request(
            "POST",
            "/upload/image",
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        parsed = json.loads(response.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {"data": parsed}

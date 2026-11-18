"""The desktop owner can manage access without granting remote session holders that ability."""

import hashlib
import os
import secrets
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from utils import config_manager
from utils.file_utils import atomic_write_json
from utils.host_origin_guard import HostOriginGuardMiddleware
from utils.instance_access import InstanceAccessMiddleware


@pytest.mark.asyncio
async def test_remote_access_settings_apply_and_preserve_the_owner_boundary(monkeypatch, tmp_path):
    from main_routers.system_router import router

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(mode=0o700)
    monkeypatch.setenv("NEKO_RUNTIME_STATE_DIR", str(runtime_dir))
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_TRUSTED_HOSTS", "deployment.example")
    manager = SimpleNamespace(
        get_runtime_config_path=lambda filename: tmp_path / "config" / filename,
        save_json_config=lambda filename, config: atomic_write_json(tmp_path / "config" / filename, config),
    )
    monkeypatch.setattr(config_manager, "get_config_manager", lambda **_kwargs: manager)

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(InstanceAccessMiddleware)
    app.add_middleware(HostOriginGuardMiddleware)

    endpoint = "/api/runtime/remote-access"

    def desktop_headers(*, age=0):
        proof = secrets.token_hex(32)
        digest = hashlib.sha256(proof.encode("ascii")).hexdigest()
        proof_file = runtime_dir / f"remote-access-{digest}.token"
        proof_file.touch(mode=0o600, exist_ok=False)
        if age:
            timestamp = time.time() - age
            os.utime(proof_file, (timestamp, timestamp))
        return {"X-Neko-Remote-Access-Token": proof}

    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as desktop,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.0.2.4", 12345)),
            base_url="https://my-tunnel.cpolar.cn",
        ) as remote,
    ):
        assert (await desktop.get(endpoint)).status_code == 403
        assert (await desktop.get(endpoint, headers=desktop_headers(age=30))).status_code == 403
        assert (await desktop.get(endpoint, headers=desktop_headers(age=-30))).status_code == 403
        forwarded = await desktop.get(endpoint, headers={**desktop_headers(), "X-Forwarded-For": "192.0.2.4"})
        assert forwarded.status_code == 403
        headers = desktop_headers()
        initial = await desktop.get(endpoint, headers=headers)
        assert initial.json() == {"ok": True, "hosts": "", "managed_key": False}
        assert (await desktop.get(endpoint, headers=headers)).status_code == 403
        assert (await remote.get(endpoint)).status_code == 400

        invalid = await desktop.put(endpoint, headers=desktop_headers(), json={"hosts": "my-tunnel.cpolar.cn, *"})
        assert invalid.status_code == 400
        saved = await desktop.put(
            endpoint, headers=desktop_headers(), json={
                "hosts": "https://my-tunnel.cpolar.cn/chat, MY-TUNNEL.cpolar.cn",
            },
        )
        assert saved.json() == {"ok": True, "hosts": "my-tunnel.cpolar.cn", "managed_key": False}
        assert (await remote.get(endpoint)).json()["detail"] == "instance_authorization_required"
        assert (await remote.get("https://deployment.example" + endpoint)).status_code == 401

        copy = await desktop.post(endpoint, headers=desktop_headers(), json={"action": "copy"})
        assert "no-store" in copy.headers["Cache-Control"]
        key = copy.json()["key"]
        authorized = {**desktop_headers(), "Authorization": f"Bearer {key}"}
        assert (await remote.post(endpoint, headers=authorized, json={"action": "copy"})).status_code == 403

        rotated = await desktop.post(endpoint, headers=desktop_headers(), json={"action": "rotate"})
        assert rotated.json() == {"ok": True}
        assert (await remote.post(endpoint, headers=authorized, json={"action": "copy"})).status_code == 401
        new_key = (await desktop.post(endpoint, headers=desktop_headers(), json={"action": "copy"})).json()["key"]
        assert key != new_key

        monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", "deployment-instance-key-" + "x" * 32)
        assert (await desktop.get(endpoint, headers=desktop_headers())).json()["managed_key"] is True
        assert (await desktop.post(endpoint, headers=desktop_headers(), json={"action": "rotate"})).status_code == 409
        monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY")
        assert (await desktop.post(endpoint, headers=desktop_headers(), json={"action": "copy"})).json()["key"] == new_key
        assert (await desktop.put(endpoint, headers=desktop_headers(), json={"hosts": ""})).status_code == 200
        assert (await remote.get(endpoint)).status_code == 400

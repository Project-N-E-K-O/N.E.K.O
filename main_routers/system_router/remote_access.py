"""Manage remote access settings from the desktop on the same machine."""

import asyncio
import hashlib
import os
import re
import stat
import time

from fastapi import Request
from filelock import Timeout as FileLockTimeout

from main_routers.local_access import is_loopback_request
from utils.cloudsave_runtime import MaintenanceModeError
from utils.deployment import has_forwarding_metadata
from utils.host_origin_guard import save_trusted_hosts, saved_trusted_hosts
from utils.instance_access import instance_key, instance_key_is_configured, rotate_instance_key
from utils.single_instance import runtime_state_dir

from ._shared import _json_no_store_response, router


def _consume_desktop_proof(token: str) -> bool:
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        return False
    if os.name == "nt" and not any(
        os.environ.get(name) for name in ("NEKO_RUNTIME_STATE_DIR", "LOCALAPPDATA", "APPDATA")
    ):
        return False
    directory = runtime_state_dir()
    # Directory entries must not disclose the bearer token.
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    proof = directory / f"remote-access-{digest}.token"
    try:
        directory_info = directory.stat()
        info = proof.lstat()
        # Windows file timestamps can lead the current clock slightly.
        if not stat.S_ISREG(info.st_mode) or not -1 <= time.time() - info.st_mtime <= 20:
            return False
        if os.name == "posix" and (
            directory_info.st_uid != os.getuid() or directory_info.st_mode & 0o077
            or info.st_uid != os.getuid() or info.st_mode & 0o077
        ):
            return False
        # Only one request can remove the file. It carries no key or configuration data.
        proof.unlink()
        return True
    except OSError:
        return False


@router.get("/runtime/remote-access")
@router.put("/runtime/remote-access")
@router.post("/runtime/remote-access")
async def remote_access_settings(request: Request):
    if (
        request.url.hostname != "127.0.0.1" or not is_loopback_request(request)
        or has_forwarding_metadata(request.headers) or not await asyncio.to_thread(
            _consume_desktop_proof, request.headers.get("x-neko-remote-access-token", ""),
        )
    ):
        return _json_no_store_response({"ok": False, "error_code": "unavailable"}, 403)

    try:
        if request.method == "GET":
            return _json_no_store_response({
                "ok": True,
                "hosts": await asyncio.to_thread(saved_trusted_hosts),
                "managed_key": instance_key_is_configured(),
            })

        try:
            data = await request.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return _json_no_store_response({"ok": False, "error_code": "invalid_request"}, 400)

        if request.method == "PUT":
            hosts = data.get("hosts")
            if not isinstance(hosts, str) or len(hosts) > 2048:
                return _json_no_store_response({"ok": False, "error_code": "invalid_hosts"}, 400)
            try:
                saved = await asyncio.to_thread(save_trusted_hosts, hosts)
            except ValueError:
                return _json_no_store_response({"ok": False, "error_code": "invalid_hosts"}, 400)
            return _json_no_store_response({
                "ok": True, "hosts": saved, "managed_key": instance_key_is_configured(),
            })

        action = data.get("action")
        if action == "copy":
            key = await asyncio.to_thread(instance_key)
        elif action == "rotate":
            if instance_key_is_configured():
                return _json_no_store_response({"ok": False, "error_code": "managed_key"}, 409)
            await asyncio.to_thread(rotate_instance_key)
            return _json_no_store_response({"ok": True})
        else:
            return _json_no_store_response({"ok": False, "error_code": "invalid_request"}, 400)
        return _json_no_store_response({"ok": True, "key": key})
    except (OSError, ValueError, FileLockTimeout, MaintenanceModeError):
        return _json_no_store_response({"ok": False, "error_code": "operation_failed"}, 503)

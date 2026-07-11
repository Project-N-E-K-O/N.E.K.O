"""Bilibili login and credential actions for the runtime."""

from __future__ import annotations

from typing import Any

from ..adapters.bili_auth_service import BiliAuthService
from ..stores.credential_store import CredentialStore


def create_credential_store(plugin: Any, audit: Any) -> CredentialStore:
    return CredentialStore(plugin, audit)


def create_auth_service(runtime: Any, plugin: Any) -> BiliAuthService:
    return BiliAuthService(
        logger=getattr(plugin, "logger", None),
        credential_provider=runtime.credential_store.build_credential,
        credential_saver=runtime.credential_store.save,
        credential_reloader=runtime.reload_credential,
    )


async def reload_credential(runtime: Any) -> None:
    try:
        runtime.bili_credential = await runtime.credential_store.build_credential()
    except Exception:
        runtime.bili_credential = None


async def bili_login(runtime: Any) -> dict[str, Any]:
    return await runtime.bili_auth.login()


async def bili_login_check(runtime: Any) -> dict[str, Any]:
    return await runtime.bili_auth.login_check()


async def bili_login_status(runtime: Any) -> dict[str, Any]:
    return await runtime.bili_auth.check_credential()


async def bili_logout(runtime: Any) -> dict[str, Any]:
    removed = await runtime.credential_store.delete()
    runtime.bili_credential = None
    runtime.audit.record("bili_logout", "logged out (local credential removed)", detail={"files": removed})
    return {"logged_out": True, "removed": removed, "logged_in": False}

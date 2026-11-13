# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Atomic, process-locked OAuth storage. Never return tokens to settings clients."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from filelock import FileLock

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
PLAN_SCOPE = "chatgpt.tokens.use.direct"


class ChatGPTAuthError(RuntimeError):
    """Safe, actionable error text (never includes a token response)."""

    def __init__(self, message, *, status_code=401):
        super().__init__(message)
        self.status_code = status_code


def http_client():
    from utils.http_client import ensure_user_agent
    from utils.llm_client.lifecycle import _get_default_ssl_context

    return httpx.Client(
        timeout=30,
        follow_redirects=False,
        verify=_get_default_ssl_context(),
        headers=ensure_user_agent({}),
    )


def token_request(client, data):
    response = client.post(TOKEN_URL, data={**data, "resource": RESOURCE})
    if response.status_code in (400, 401, 403):
        raise ChatGPTAuthError(
            "ChatGPT authorization expired or was denied. Continue with ChatGPT again."
        )
    if response.status_code != 200:
        raise ChatGPTAuthError(
            "ChatGPT authorization is temporarily unavailable. Try again later.",
            status_code=response.status_code,
        )
    try:
        value = response.json()
        if not isinstance(value.get("access_token"), str) or not value["access_token"]:
            raise ValueError()
        if str(value.get("token_type", "")).lower() != "bearer":
            raise ValueError()
        expires = int(value["expires_in"])
        if expires <= 0:
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ChatGPTAuthError("ChatGPT returned an invalid token response.") from None
    return value


def merge_tokens(tokens, previous=None):
    value = dict(previous or {})
    value.update(
        {
            key: tokens[key]
            for key in ("access_token", "refresh_token", "id_token")
            if key in tokens
        }
    )
    value["expires_at"] = time.time() + int(tokens["expires_in"])
    value["scopes"] = (
        tokens["scope"].split() if "scope" in tokens else value.get("scopes", [])
    )
    if PLAN_SCOPE not in value["scopes"]:
        raise ChatGPTAuthError(
            "ChatGPT plan usage was not authorized. Sign in and allow plan usage."
        )
    if not value.get("refresh_token"):
        raise ChatGPTAuthError(
            "ChatGPT did not return a renewable session. Sign in again."
        )
    return value


class CredentialStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.path) + ".lock", timeout=45, mode=0o600)

    def _read(self):
        if not self.path.exists():
            return {
                "host_id": "urn:uuid:" + str(uuid.uuid4()),
                "accounts": {},
                "active": None,
            }
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value["accounts"], dict) or not value["host_id"]:
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError):
            raise ChatGPTAuthError(
                "ChatGPT credential storage is invalid. Restore it before signing in."
            ) from None

    def _write(self, value):
        fd, name = tempfile.mkstemp(prefix=".chatgpt-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def registration(self, account_id=None):
        with self.lock:
            value = self._read()
            self._write(
                value
            )  # Persist host ID before the first authorization attempt.
            account = value["accounts"].get(account_id) if account_id else None
            if account_id and account is None:
                raise ChatGPTAuthError("Unknown ChatGPT account.")
            return value["host_id"], dict(account or {})

    def save(self, client_id, claims, tokens, expected_subject=None):
        if expected_subject and claims["sub"] != expected_subject:
            raise ChatGPTAuthError(
                "ChatGPT returned a different account. Add it as a new account instead."
            )
        account = merge_tokens(tokens)
        account.update(
            client_id=client_id,
            subject=claims["sub"],
            email=claims.get("email", ""),
            issuer=ISSUER,
        )
        with self.lock:
            value = self._read()
            old = value["accounts"].get(client_id)
            if old and old["subject"] != claims["sub"]:
                raise ChatGPTAuthError(
                    "ChatGPT account identity does not match the saved registration."
                )
            value["accounts"][client_id] = account
            value["active"] = client_id
            self._write(value)

    def status(self):
        with self.lock:
            value = self._read()
            return {
                "active": value["active"],
                "accounts": [
                    {
                        "id": key,
                        "email": account.get("email", ""),
                        "connected": bool(account.get("refresh_token")),
                    }
                    for key, account in value["accounts"].items()
                ],
            }

    def select(self, account_id):
        with self.lock:
            value = self._read()
            account = value["accounts"].get(account_id, {})
            if not account.get("refresh_token"):
                raise ChatGPTAuthError("Sign in to this ChatGPT account first.")
            value["active"] = account_id
            self._write(value)

    def access_token(self):
        # Lock covers read, network refresh, and replacement across all Neko processes.
        with self.lock:
            value = self._read()
            account = value["accounts"].get(value["active"], {})
            if not account.get("refresh_token"):
                raise ChatGPTAuthError(
                    "Continue with ChatGPT in API settings before using this provider."
                )
            if PLAN_SCOPE not in account.get("scopes", []):
                raise ChatGPTAuthError(
                    "ChatGPT plan usage is not authorized. Sign in again."
                )
            if account.get("expires_at", 0) <= time.time() + 60:
                with http_client() as client:
                    tokens = token_request(
                        client,
                        {
                            "grant_type": "refresh_token",
                            "client_id": account["client_id"],
                            "refresh_token": account["refresh_token"],
                        },
                    )
                account = merge_tokens(tokens, account)
                value["accounts"][value["active"]] = account
                self._write(value)
            return account["access_token"]

    def sign_out(self, account_id):
        confirmed = False
        with self.lock:
            value = self._read()
            account = value["accounts"].get(account_id)
            if account is None:
                raise ChatGPTAuthError("Unknown ChatGPT account.")
            if account.get("refresh_token"):
                try:
                    with http_client() as client:
                        metadata = client.get(
                            ISSUER + "/.well-known/openid-configuration"
                        )
                        metadata.raise_for_status()
                        endpoint = metadata.json()["revocation_endpoint"]
                        from urllib.parse import urlsplit

                        parsed = urlsplit(endpoint)
                        if (parsed.scheme, parsed.netloc) != (
                            "https",
                            "auth.openai.com",
                        ):
                            raise ValueError("Invalid revocation endpoint")
                        for attempt in range(2):
                            try:
                                response = client.post(
                                    endpoint,
                                    data={
                                        "token": account["refresh_token"],
                                        "token_type_hint": "refresh_token",
                                        "client_id": account["client_id"],
                                    },
                                )
                                confirmed = response.status_code == 200
                                if confirmed or response.status_code < 500:
                                    break
                            except httpx.TransportError:
                                # Keep the rotating credential until the bounded
                                # retry finishes, including ambiguous timeouts.
                                pass
                            if attempt == 0:
                                time.sleep(0.5)
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
            for key in (
                "access_token",
                "refresh_token",
                "id_token",
                "expires_at",
                "scopes",
            ):
                account.pop(key, None)
            if value["active"] == account_id:
                value["active"] = None
            self._write(value)
        return confirmed


def get_store():
    from utils.config_manager import get_config_manager

    return CredentialStore(get_config_manager().local_state_dir / "chatgpt_oauth.json")

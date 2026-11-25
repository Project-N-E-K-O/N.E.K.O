# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""PKCE login with a short-lived IPv4 loopback callback and verified OIDC identity."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt

from .store import (
    ChatGPTAuthError,
    ISSUER,
    PLAN_SCOPE,
    RESOURCE,
    http_client,
    token_request,
)


def verify_identity(token, client_id, nonce, jwks):
    try:
        header = jwt.get_unverified_header(token)
        key = next(key for key in jwks["keys"] if key.get("kid") == header.get("kid"))
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(key).key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            options={"require": ["exp", "sub", "nonce", "iss", "aud"]},
        )
        if not isinstance(claims["sub"], str) or not claims["sub"].strip():
            raise ValueError()
        if not isinstance(claims["nonce"], str) or not secrets.compare_digest(
            claims["nonce"], nonce
        ):
            raise ValueError()
        return claims
    except (jwt.PyJWTError, ValueError, KeyError, StopIteration, TypeError):
        raise ChatGPTAuthError(
            "ChatGPT returned an invalid ID token. Sign in again."
        ) from None


class LoginAttempt:
    def __init__(self, store, account_id=None, timeout=300):
        self.store = store
        host, self.previous = store.registration(account_id)
        self.client_id = self.previous.get("client_id")
        self.state = secrets.token_urlsafe(32)
        self.nonce = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(48)
        self.done = threading.Event()
        self.status = "pending"
        self.error = ""
        self.deadline = time.monotonic() + timeout
        attempt = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(2)
                super().setup()

            def log_message(self, *_args):
                pass  # Callback URLs contain the one-time authorization code.

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path != "/auth/callback":
                    self.send_error(404)
                    return
                params = parse_qs(parsed.query)
                if any(len(values) != 1 for values in params.values()):
                    self.send_error(400)
                    return
                params = {key: values[0] for key, values in params.items()}
                if not secrets.compare_digest(params.get("state", ""), attempt.state):
                    self.send_error(400)
                    return
                if attempt.done.is_set() or time.monotonic() >= attempt.deadline:
                    self.send_error(410)
                    return
                try:
                    attempt.complete(params)
                    message = b"ChatGPT connected. You can close this tab and return to N.E.K.O."
                    status = 200
                except Exception as exc:
                    attempt.error = (
                        str(exc)
                        if isinstance(exc, ChatGPTAuthError)
                        else "ChatGPT sign-in failed. Try again."
                    )
                    attempt.status = "failed"
                    message = (
                        b"ChatGPT sign-in failed. Return to N.E.K.O. to try again."
                    )
                    status = 400
                attempt.done.set()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(message)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.timeout = 0.5
        self.redirect = f"http://127.0.0.1:{self.server.server_port}/auth/callback"
        params = {
            "client_id": self.client_id or "dynamic_agent_client",
            "ext_agent_host_id": host,
            "response_type": "code",
            "redirect_uri": self.redirect,
            "scope": "openid profile email offline_access resource.invoke "
            + PLAN_SCOPE,
            "resource": RESOURCE,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(
                hashlib.sha256(self.verifier.encode()).digest()
            )
            .rstrip(b"=")
            .decode(),
        }
        if not self.client_id:
            params["agent_name_hint"] = "Project N.E.K.O."
        elif self.previous.get("email"):
            params["login_hint"] = self.previous["email"]
        self.url = ISSUER + "/api/accounts/authorize?" + urlencode(params)
        threading.Thread(
            target=self._serve, daemon=True, name="neko-chatgpt-login"
        ).start()

    def _serve(self):
        try:
            while not self.done.is_set() and time.monotonic() < self.deadline:
                self.server.handle_request()
            if not self.done.is_set():
                self.status = "expired"
                self.done.set()
        finally:
            self.server.server_close()

    def complete(self, params):
        if params.get("error"):
            raise ChatGPTAuthError("ChatGPT sign-in was declined. You can try again.")
        issued = params.get("client_id") or self.client_id
        if not issued or issued == "dynamic_agent_client" or not params.get("code"):
            raise ChatGPTAuthError(
                "ChatGPT registration was incomplete. Sign in again."
            )
        if self.client_id and issued != self.client_id:
            raise ChatGPTAuthError("ChatGPT returned a different client registration.")
        with http_client() as client:
            tokens = token_request(
                client,
                {
                    "grant_type": "authorization_code",
                    "client_id": issued,
                    "code": params["code"],
                    "code_verifier": self.verifier,
                    "redirect_uri": self.redirect,
                },
            )
            response = client.get(ISSUER + "/.well-known/jwks.json")
            response.raise_for_status()
            claims = verify_identity(
                tokens.get("id_token", ""), issued, self.nonce, response.json()
            )
        self.store.save(issued, claims, tokens, self.previous.get("subject"))
        self.status = "connected"

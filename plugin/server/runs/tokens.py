from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from plugin.settings import RUN_TOKEN_SECRET, RUN_TOKEN_TTL_SECONDS


def _get_run_token_key() -> bytes:
    secret = RUN_TOKEN_SECRET
    if not isinstance(secret, str) or not secret.strip():
        raise RuntimeError("RUN_TOKEN_SECRET is not configured")
    return secret.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * ((4 - (len(value) % 4)) % 4)
    try:
        return base64.b64decode((value + pad).encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid token") from exc


def issue_run_token(*, run_id: str, perm: str = "read") -> tuple[str, int]:
    exp = int(time.time()) + int(RUN_TOKEN_TTL_SECONDS)
    payload: dict[str, object] = {
        "run_id": str(run_id),
        "exp": exp,
        "nonce": secrets.token_urlsafe(16),
        "perm": str(perm),
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = _b64url_encode(payload_raw)
    key = _get_run_token_key()
    sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    token = payload_b64 + "." + _b64url_encode(sig)
    return token, exp


def verify_run_token(token: str) -> tuple[str, str, int]:
    if not isinstance(token, str) or token.count(".") != 1:
        raise ValueError("invalid token")

    p1, p2 = token.split(".", 1)
    if not p1 or not p2:
        raise ValueError("invalid token")

    key = _get_run_token_key()
    expected = hmac.new(key, p1.encode("ascii"), hashlib.sha256).digest()
    got = _b64url_decode(p2)
    if not hmac.compare_digest(expected, got):
        raise ValueError("invalid token")

    payload_raw = _b64url_decode(p1)
    try:
        payload_obj = json.loads(payload_raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid token") from exc

    if not isinstance(payload_obj, dict):
        raise ValueError("invalid token")

    run_id_obj = payload_obj.get("run_id")
    perm_obj = payload_obj.get("perm")
    exp_obj = payload_obj.get("exp")

    if not isinstance(run_id_obj, str):
        raise ValueError("invalid token")
    run_id = run_id_obj.strip()
    if not run_id:
        raise ValueError("invalid token")

    perm = "read"
    if isinstance(perm_obj, str):
        perm_value = perm_obj.strip()
        if perm_value:
            perm = perm_value

    if isinstance(exp_obj, bool):
        raise ValueError("invalid token")

    exp: int
    if isinstance(exp_obj, int):
        exp = exp_obj
    elif isinstance(exp_obj, str):
        try:
            exp = int(exp_obj)
        except ValueError as exc:
            raise ValueError("invalid token") from exc
    else:
        raise ValueError("invalid token")

    if int(time.time()) > exp:
        raise ValueError("expired")

    return run_id, perm, exp

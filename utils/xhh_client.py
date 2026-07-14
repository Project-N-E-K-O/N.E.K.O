# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Shared Xiaoheihe request signing and credential helpers."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any


_XHH_SIGNING_KEY = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
_XHH_TOKEN_PHRASES = ("唉？！云朵！", "哒哒哒哒哒，好想玩原神", "云！原！神！")


def _vm(num: int) -> int:
    return (255 & ((num << 1) ^ 27)) if num & 128 else num << 1


def _qm(num: int) -> int:
    return _vm(num) ^ num


def _mm(num: int) -> int:
    return _qm(_vm(num))


def _ym(num: int) -> int:
    return _mm(_qm(_vm(num)))


def _gm(num: int) -> int:
    return _ym(num) ^ _mm(num) ^ _qm(num)


def _mixed(values: list[int]) -> list[int]:
    return [
        _gm(values[0]) ^ _ym(values[1]) ^ _mm(values[2]) ^ _qm(values[3]),
        _qm(values[0]) ^ _gm(values[1]) ^ _ym(values[2]) ^ _mm(values[3]),
        _mm(values[0]) ^ _qm(values[1]) ^ _gm(values[2]) ^ _ym(values[3]),
        _ym(values[0]) ^ _mm(values[1]) ^ _qm(values[2]) ^ _gm(values[3]),
        values[4],
        values[5],
    ]


def _av(value: str, key: str, n: int) -> str:
    pool = key[: len(key) + n]
    return "".join(pool[ord(char) % len(pool)] for char in value)


def _sv(value: str, key: str) -> str:
    return "".join(key[ord(char) % len(key)] for char in value)


def _interleave(values: list[str]) -> str:
    output: list[str] = []
    for index in range(len(values[2])):
        for value in values:
            if index < len(value):
                output.append(value[index])
    return "".join(output)


def build_xhh_request_keys(
    path: str,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> tuple[str, str, int]:
    """Build Xiaoheihe's hkey, nonce and request timestamp."""
    request_time = int(timestamp or time.time())
    request_nonce = nonce or hashlib.md5(
        f"{request_time}{secrets.randbelow(max(2, int(time.time() * 1000)))}".encode()
    ).hexdigest().upper()
    values = [
        _av(str(request_time), _XHH_SIGNING_KEY, -2),
        _sv(path, _XHH_SIGNING_KEY),
        _sv(request_nonce, _XHH_SIGNING_KEY),
    ]
    values.sort(key=len)
    digest = hashlib.md5(_interleave(values).encode()[:20]).hexdigest()
    checksum = sum(_mixed([ord(char) for char in digest[-6:]])) % 100
    return f"{_av(digest[:5], _XHH_SIGNING_KEY, -4)}{checksum:02d}", request_nonce, request_time


def build_xhh_token_id(*, timestamp: int | None = None) -> str:
    """Build the short-lived browser token used by Xiaoheihe requests."""
    current = int(timestamp or time.time())
    raw = bytearray(hashlib.md5(str(current).encode()).digest())
    for phrase in _XHH_TOKEN_PHRASES:
        raw.extend(hashlib.md5(phrase.encode()).digest())
    raw.append(0)
    return base64.b64encode(bytes(raw)).decode("ascii")


def build_xhh_request_params(
    path: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hkey, nonce, request_time = build_xhh_request_keys(path)
    params: dict[str, Any] = dict(extra or {})
    params.update(
        {
            "os_type": "web",
            "app": "web",
            "client_type": "web",
            "version": "999.0.4",
            "web_version": "2.5",
            "x_client_type": "web",
            "x_app": "heybox_website",
            "x_os_type": "Windows",
            "device_info": "Chrome",
            "hkey": hkey,
            "_time": str(request_time),
            "nonce": nonce,
            "_notip": "true",
        }
    )
    return params


def build_xhh_cookie_header(cookies: dict[str, str]) -> str:
    normalized = {
        str(key).strip(): str(value).strip()
        for key, value in (cookies or {}).items()
        if str(key).strip() and str(value).strip()
    }
    normalized["x_xhh_tokenid"] = build_xhh_token_id()
    return "; ".join(f"{key}={value}" for key, value in normalized.items())


__all__ = [
    "build_xhh_cookie_header",
    "build_xhh_request_keys",
    "build_xhh_request_params",
    "build_xhh_token_id",
]

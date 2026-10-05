# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Identity ticket verification for catgirl visits (design §4.7, OD-01 v2).

Ticket wire format
------------------
``base64url(claims_json) + '.' + base64url(sig_64B)`` -- two segments, not a
three-segment JWT. Padding is optional on input and never emitted.

**Signed bytes.** §4.7 fixes the encoding but does not spell out which bytes
the Ed25519 signature covers; this module fixes it as *the ASCII bytes of the
first segment exactly as transmitted* (the JWS "signing input" convention),
i.e. ``sig = Ed25519.sign(segment0.encode('ascii'))``. Signing the encoded
segment instead of the decoded JSON means no canonicalisation is needed and
alternate base64 spellings of the same JSON never verify. Servers and
``scripts/visit_dev_mint.py`` must sign exactly this; :func:`mint_ticket` is the
reference implementation (used by tests and the dev loopback).

Verification order (fixed, §4.2 hello / §5 PR-06)
-------------------------------------------------
1. ``pubkeys`` stale (cache expired and refresh failed) -> :class:`PubkeysStale`
2. ``kid in pubkeys.revoked`` -> :class:`RevokedKid` (even when the built-in
   table has the kid)
3. signature, ``kid`` looked up in ``pubkeys.keys`` (miss -> :class:`UnknownKid`,
   bad signature -> :class:`BadSignature`)
4. key validity window: ``not_before <= iat <= not_after`` and
   ``now <= not_after + VISIT_HOST_CREDENTIAL_TTL_S + tolerance``
   -> :class:`KeyOutOfWindow`
5. ``v`` / ``iss`` / ``aud`` / ``transport`` / ``visit_id`` / ``role``
   -> :class:`ClaimMismatch`; ``iat - tol <= now <= exp + tol``
   -> :class:`TicketTimeInvalid`
6. ``vid == expect_vid`` (vendor-stamped sender id) -> :class:`VidMismatch`
7. ``sub not in blocklist`` -> :class:`PeerBlocked`
8. ``jti`` window: the same ``jti`` may be replayed only by the same room and
   ``vid`` -> :class:`JtiReplay`; recorded only when every check passed.

Nothing is read from the blocklist before the signature verified. There is no
"skip verification" branch: the dev loopback only adds one more public key
under ``VISIT_DEV_KID``.

Pure logic: no network, no event loop. Fetching ``GET /api/visit/pubkeys`` is
the caller's job; this module only parses / merges / ages the result.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from config.visit_settings import (
    NEKO_VISIT_DEV_KEYFILE,
    VISIT_CREDENTIAL_TTL_S,
    VISIT_DEV_KID,
    VISIT_HOST_CREDENTIAL_TTL_S,
    VISIT_PUBKEYS_CACHE_S,
    VISIT_SERVERS_PUBKEYS,
    VISIT_TICKET_AUD,
    VISIT_TICKET_CLOCK_TOLERANCE_S,
    VISIT_TICKET_ISS,
    VISIT_TICKET_VERSION,
)
from main_logic.visit.limits import BlocklistUnavailable as _LimitsBlocklistUnavailable
from utils.logger_config import get_module_logger

if TYPE_CHECKING:  # pragma: no cover
    from main_logic.visit.limits import Blocklist

logger = get_module_logger(__name__, "Main")

# 票据整体长度上限：claims ≈560 B，base64 后 ≈750 + 签名 86；留足余量但挡住超大输入。
_TICKET_MAX_CHARS = 4096
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ROLES = frozenset({"host", "guest"})
_TRANSPORTS = frozenset({"trtc", "livekit"})
# 开发键没有轮换窗口：not_before=0、not_after=2^53-1。
_DEV_KEY_NOT_BEFORE = 0
_DEV_KEY_NOT_AFTER = 2 ** 53 - 1
# claims 字符串字段长度上限（§4.7 表；sub/vid/jti/char_tag 给宽一点，只防畸形输入）。
_CLAIM_STR_MAX = {
    "kid": 16,
    "sub": 64,
    "vid": 64,
    "visit_id": 64,
    "char_tag": 64,
    "jti": 64,
}
_DISPLAY_NAME_RAW_MAX = 256

FINALIZE_IDENTITY_REJECTED = "peer_identity_rejected"
"""Finalize reason for every ticket rejection except a blocklist hit."""

FINALIZE_PEER_BLOCKED = "peer_blocked"
"""Finalize reason for a blocklist hit (the peer only sees a plain leave)."""


# ── 异常层级 ───────────────────────────────────────────────────────────


class TicketRejected(Exception):
    """Base class of every identity-ticket rejection.

    ``code`` is a short machine-readable reason (never contains ticket text or
    display names, safe to log); ``finalize_reason`` is what the runtime maps
    the rejection to (``peer_identity_rejected`` or ``peer_blocked``).
    """

    code = "rejected"
    finalize_reason = FINALIZE_IDENTITY_REJECTED

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class PubkeysStale(TicketRejected):
    """The pubkey cache expired and could not be refreshed (fail closed)."""

    code = "pubkeys_stale"


class MalformedTicket(TicketRejected):
    """The ticket does not parse (segments, base64url, JSON, claim types)."""

    code = "malformed"


class RevokedKid(TicketRejected):
    """The signing ``kid`` is on the fetched revocation list."""

    code = "revoked_kid"


class UnknownKid(TicketRejected):
    """The signing ``kid`` is in neither the built-in nor the fetched table."""

    code = "unknown_kid"


class BadSignature(TicketRejected):
    """The Ed25519 signature does not verify."""

    code = "bad_signature"


class KeyOutOfWindow(TicketRejected):
    """The ticket was signed, or is used, outside the key's validity window."""

    code = "key_out_of_window"


class ClaimMismatch(TicketRejected):
    """A fixed or room-bound claim (v/iss/aud/transport/visit_id/role) differs.

    ``claim`` names the offending claim.
    """

    code = "claim_mismatch"

    def __init__(self, claim: str) -> None:
        self.claim = claim
        super().__init__(claim)


class TicketTimeInvalid(TicketRejected):
    """``now`` is outside ``[iat - tolerance, exp + tolerance]``."""

    code = "ticket_time"


class VidMismatch(TicketRejected):
    """The ticket ``vid`` differs from the vendor-stamped sender id."""

    code = "vid_mismatch"


class PeerBlocked(TicketRejected):
    """The ticket ``sub`` is on the local blocklist."""

    code = "peer_blocked"
    finalize_reason = FINALIZE_PEER_BLOCKED


class BlocklistUnavailable(TicketRejected, _LimitsBlocklistUnavailable):
    """The local blocklist could not be read, so no peer can be admitted (fail closed).

    Also a :class:`main_logic.visit.limits.BlocklistUnavailable`, so a caller
    catching either name catches it; :func:`verify_identity_ticket` turns the
    limits error raised by ``Blocklist.is_blocked`` into this one.
    """

    code = "blocklist_unavailable"


class JtiReplay(TicketRejected):
    """The ``jti`` was already used by another room or another ``vid``."""

    code = "jti_replay"


# ── base64url ──────────────────────────────────────────────────────────


def b64url_encode(data: bytes) -> str:
    """Encode bytes as unpadded base64url text."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Strictly decode unpadded (or padded) base64url text.

    Raises ``ValueError`` on any character outside the base64url alphabet.
    """
    if not isinstance(text, str):
        raise ValueError("not a string")
    stripped = text.rstrip("=")
    if not stripped or not _B64URL_RE.match(stripped):
        raise ValueError("invalid base64url")
    padded = stripped + "=" * (-len(stripped) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64url") from exc


# ── 公钥集合 ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PubkeyEntry:
    """One Ed25519 verification key with its validity window.

    ``not_before`` / ``not_after`` bound the ticket ``iat`` (unix seconds).
    ``source`` is ``'builtin'``, ``'fetched'`` or ``'dev'`` (diagnostics only).
    """

    kid: str
    public_key: Ed25519PublicKey
    not_before: int
    not_after: int
    source: str = "builtin"

    def raw_bytes(self) -> bytes:
        """Return the 32-byte raw public key."""
        return self.public_key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )


@dataclass(frozen=True)
class FetchedPubkeys:
    """Parsed result of one successful ``GET /api/visit/pubkeys``.

    ``fetched_at`` is the wall-clock time of the successful fetch; the cache is
    usable until ``fetched_at + min(ttl_s, VISIT_PUBKEYS_CACHE_S)``.
    """

    keys: Mapping[str, PubkeyEntry]
    revoked: frozenset[str]
    fetched_at: float
    ttl_s: float = VISIT_PUBKEYS_CACHE_S


def _require_int(value: Any, what: str) -> int:
    # bool 是 int 子类：True 不能当 1 通过。
    if type(value) is not int:
        raise ValueError(f"{what} must be an integer")
    return value


def _public_key_from_b64url(pub: Any) -> Ed25519PublicKey:
    raw = b64url_decode(pub)
    if len(raw) != 32:
        raise ValueError("public key must be 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def _entry_from_mapping(kid: Any, spec: Any, *, source: str) -> PubkeyEntry:
    if not isinstance(kid, str) or not kid or len(kid) > _CLAIM_STR_MAX["kid"]:
        raise ValueError("invalid kid")
    if not isinstance(spec, Mapping):
        raise ValueError("key spec must be an object")
    alg = spec.get("alg", "Ed25519")
    if alg != "Ed25519":
        raise ValueError("unsupported alg")
    not_before = _require_int(spec.get("not_before"), "not_before")
    not_after = _require_int(spec.get("not_after"), "not_after")
    if not_after < not_before:
        raise ValueError("not_after precedes not_before")
    return PubkeyEntry(
        kid=kid,
        public_key=_public_key_from_b64url(spec.get("pub")),
        not_before=not_before,
        not_after=not_after,
        source=source,
    )


def parse_pubkeys_response(payload: Any, *, fetched_at: float) -> FetchedPubkeys:
    """Validate a ``GET /api/visit/pubkeys`` body into :class:`FetchedPubkeys`.

    Expected shape: ``{keys:[{kid, alg:'Ed25519', pub, not_before, not_after}],
    revoked:[kid], ttl_s}``. ``not_before`` / ``not_after`` are mandatory. A
    malformed key entry is skipped (logged by kid only) and its kid is added
    to ``revoked``, so a same-named built-in key cannot stay usable; a malformed envelope, a missing ``revoked`` list or
    any malformed revocation entry raises ``ValueError`` so the caller
    treats the refresh as failed. A key published under the reserved dev kid
    is ignored.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("pubkeys response must be an object")
    raw_keys = payload.get("keys")
    raw_revoked = payload.get("revoked")
    if not isinstance(raw_keys, list) or not isinstance(raw_revoked, list):
        raise ValueError("pubkeys response keys/revoked must be lists")
    # 吊销名单不完整就等于不知道最新吊销状态：缺字段或有坏条目都按刷新失败处理，
    # 否则一把已吊销的内置钥匙会继续放行到缓存过期
    if not all(isinstance(k, str) and k for k in raw_revoked):
        raise ValueError("pubkeys response revoked entries must be non-empty kid strings")
    revoked = set(raw_revoked)
    keys: dict[str, PubkeyEntry] = {}
    seen: set[str] = set()
    for item in raw_keys:
        kid = item.get("kid") if isinstance(item, Mapping) else None
        if isinstance(kid, str) and kid:
            if kid in seen:
                # 同一个 kid 出现两次：哪条生效取决于顺序，两条可能是不同的钥匙或窗口。
                # 与「内置 / 拉取冲突」一样 fail closed：这个 kid 整体吊销
                logger.warning("visit pubkeys: duplicate fetched kid %r, revoking it", kid)
                revoked.add(kid)
                keys.pop(kid, None)
                continue
            seen.add(kid)
        if kid == VISIT_DEV_KID:
            logger.warning("visit pubkeys: ignoring fetched key under reserved dev kid")
            continue
        try:
            entry = _entry_from_mapping(kid, item, source="fetched")
        except ValueError as exc:
            logger.warning("visit pubkeys: skipping malformed fetched key %r: %s", kid, exc)
            # 坏条目按吊销处理：只从拉取结果里跳过的话，内置表里同名的旧钥匙仍会被放行
            if isinstance(kid, str) and kid:
                revoked.add(kid)
            continue
        keys[entry.kid] = entry
    ttl_raw = payload.get("ttl_s", VISIT_PUBKEYS_CACHE_S)
    ttl_s = math.nan
    if not isinstance(ttl_raw, bool) and isinstance(ttl_raw, (int, float)):
        try:
            ttl_s = float(ttl_raw)
        except OverflowError:  # 超出 float 范围的 JSON 整数（如 10**400）：与其它坏值一样回落默认
            pass
    if not math.isfinite(ttl_s) or ttl_s < 0:
        ttl_s = float(VISIT_PUBKEYS_CACHE_S)
    return FetchedPubkeys(
        keys=keys, revoked=frozenset(revoked), fetched_at=float(fetched_at), ttl_s=ttl_s,
    )


def load_dev_public_key(path: str | Path) -> Ed25519PublicKey:
    """Read the dev-loopback Ed25519 private key file and return its public key.

    Accepted formats: PEM (PKCS#8, unencrypted), raw 32-byte seed, or a text
    file holding the 32-byte seed as base64url or hex. Synchronous file read:
    call it off the event loop (``asyncio.to_thread``) or at startup.
    """
    data = Path(path).read_bytes()
    return _private_key_from_bytes(data).public_key()


def _private_key_from_bytes(data: bytes) -> Ed25519PrivateKey:
    stripped = data.strip()
    if stripped.startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(stripped, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("dev keyfile is not an Ed25519 key")
        return key
    if len(data) == 32:
        return Ed25519PrivateKey.from_private_bytes(data)
    text = stripped.decode("ascii", errors="strict")
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(text))
    seed = b64url_decode(text)
    if len(seed) != 32:
        raise ValueError("dev keyfile seed must be 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


@dataclass(frozen=True)
class PubkeySet:
    """Snapshot of the verification keys plus the revocation list.

    ``stale`` is True when no fresh fetch backs this snapshot (never fetched,
    or the cache expired and the refresh failed): verification then fails
    closed because the latest revocation list is unknown. ``expires_at`` lets
    a long-lived snapshot go stale by itself while a visit is running.
    """

    keys: Mapping[str, PubkeyEntry]
    revoked: frozenset[str] = frozenset()
    stale: bool = False
    expires_at: float | None = None

    def is_stale_at(self, now: float) -> bool:
        """Return True when the snapshot must not be trusted at ``now``."""
        return self.stale or (self.expires_at is not None and now > self.expires_at)

    @classmethod
    def build(
        cls,
        *,
        now: float,
        fetched: FetchedPubkeys | None,
        builtin: Mapping[str, Mapping[str, Any]] | None = None,
        dev_public_key: Ed25519PublicKey | None = None,
        cache_s: float = VISIT_PUBKEYS_CACHE_S,
    ) -> "PubkeySet":
        """Merge the built-in table, the last fetch and the dev key.

        * ``builtin`` defaults to ``config.visit_settings.VISIT_SERVERS_PUBKEYS``.
        * A kid present in both built-in and fetched tables with *different*
          key bytes is dropped (fail closed); with equal bytes the stricter
          (intersected) validity window wins.
        * The reserved ``VISIT_DEV_KID`` is only ever taken from
          ``dev_public_key`` (see :func:`load_dev_public_key`), never from the
          built-in or fetched tables.
        * ``revoked`` comes from the fetch only; it overrides every source.
        * ``stale`` = never fetched, or ``now`` is past
          ``fetched_at + min(ttl_s, cache_s)``.
        """
        table = VISIT_SERVERS_PUBKEYS if builtin is None else builtin
        merged: dict[str, PubkeyEntry] = {}
        for kid, spec in table.items():
            if kid == VISIT_DEV_KID:
                logger.warning("visit pubkeys: built-in table must not use the dev kid")
                continue
            try:
                merged[kid] = _entry_from_mapping(kid, spec, source="builtin")
            except ValueError as exc:
                logger.warning("visit pubkeys: skipping malformed built-in key %r: %s", kid, exc)

        conflicted: set[str] = set()
        if fetched is not None:
            for kid, entry in fetched.keys.items():
                if kid == VISIT_DEV_KID:
                    continue
                prior = merged.get(kid)
                if prior is None:
                    merged[kid] = entry
                    continue
                if prior.raw_bytes() != entry.raw_bytes():
                    logger.warning("visit pubkeys: kid %r differs between built-in and fetched; dropping", kid)
                    conflicted.add(kid)
                    continue
                merged[kid] = PubkeyEntry(
                    kid=kid,
                    public_key=prior.public_key,
                    not_before=max(prior.not_before, entry.not_before),
                    not_after=min(prior.not_after, entry.not_after),
                    source="builtin+fetched",
                )
        for kid in conflicted:
            merged.pop(kid, None)

        if dev_public_key is not None:
            merged[VISIT_DEV_KID] = PubkeyEntry(
                kid=VISIT_DEV_KID,
                public_key=dev_public_key,
                not_before=_DEV_KEY_NOT_BEFORE,
                not_after=_DEV_KEY_NOT_AFTER,
                source="dev",
            )

        if fetched is None:
            return cls(keys=merged, revoked=frozenset(), stale=True, expires_at=None)
        expires_at = fetched.fetched_at + min(fetched.ttl_s, cache_s)
        return cls(
            keys=merged,
            revoked=frozenset(fetched.revoked),
            stale=now > expires_at,
            expires_at=expires_at,
        )

    @classmethod
    def from_runtime(cls, *, now: float, fetched: FetchedPubkeys | None) -> "PubkeySet":
        """Production entry: built-in table + fetch + dev key from ``NEKO_VISIT_DEV_KEYFILE``.

        Reads the dev keyfile synchronously when the env var is set; call it
        off the event loop.
        """
        dev_key = None
        if NEKO_VISIT_DEV_KEYFILE:
            try:
                dev_key = load_dev_public_key(NEKO_VISIT_DEV_KEYFILE)
            except (OSError, ValueError, TypeError, UnsupportedAlgorithm) as exc:
                # 加密的 PKCS#8 PEM 在 password=None 时抛 TypeError：同样按「不可用」跳过，
                # 不能让身份初始化整个失败
                logger.warning("visit pubkeys: dev keyfile unusable: %s", type(exc).__name__)
        return cls.build(now=now, fetched=fetched, dev_public_key=dev_key)


# ── jti 窗口 ───────────────────────────────────────────────────────────


@dataclass
class JtiWindow:
    """Per-process memory of accepted ``jti`` values.

    A ``jti`` may be presented again only with the same ``(visit_id, vid)``
    (same room, same vendor sender: a reconnect replaying its ticket); any
    other binding is a replay and is refused. Oldest entries are evicted past
    ``max_entries``.
    """

    max_entries: int = 256
    _seen: dict[str, tuple[str, str]] = field(default_factory=dict)

    def check(self, jti: str, *, visit_id: str, vid: str) -> bool:
        """Return True when ``jti`` is unseen or bound to the same room and vid."""
        bound = self._seen.get(jti)
        return bound is None or bound == (visit_id, vid)

    def record(self, jti: str, *, visit_id: str, vid: str) -> None:
        """Remember ``jti`` as bound to ``(visit_id, vid)``."""
        self._seen.pop(jti, None)
        self._seen[jti] = (visit_id, vid)
        while len(self._seen) > self.max_entries:
            self._seen.pop(next(iter(self._seen)))

    def __len__(self) -> int:
        return len(self._seen)


# ── claims ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TicketClaims:
    """Verified identity-ticket claims (§4.7 table).

    ``display_name`` is the peer's self-reported, *unsanitised* name: run it
    through ``sanitize.neutralize_display_name`` before any UI / roster use.
    """

    v: int
    iss: str
    aud: str
    kid: str
    sub: str
    vid: str
    visit_id: str
    role: str
    transport: str
    char_tag: str
    iat: int
    exp: int
    jti: str
    display_name: str | None = None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate claim")
        out[key] = value
    return out


def _split_ticket(ticket: Any) -> tuple[str, dict[str, Any], bytes]:
    if not isinstance(ticket, str) or not ticket or len(ticket) > _TICKET_MAX_CHARS:
        raise MalformedTicket("ticket must be a short string")
    parts = ticket.split(".")
    if len(parts) != 2:
        raise MalformedTicket("ticket must have two segments")
    seg_claims, seg_sig = parts
    try:
        claims_bytes = b64url_decode(seg_claims)
        sig = b64url_decode(seg_sig)
        claims = json.loads(
            claims_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
        )
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        # 深层嵌套（"[[[[…"）让 json.loads 抛 RecursionError：同样是一张坏票，
        # 必须按 TicketRejected 走拒绝流程，不能冲出核验函数
        raise MalformedTicket("undecodable segment") from exc
    if not isinstance(claims, dict):
        raise MalformedTicket("claims must be an object")
    if len(sig) != 64:
        raise MalformedTicket("signature must be 64 bytes")
    return seg_claims, claims, sig


def _claims_from_dict(raw: Mapping[str, Any]) -> TicketClaims:
    try:
        values: dict[str, Any] = {}
        for name in ("iss", "aud", "kid", "sub", "vid", "visit_id", "role", "transport", "char_tag", "jti"):
            val = raw.get(name)
            if not isinstance(val, str) or not val:
                raise ValueError(f"{name} must be a non-empty string")
            limit = _CLAIM_STR_MAX.get(name)
            if limit is not None and len(val) > limit:
                raise ValueError(f"{name} too long")
            values[name] = val
        values["v"] = _require_int(raw.get("v"), "v")
        values["iat"] = _require_int(raw.get("iat"), "iat")
        values["exp"] = _require_int(raw.get("exp"), "exp")
        display_name = raw.get("display_name")
        if display_name is not None and (
            not isinstance(display_name, str) or len(display_name) > _DISPLAY_NAME_RAW_MAX
        ):
            raise ValueError("display_name must be a short string")
        values["display_name"] = display_name
    except ValueError as exc:
        raise MalformedTicket(str(exc)) from exc
    return TicketClaims(**values)


_ROLE_TICKET_TTL_S = {"guest": VISIT_CREDENTIAL_TTL_S, "host": VISIT_HOST_CREDENTIAL_TTL_S}


def peek_ticket_claims(ticket: str) -> TicketClaims:
    """Parse the claims of a ticket WITHOUT verifying its signature.

    Only for local consistency checks on a ticket this side just received
    from Servers for itself (the credentials client compares it with the
    rest of the response). Never use the result to trust a peer: that is
    :func:`verify_identity_ticket`. Raises :class:`MalformedTicket`.
    """
    _seg, raw, _sig = _split_ticket(ticket)
    return _claims_from_dict(raw)


def ticket_ttl_s(role: str) -> int:
    """Identity ticket lifetime (``exp - iat``) Servers issues for ``role``."""
    return _ROLE_TICKET_TTL_S[role]


def verify_identity_ticket(
    ticket: str,
    *,
    expect_visit_id: str,
    expect_role: str,
    expect_vid: str,
    expect_transport: str,
    now: float,
    pubkeys: PubkeySet,
    blocklist: "Blocklist",
    jti_window: JtiWindow,
) -> TicketClaims:
    """Verify a peer identity ticket in the fixed order and return its claims.

    ``expect_role`` is the role the *peer* ticket must claim, i.e. the
    complement of the local side (a host passes ``'guest'``).
    ``expect_vid`` is the vendor-stamped sender id of the ``hello`` (TRTC
    ``CUSTOM_MESSAGE.userId`` / LiveKit ``participant.identity``).
    ``blocklist`` must already be loaded; only its synchronous
    ``is_blocked`` is used, and only after the signature verified.
    Raises a :class:`TicketRejected` subclass on any failure; the ``jti`` is
    recorded in ``jti_window`` only when every check passed.
    """
    tol = VISIT_TICKET_CLOCK_TOLERANCE_S

    # 1. 公钥表过期且刷新失败 → 不知道最新吊销名单，一律拒。
    if pubkeys.is_stale_at(now):
        raise PubkeysStale()

    seg_claims, raw, sig = _split_ticket(ticket)
    kid = raw.get("kid")
    if not isinstance(kid, str) or not kid:
        raise MalformedTicket("kid missing")

    # 2. 吊销先于查表：内置表命中也不放行。
    if kid in pubkeys.revoked:
        raise RevokedKid()

    # 3. 验签（签名覆盖第一段 base64url 文本的 ASCII 字节）。
    entry = pubkeys.keys.get(kid)
    if entry is None:
        raise UnknownKid()
    try:
        entry.public_key.verify(sig, seg_claims.encode("ascii"))
    except InvalidSignature as exc:
        raise BadSignature() from exc

    claims = _claims_from_dict(raw)

    # 4. 该 kid 的有效期：票必须在钥匙有效期内签出；钥匙下线后最多再认一张最长票的寿命。
    if not (entry.not_before <= claims.iat <= entry.not_after):
        raise KeyOutOfWindow("iat outside key window")
    if now > entry.not_after + VISIT_HOST_CREDENTIAL_TTL_S + tol:
        raise KeyOutOfWindow("key retired")

    # 5. 固定 claims 与本房绑定。
    if claims.v != VISIT_TICKET_VERSION:
        raise ClaimMismatch("v")
    if claims.iss != VISIT_TICKET_ISS:
        raise ClaimMismatch("iss")
    if claims.aud != VISIT_TICKET_AUD:
        raise ClaimMismatch("aud")
    if claims.transport not in _TRANSPORTS or claims.transport != expect_transport:
        raise ClaimMismatch("transport")
    if claims.visit_id != expect_visit_id:
        raise ClaimMismatch("visit_id")
    if claims.role not in _ROLES or claims.role != expect_role:
        raise ClaimMismatch("role")
    if claims.exp < claims.iat:
        raise TicketTimeInvalid("exp precedes iat")
    # 寿命按角色封顶（guest 40 / host 50 min）：签发方即便给出超长 exp，也不能让
    # 同一张票被反复用到协议承诺之外
    if claims.exp - claims.iat > _ROLE_TICKET_TTL_S[claims.role]:
        raise TicketTimeInvalid("ticket lifetime exceeds the role limit")
    if not (claims.iat - tol <= now <= claims.exp + tol):
        raise TicketTimeInvalid()

    # 6. vendor 盖章的发送者 id。
    if claims.vid != expect_vid:
        raise VidMismatch()

    # 7. 本机黑名单（只在验签通过后才读）。读不出来时一律拒：当空表会放进被拉黑的人。
    if not getattr(blocklist, "available", True):
        raise BlocklistUnavailable()
    try:
        blocked = blocklist.is_blocked(claims.sub)
    except _LimitsBlocklistUnavailable as exc:
        # 黑名单类自己报不可用（例如没有 available 标志的实现）：同样是一次票据拒绝
        raise BlocklistUnavailable() from exc
    if blocked:
        raise PeerBlocked()

    # 8. jti：同房同 vid 才允许重放。
    if not jti_window.check(claims.jti, visit_id=claims.visit_id, vid=claims.vid):
        raise JtiReplay()
    jti_window.record(claims.jti, visit_id=claims.visit_id, vid=claims.vid)
    return claims


def mint_ticket(claims: Mapping[str, Any], private_key: Ed25519PrivateKey) -> str:
    """Encode and sign ``claims`` in the wire format this module verifies.

    Reference implementation for tests and the dev loopback; production
    tickets are minted by Servers with the same signing input.
    """
    seg_claims = b64url_encode(
        json.dumps(dict(claims), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    sig = private_key.sign(seg_claims.encode("ascii"))
    return f"{seg_claims}.{b64url_encode(sig)}"


__all__ = [
    "BlocklistUnavailable",
    "FINALIZE_IDENTITY_REJECTED",
    "FINALIZE_PEER_BLOCKED",
    "BadSignature",
    "ClaimMismatch",
    "FetchedPubkeys",
    "JtiReplay",
    "JtiWindow",
    "KeyOutOfWindow",
    "MalformedTicket",
    "PeerBlocked",
    "PubkeyEntry",
    "PubkeySet",
    "PubkeysStale",
    "RevokedKid",
    "TicketClaims",
    "TicketRejected",
    "TicketTimeInvalid",
    "UnknownKid",
    "VidMismatch",
    "b64url_decode",
    "b64url_encode",
    "load_dev_public_key",
    "mint_ticket",
    "parse_pubkeys_response",
    "peek_ticket_claims",
    "ticket_ttl_s",
    "verify_identity_ticket",
]

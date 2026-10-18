#!/usr/bin/env python3
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

"""Dev loopback for catgirl visits: sign identity tickets with a local Ed25519 key.

The production verifier (``main_logic.visit.identity.verify_identity_ticket``)
has no "skip verification" branch. The dev loopback only adds one more public
key: when ``NEKO_VISIT_DEV_KEYFILE`` points at a private key file, the backend
puts that key's public half into its key set under the reserved dev kid
(``config.visit_settings.VISIT_DEV_KID``). This script signs tickets with the
same private key through the reference signer ``identity.mint_ticket``, so a
ticket printed here is checked by exactly the code path a Servers ticket is.

Sub-commands::

    keygen --out PATH [--force]     write a new unencrypted PKCS#8 PEM key
    pubkey [--keyfile PATH]         print the dev kid and base64url public key
    mint --role guest|host [...]    sign one ticket and print it with its claims

``--keyfile`` defaults to ``NEKO_VISIT_DEV_KEYFILE``. Output is one JSON
object on stdout (``mint --ticket-only`` prints the bare ticket). The dev key
never signs anything Servers would accept: Servers keys live under their own
kids and a fetched key under the dev kid is ignored by the client.

Run::

    uv run python scripts/visit_dev_mint.py keygen --out <path>
    uv run python scripts/visit_dev_mint.py mint --role guest --visit-id <22 chars>
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

# Project imports initialize logging and print banners to stdout; redirect stdout to stderr during import
# so stdout contains only this script's JSON / ticket (`--ticket-only` can pipe directly into other commands).
with contextlib.redirect_stdout(sys.stderr):
    from config import visit_settings  # noqa: E402
    from main_logic.visit import identity  # noqa: E402

ROLES = ("guest", "host")
TRANSPORTS = ("livekit", "trtc")
VISIT_ID_RE = re.compile(r"[A-Za-z0-9_-]{22}")
# Shapes the production credentials path issues (contract §4.7): sub = HMAC[:24] hex,
# vid = role initial + "_" + 24 hex, char_tag = 32 hex, jti = 22 base64url characters
VISIT_UID_RE = re.compile(r"[0-9a-f]{24}")
CHAR_TAG_RE = re.compile(r"[0-9a-f]{32}")
JTI_RE = re.compile(r"[A-Za-z0-9_-]{22}")
DISPLAY_NAME_MAX = 64  # contract: display_name?:str(<=64); the verifier hard-rejects anything over 256


class DevMintError(Exception):
    """A user-facing error of this script (bad arguments, missing key file)."""


def resolve_keyfile(keyfile: str | os.PathLike[str] | None) -> Path:
    """Return the key path from the argument, else ``NEKO_VISIT_DEV_KEYFILE``."""
    raw = os.fspath(keyfile) if keyfile else visit_settings.NEKO_VISIT_DEV_KEYFILE
    if not raw:
        raise DevMintError("no key file: pass --keyfile or set NEKO_VISIT_DEV_KEYFILE")
    return Path(raw)


def load_private_key(path: str | os.PathLike[str]) -> Ed25519PrivateKey:
    """Load the dev private key in every format ``identity.load_dev_public_key`` accepts."""
    # Share the backend's development-public-key parser so accepted formats cannot diverge.
    return identity._private_key_from_bytes(Path(path).read_bytes())


def public_key_b64url(private_key: Ed25519PrivateKey) -> str:
    """Return the base64url text of the raw 32-byte public key."""
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return identity.b64url_encode(raw)


def generate_keyfile(path: str | os.PathLike[str], *, force: bool = False) -> Ed25519PrivateKey:
    """Create a new Ed25519 key as an unencrypted PKCS#8 PEM file readable by the owner only.

    Refuses to overwrite an existing file unless ``force`` is true. On POSIX the
    file is created with mode 0600; on Windows the mode only controls the
    read-only bit, so keep the file inside your user profile.
    """
    target = Path(path)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        raise DevMintError(f"{target} exists; pass --force to overwrite") from None
    if os.name == "posix":
        # O_TRUNC does not change existing file permissions: tighten permissions on the fd before writing the private key.
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return key


def derive_vid(role: str, visit_uid: str, visit_id: str) -> str:
    """``role[0] + '_' + sha256(visit_uid + '|' + visit_id).hexdigest()[:24]`` (contract)."""
    digest = hashlib.sha256(f"{visit_uid}|{visit_id}".encode("utf-8")).hexdigest()
    return f"{role[0]}_{digest[:24]}"


def build_claims(
    *,
    role: str,
    visit_id: str | None = None,
    visit_uid: str | None = None,
    vid: str | None = None,
    transport: str = "livekit",
    char_tag: str | None = None,
    display_name: str | None = None,
    iat: int | None = None,
    ttl_s: int | None = None,
    jti: str | None = None,
) -> dict[str, Any]:
    """Assemble the claims dict of the contract table; unset ids are generated.

    ``exp`` defaults to ``iat`` plus the role lifetime (guest 2400 s, host
    3000 s); ``vid`` defaults to the contract derivation from ``visit_uid``
    and ``visit_id``. ``kid`` is always the dev kid.
    """
    if role not in ROLES:
        raise DevMintError(f"role must be one of {ROLES}")
    if transport not in TRANSPORTS:
        raise DevMintError(f"transport must be one of {TRANSPORTS}")
    # Validate explicitly supplied values, including empty strings; generate only when absent (None).
    if visit_id is not None and not VISIT_ID_RE.fullmatch(visit_id):
        # The transport page and the backend session both require this format; never sign an unusable ticket
        raise DevMintError("visit_id must be 22 characters of [A-Za-z0-9_-]")
    if visit_uid is not None and not VISIT_UID_RE.fullmatch(visit_uid):
        raise DevMintError("visit_uid must be 24 lowercase hex characters")
    if vid is not None and not re.fullmatch(rf"{role[0]}_[0-9a-f]{{24}}", vid):
        raise DevMintError(f"vid must be '{role[0]}_' followed by 24 lowercase hex characters for role {role}")
    if char_tag is not None and not CHAR_TAG_RE.fullmatch(char_tag):
        raise DevMintError("char_tag must be 32 lowercase hex characters")
    if jti is not None and not JTI_RE.fullmatch(jti):
        raise DevMintError("jti must be 22 characters of [A-Za-z0-9_-]")
    if display_name is not None and len(display_name) > DISPLAY_NAME_MAX:
        raise DevMintError(f"display_name must be at most {DISPLAY_NAME_MAX} characters")
    visit_id = visit_id if visit_id is not None else secrets.token_urlsafe(16)
    visit_uid = visit_uid if visit_uid is not None else secrets.token_hex(12)
    iat = int(time.time()) if iat is None else int(iat)
    ttl = identity.ticket_ttl_s(role) if ttl_s is None else int(ttl_s)
    if ttl <= 0:
        raise DevMintError("ttl must be positive")
    max_ttl = identity.ticket_ttl_s(role)
    if ttl > max_ttl:
        # verify_identity_ticket rejects exp - iat above the role lifetime; never print an unusable ticket
        raise DevMintError(f"ttl must not exceed the {role} ticket lifetime ({max_ttl} s)")
    claims: dict[str, Any] = {
        "v": visit_settings.VISIT_TICKET_VERSION,
        "iss": visit_settings.VISIT_TICKET_ISS,
        "aud": visit_settings.VISIT_TICKET_AUD,
        "kid": visit_settings.VISIT_DEV_KID,
        "sub": visit_uid,
        "vid": vid if vid is not None else derive_vid(role, visit_uid, visit_id),
        "visit_id": visit_id,
        "role": role,
        "transport": transport,
        "char_tag": char_tag if char_tag is not None else secrets.token_hex(16),
        "iat": iat,
        "exp": iat + ttl,
        "jti": jti if jti is not None else secrets.token_urlsafe(16),
    }
    if display_name is not None:
        claims["display_name"] = display_name
    return claims


def mint(claims: dict[str, Any], private_key: Ed25519PrivateKey) -> str:
    """Sign ``claims`` with the reference signer the verifier is tested against."""
    return identity.mint_ticket(claims, private_key)


def _cmd_keygen(args: argparse.Namespace) -> dict[str, Any]:
    key = generate_keyfile(args.out, force=args.force)
    return {
        "kid": visit_settings.VISIT_DEV_KID,
        "pub": public_key_b64url(key),
        "keyfile": str(Path(args.out).resolve()),
        "env": f"NEKO_VISIT_DEV_KEYFILE={Path(args.out).resolve()}",
    }


def _cmd_pubkey(args: argparse.Namespace) -> dict[str, Any]:
    key = load_private_key(resolve_keyfile(args.keyfile))
    return {"kid": visit_settings.VISIT_DEV_KID, "pub": public_key_b64url(key)}


def _cmd_mint(args: argparse.Namespace) -> dict[str, Any] | str:
    key = load_private_key(resolve_keyfile(args.keyfile))
    claims = build_claims(
        role=args.role,
        visit_id=args.visit_id,
        visit_uid=args.visit_uid,
        vid=args.vid,
        transport=args.transport,
        char_tag=args.char_tag,
        display_name=args.display_name,
        iat=args.iat,
        ttl_s=args.ttl,
        jti=args.jti,
    )
    ticket = mint(claims, key)
    if args.ticket_only:
        return ticket
    return {
        "kid": visit_settings.VISIT_DEV_KID,
        "pub": public_key_b64url(key),
        "ticket": ticket,
        "claims": claims,
    }


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface of the dev loopback minter."""
    parser = argparse.ArgumentParser(description="Sign dev-loopback visit identity tickets.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_keygen = sub.add_parser("keygen", help="create a new dev private key file (PKCS#8 PEM)")
    p_keygen.add_argument("--out", required=True, help="path of the key file to create")
    p_keygen.add_argument("--force", action="store_true", help="overwrite an existing file")
    p_keygen.set_defaults(func=_cmd_keygen)

    p_pub = sub.add_parser("pubkey", help="print the dev kid and public key")
    p_pub.add_argument("--keyfile", help="private key file (default: NEKO_VISIT_DEV_KEYFILE)")
    p_pub.set_defaults(func=_cmd_pubkey)

    p_mint = sub.add_parser("mint", help="sign one identity ticket")
    p_mint.add_argument("--keyfile", help="private key file (default: NEKO_VISIT_DEV_KEYFILE)")
    p_mint.add_argument("--role", required=True, choices=ROLES)
    p_mint.add_argument("--visit-id", help="22-char room id (default: random)")
    p_mint.add_argument("--visit-uid", help="24-hex account id, becomes `sub` (default: random)")
    p_mint.add_argument("--vid", help="vendor sender id (default: derived from visit_uid + visit_id)")
    p_mint.add_argument("--transport", default="livekit", choices=TRANSPORTS)
    p_mint.add_argument("--char-tag", help="32-hex character tag (default: random)")
    p_mint.add_argument("--display-name", help="optional display name claim")
    p_mint.add_argument("--iat", type=int, help="issue time, unix seconds (default: now)")
    p_mint.add_argument("--ttl", type=int, help="exp - iat in seconds (default: role lifetime)")
    p_mint.add_argument("--jti", help="ticket id (default: random)")
    p_mint.add_argument("--ticket-only", action="store_true", help="print only the ticket")
    p_mint.set_defaults(func=_cmd_mint)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except DevMintError as exc:
        print(f"visit_dev_mint: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError) as exc:
        print(f"visit_dev_mint: key file unusable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

"""``scripts/visit_dev_mint.py`` signs tickets the production verifier accepts.

The dev loopback must go through the same verification path as Servers
tickets: the script's ticket verifies through ``verify_identity_ticket`` with
the key set built by ``PubkeySet.from_runtime`` (dev key loaded from
``NEKO_VISIT_DEV_KEYFILE``), any single-byte change is rejected, and the kid
is the reserved dev kid. Key files only ever live in ``tmp_path``; nothing
here reads or writes the runtime data root.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from config import visit_settings
from config.visit_settings import VISIT_DEV_KID
from main_logic.visit import identity as idm
from main_logic.visit.identity import (
    FetchedPubkeys,
    JtiWindow,
    PubkeySet,
    TicketRejected,
    UnknownKid,
    b64url_decode,
    b64url_encode,
    load_dev_public_key,
    verify_identity_ticket,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "visit_dev_mint.py"
VISIT_ID = "AbCdEfGhIjKlMnOpQrStUv"
VISIT_UID = "0123456789abcdef01234567"
NOW = 1_800_000_000


def _load_script():
    spec = importlib.util.spec_from_file_location("visit_dev_mint_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mint_mod():
    return _load_script()


class _EmptyBlocklist:
    available = True

    def is_blocked(self, uid: str) -> bool:
        return False


@pytest.fixture
def keyfile(tmp_path, mint_mod, monkeypatch):
    path = tmp_path / "visit_dev_key.pem"
    mint_mod.generate_keyfile(path)
    # The production PubkeySet.from_runtime entry reads this module constant: point it only to tmp_path.
    monkeypatch.setattr(idm, "NEKO_VISIT_DEV_KEYFILE", str(path))
    monkeypatch.setattr(visit_settings, "NEKO_VISIT_DEV_KEYFILE", str(path))
    return path


def _runtime_pubkeys() -> PubkeySet:
    fetched = FetchedPubkeys(keys={}, revoked=frozenset(), fetched_at=NOW)
    return PubkeySet.from_runtime(now=NOW, fetched=fetched)


def _verify(ticket: str, *, role: str = "guest", vid: str, pubkeys: PubkeySet | None = None):
    return verify_identity_ticket(
        ticket,
        expect_visit_id=VISIT_ID,
        expect_role=role,
        expect_vid=vid,
        expect_transport="livekit",
        now=NOW,
        pubkeys=pubkeys or _runtime_pubkeys(),
        blocklist=_EmptyBlocklist(),
        jti_window=JtiWindow(),
    )


def _mint(mint_mod, keyfile: Path, *, role: str = "guest") -> tuple[str, dict]:
    claims = mint_mod.build_claims(
        role=role, visit_id=VISIT_ID, visit_uid=VISIT_UID, iat=NOW - 10, display_name="Neko",
    )
    return mint_mod.mint(claims, mint_mod.load_private_key(keyfile)), claims


@pytest.mark.parametrize("role", ("guest", "host"))
def test_minted_ticket_passes_production_verifier(mint_mod, keyfile, role):
    ticket, claims = _mint(mint_mod, keyfile, role=role)
    verified = _verify(ticket, role=role, vid=claims["vid"])
    assert verified.sub == VISIT_UID
    assert verified.kid == VISIT_DEV_KID
    assert verified.role == role
    assert verified.exp - verified.iat == idm.ticket_ttl_s(role)
    assert verified.vid == mint_mod.derive_vid(role, VISIT_UID, VISIT_ID)
    assert verified.vid.startswith(role[0] + "_") and len(verified.vid) == 26


def test_kid_is_the_dev_slot_and_only_the_dev_key_verifies(mint_mod, keyfile):
    ticket, claims = _mint(mint_mod, keyfile)
    assert claims["kid"] == VISIT_DEV_KID
    pubkeys = _runtime_pubkeys()
    entry = pubkeys.keys[VISIT_DEV_KID]
    assert entry.source == "dev"
    pub_raw = b64url_decode(mint_mod.public_key_b64url(mint_mod.load_private_key(keyfile)))
    assert entry.raw_bytes() == pub_raw == load_dev_public_key(keyfile).public_bytes_raw()
    # Without a development key (NEKO_VISIT_DEV_KEYFILE unset), reject the same ticket on the same path; no signature-verification bypass exists.
    without_dev = PubkeySet.build(
        now=NOW, fetched=FetchedPubkeys(keys={}, revoked=frozenset(), fetched_at=NOW), builtin={},
    )
    with pytest.raises(UnknownKid):
        _verify(ticket, vid=claims["vid"], pubkeys=without_dev)


def test_any_single_byte_change_is_rejected(mint_mod, keyfile):
    ticket, claims = _mint(mint_mod, keyfile)
    seg_claims, seg_sig = ticket.split(".")
    claims_bytes, sig = b64url_decode(seg_claims), b64url_decode(seg_sig)
    _verify(ticket, vid=claims["vid"])  # First verify the original ticket passes.

    for i in range(len(claims_bytes)):
        tampered = bytearray(claims_bytes)
        tampered[i] ^= 0x01
        forged = f"{b64url_encode(bytes(tampered))}.{seg_sig}"
        with pytest.raises(TicketRejected):
            _verify(forged, vid=claims["vid"])
    for i in range(len(sig)):
        tampered = bytearray(sig)
        tampered[i] ^= 0x01
        forged = f"{seg_claims}.{b64url_encode(bytes(tampered))}"
        with pytest.raises(TicketRejected):
            _verify(forged, vid=claims["vid"])


def test_cli_mint_reads_keyfile_from_env_setting(mint_mod, keyfile, capsys):
    assert mint_mod.main([
        "mint", "--role", "guest", "--visit-id", VISIT_ID, "--visit-uid", VISIT_UID,
        "--iat", str(NOW), "--ticket-only",
    ]) == 0
    ticket = capsys.readouterr().out.strip()
    vid = mint_mod.derive_vid("guest", VISIT_UID, VISIT_ID)
    assert _verify(ticket, vid=vid).kid == VISIT_DEV_KID

    assert mint_mod.main(["pubkey"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kid"] == VISIT_DEV_KID
    assert b64url_decode(out["pub"]) == _runtime_pubkeys().keys[VISIT_DEV_KID].raw_bytes()


def test_cli_without_keyfile_fails_closed(mint_mod, monkeypatch, capsys):
    monkeypatch.setattr(visit_settings, "NEKO_VISIT_DEV_KEYFILE", "")
    assert mint_mod.main(["mint", "--role", "guest"]) == 2
    assert "NEKO_VISIT_DEV_KEYFILE" in capsys.readouterr().err


def test_keygen_refuses_to_overwrite_and_restricts_mode(mint_mod, tmp_path, capsys):
    path = tmp_path / "k.pem"
    assert mint_mod.main(["keygen", "--out", str(path)]) == 0
    first = path.read_bytes()
    assert json.loads(capsys.readouterr().out)["kid"] == VISIT_DEV_KID
    assert mint_mod.main(["keygen", "--out", str(path)]) == 2
    assert path.read_bytes() == first
    assert mint_mod.main(["keygen", "--out", str(path), "--force"]) == 0
    assert path.read_bytes() != first
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600
    # The same backend function that reads development keys can read its generated file.
    load_dev_public_key(path)


def test_script_has_no_verification_bypass():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "mint_ticket" in source
    for needle in ("skip_verify", "verify=False", "noverify"):
        assert needle not in source


@pytest.mark.parametrize("role", ("guest", "host"))
def test_ttl_above_the_role_lifetime_is_refused(mint_mod, role):
    limit = idm.ticket_ttl_s(role)
    claims = mint_mod.build_claims(role=role, visit_id=VISIT_ID, visit_uid=VISIT_UID, iat=NOW, ttl_s=limit)
    assert claims["exp"] - claims["iat"] == limit
    with pytest.raises(mint_mod.DevMintError):
        mint_mod.build_claims(role=role, visit_id=VISIT_ID, visit_uid=VISIT_UID, iat=NOW, ttl_s=limit + 1)


@pytest.mark.parametrize("bad", ("abc", "x" * 21, "x" * 23, "a" * 21 + "!"))
def test_malformed_explicit_visit_id_is_refused(mint_mod, bad):
    with pytest.raises(mint_mod.DevMintError):
        mint_mod.build_claims(role="guest", visit_id=bad, visit_uid=VISIT_UID, iat=NOW)
    assert mint_mod.build_claims(role="guest", visit_id=VISIT_ID, visit_uid=VISIT_UID, iat=NOW)["visit_id"] == VISIT_ID


@pytest.mark.parametrize("field,bad", (
    ("visit_uid", "abc"),
    ("visit_uid", "G" * 24),
    ("vid", "h_" + "0" * 24),          # A guest ticket cannot use the host's vid.
    ("vid", "g_" + "0" * 23),
    ("char_tag", "abc"),
    ("char_tag", "Z" * 32),
    ("jti", "short"),
))
def test_malformed_explicit_identifiers_are_refused(mint_mod, field, bad):
    kwargs = {"role": "guest", "visit_id": VISIT_ID, "visit_uid": VISIT_UID, "iat": NOW}
    kwargs[field] = bad
    with pytest.raises(mint_mod.DevMintError):
        mint_mod.build_claims(**kwargs)
    good = mint_mod.build_claims(role="guest", visit_id=VISIT_ID, visit_uid=VISIT_UID, iat=NOW,
                                 vid="g_" + "0" * 24, char_tag="a" * 32, jti="J" * 22)
    assert good["vid"] == "g_" + "0" * 24 and good["char_tag"] == "a" * 32 and good["jti"] == "J" * 22


def test_overlong_display_name_is_refused(mint_mod):
    ok = mint_mod.build_claims(role="guest", visit_id=VISIT_ID, visit_uid=VISIT_UID, iat=NOW, display_name="N" * 64)
    assert ok["display_name"] == "N" * 64
    with pytest.raises(mint_mod.DevMintError):
        mint_mod.build_claims(role="guest", visit_id=VISIT_ID, visit_uid=VISIT_UID, iat=NOW, display_name="N" * 65)


@pytest.mark.parametrize("field", ("visit_id", "visit_uid", "vid", "char_tag", "jti"))
def test_empty_explicit_identifier_is_refused_not_regenerated(mint_mod, field):
    kwargs = {"role": "guest", "visit_id": VISIT_ID, "visit_uid": VISIT_UID, "iat": NOW}
    kwargs[field] = ""
    with pytest.raises(mint_mod.DevMintError):
        mint_mod.build_claims(**kwargs)

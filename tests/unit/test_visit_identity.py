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

"""Identity ticket verification: order, claims, clock tolerance, jti, keys."""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from config.visit_settings import VISIT_DEV_KID, VISIT_HOST_CREDENTIAL_TTL_S
from main_logic.visit import identity as idm
from main_logic.visit.identity import (
    BadSignature,
    ClaimMismatch,
    FetchedPubkeys,
    JtiReplay,
    JtiWindow,
    KeyOutOfWindow,
    MalformedTicket,
    PeerBlocked,
    PubkeySet,
    PubkeysStale,
    RevokedKid,
    TicketRejected,
    TicketTimeInvalid,
    UnknownKid,
    VidMismatch,
    b64url_decode,
    b64url_encode,
    mint_ticket,
    parse_pubkeys_response,
    verify_identity_ticket,
)
from main_logic.visit.limits import Blocklist

NOW = 1_800_000_000
KID = "k2026a"
VISIT_ID = "AbCdEfGhIjKlMnOpQrStUv"
SUB = "0123456789abcdef01234567"
VID = "g_" + "a" * 24
KEY_NB = NOW - 86400
KEY_NA = NOW + 30 * 86400


@pytest.fixture(scope="module")
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _pub_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64url_encode(raw)


def _builtin(key: Ed25519PrivateKey, *, kid: str = KID, nb: int = KEY_NB, na: int = KEY_NA) -> dict:
    return {kid: {"pub": _pub_b64(key), "not_before": nb, "not_after": na}}


def _fresh(revoked: frozenset[str] = frozenset(), keys: dict | None = None) -> FetchedPubkeys:
    return FetchedPubkeys(keys=keys or {}, revoked=revoked, fetched_at=NOW - 60)


@pytest.fixture
def pubkeys(priv) -> PubkeySet:
    return PubkeySet.build(now=NOW, fetched=_fresh(), builtin=_builtin(priv))


@pytest.fixture
def blocklist(tmp_path) -> Blocklist:
    return Blocklist(tmp_path)


def _claims(**over) -> dict:
    iat = over.pop("iat", NOW - 60)
    base = {
        "v": 1,
        "iss": "neko-servers",
        "aud": "neko-visit",
        "kid": KID,
        "sub": SUB,
        "vid": VID,
        "visit_id": VISIT_ID,
        "role": "guest",
        "transport": "trtc",
        "char_tag": "f" * 32,
        "display_name": "Mimi",
        "iat": iat,
        "exp": iat + 2400,
        "jti": "J" * 22,
    }
    base.update(over)
    return base


def _verify(ticket, *, pubkeys, blocklist, jti_window=None, now=NOW, **over):
    kwargs = dict(
        expect_visit_id=VISIT_ID,
        expect_role="guest",
        expect_vid=VID,
        expect_transport="trtc",
        now=now,
        pubkeys=pubkeys,
        blocklist=blocklist,
        jti_window=jti_window if jti_window is not None else JtiWindow(),
    )
    kwargs.update(over)
    return verify_identity_ticket(ticket, **kwargs)


class SpyBlocklist:
    """Blocklist double that records every lookup."""

    def __init__(self, blocked: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.blocked = blocked or set()

    def is_blocked(self, uid: str) -> bool:
        self.calls.append(uid)
        return uid in self.blocked


# ── 基本通过 ──


def test_valid_ticket_returns_claims(priv, pubkeys, blocklist):
    claims = _verify(mint_ticket(_claims(), priv), pubkeys=pubkeys, blocklist=blocklist)
    assert claims.sub == SUB and claims.vid == VID and claims.role == "guest"
    assert claims.display_name == "Mimi"


def test_wire_format_is_two_segments_and_signs_first_segment(priv):
    ticket = mint_ticket(_claims(), priv)
    seg, sig = ticket.split(".")
    assert json.loads(b64url_decode(seg))["sub"] == SUB
    priv.public_key().verify(b64url_decode(sig), seg.encode("ascii"))
    assert "=" not in ticket


# ── 五种变异必红 ──


def test_tampered_sub_rejected(priv, pubkeys, blocklist):
    ticket = mint_ticket(_claims(), priv)
    seg, sig = ticket.split(".")
    forged = json.loads(b64url_decode(seg))
    forged["sub"] = "f" * 24
    forged_seg = b64url_encode(json.dumps(forged, separators=(",", ":")).encode())
    with pytest.raises(BadSignature):
        _verify(f"{forged_seg}.{sig}", pubkeys=pubkeys, blocklist=blocklist)


def test_expired_rejected(priv, pubkeys, blocklist):
    c = _claims()
    with pytest.raises(TicketTimeInvalid):
        _verify(mint_ticket(c, priv), pubkeys=pubkeys, blocklist=blocklist, now=c["exp"] + 3600)


def test_role_swapped_rejected(priv, pubkeys, blocklist):
    with pytest.raises(ClaimMismatch) as ei:
        _verify(mint_ticket(_claims(role="host"), priv), pubkeys=pubkeys, blocklist=blocklist)
    assert ei.value.claim == "role"


def test_vid_mismatch_rejected(priv, pubkeys, blocklist):
    with pytest.raises(VidMismatch):
        _verify(mint_ticket(_claims(), priv), pubkeys=pubkeys, blocklist=blocklist, expect_vid="g_" + "b" * 24)


def test_unknown_kid_rejected(priv, pubkeys, blocklist):
    with pytest.raises(UnknownKid):
        _verify(mint_ticket(_claims(kid="other"), priv), pubkeys=pubkeys, blocklist=blocklist)


# ── 时钟容差 ──


def test_iat_299s_in_future_passes_301s_rejected(priv, pubkeys, blocklist):
    _verify(mint_ticket(_claims(iat=NOW + 299), priv), pubkeys=pubkeys, blocklist=blocklist)
    with pytest.raises(TicketTimeInvalid):
        _verify(mint_ticket(_claims(iat=NOW + 301), priv), pubkeys=pubkeys, blocklist=blocklist)


def test_exp_plus_299s_passes_301s_rejected(priv, pubkeys, blocklist):
    c = _claims()
    ticket = mint_ticket(c, priv)
    _verify(ticket, pubkeys=pubkeys, blocklist=blocklist, now=c["exp"] + 299)
    with pytest.raises(TicketTimeInvalid):
        _verify(ticket, pubkeys=pubkeys, blocklist=blocklist, now=c["exp"] + 301)


# ── jti 窗口 ──


def test_same_room_same_vid_may_replay_jti(priv, pubkeys, blocklist):
    window = JtiWindow()
    ticket = mint_ticket(_claims(), priv)
    _verify(ticket, pubkeys=pubkeys, blocklist=blocklist, jti_window=window)
    _verify(ticket, pubkeys=pubkeys, blocklist=blocklist, jti_window=window)
    assert len(window) == 1


def test_jti_replayed_by_other_vid_rejected(priv, pubkeys, blocklist):
    window = JtiWindow()
    _verify(mint_ticket(_claims(), priv), pubkeys=pubkeys, blocklist=blocklist, jti_window=window)
    other_vid = "g_" + "c" * 24
    with pytest.raises(JtiReplay):
        _verify(
            mint_ticket(_claims(vid=other_vid), priv),
            pubkeys=pubkeys, blocklist=blocklist, jti_window=window, expect_vid=other_vid,
        )


def test_jti_replayed_in_other_room_rejected(priv, pubkeys, blocklist):
    window = JtiWindow()
    _verify(mint_ticket(_claims(), priv), pubkeys=pubkeys, blocklist=blocklist, jti_window=window)
    other_room = "ZzZzZzZzZzZzZzZzZzZzZz"
    with pytest.raises(JtiReplay):
        _verify(
            mint_ticket(_claims(visit_id=other_room), priv),
            pubkeys=pubkeys, blocklist=blocklist, jti_window=window, expect_visit_id=other_room,
        )


def test_failed_verification_does_not_record_jti(priv, pubkeys, blocklist):
    window = JtiWindow()
    with pytest.raises(VidMismatch):
        _verify(mint_ticket(_claims(), priv), pubkeys=pubkeys, blocklist=blocklist,
                jti_window=window, expect_vid="g_" + "b" * 24)
    assert len(window) == 0


def test_jti_window_evicts_oldest():
    window = JtiWindow(max_entries=2)
    for j in ("a", "b", "c"):
        window.record(j, visit_id=VISIT_ID, vid=VID)
    assert len(window) == 2
    assert window.check("a", visit_id="other", vid=VID)
    assert not window.check("c", visit_id="other", vid=VID)


# ── 黑名单与核验顺序 ──


def test_blocklist_hit_is_peer_blocked(priv, pubkeys, tmp_path):
    bl = Blocklist(tmp_path)
    bl.block(SUB, display_name_at_block="Mimi", now=NOW)
    with pytest.raises(PeerBlocked) as ei:
        _verify(mint_ticket(_claims(), priv), pubkeys=pubkeys, blocklist=bl)
    assert ei.value.finalize_reason == "peer_blocked"


def test_unreadable_blocklist_rejects_every_peer(priv, pubkeys, tmp_path):
    from main_logic.visit.identity import BlocklistUnavailable

    (tmp_path / "visit_blocklist.json").write_text("{broken", encoding="utf-8")
    bl = Blocklist.load(tmp_path)
    with pytest.raises(BlocklistUnavailable) as ei:
        _verify(mint_ticket(_claims(), priv), pubkeys=pubkeys, blocklist=bl)
    assert ei.value.finalize_reason == "peer_identity_rejected"


def test_blocklist_not_read_when_signature_fails(priv, pubkeys):
    spy = SpyBlocklist({SUB})
    other = Ed25519PrivateKey.generate()
    with pytest.raises(BadSignature):
        _verify(mint_ticket(_claims(), other), pubkeys=pubkeys, blocklist=spy)
    assert spy.calls == []


@pytest.mark.parametrize("bad", [
    {"v": 2}, {"iss": "other"}, {"aud": "other"}, {"transport": "livekit"}, {"role": "host"},
])
def test_blocklist_not_read_when_claims_fail(priv, pubkeys, bad):
    spy = SpyBlocklist({SUB})
    with pytest.raises(ClaimMismatch):
        _verify(mint_ticket(_claims(**bad), priv), pubkeys=pubkeys, blocklist=spy)
    assert spy.calls == []


def test_blocklist_read_once_after_all_checks(priv, pubkeys):
    spy = SpyBlocklist()
    _verify(mint_ticket(_claims(), priv), pubkeys=pubkeys, blocklist=spy)
    assert spy.calls == [SUB]


def test_stale_pubkeys_rejected_before_anything(priv):
    stale = PubkeySet.build(now=NOW, fetched=None, builtin=_builtin(priv))
    assert stale.stale
    spy = SpyBlocklist({SUB})
    with pytest.raises(PubkeysStale):
        _verify(mint_ticket(_claims(), priv), pubkeys=stale, blocklist=spy)
    with pytest.raises(PubkeysStale):
        _verify("garbage", pubkeys=stale, blocklist=spy)
    assert spy.calls == []


def test_revoked_checked_before_unknown_kid(priv):
    pk = PubkeySet.build(now=NOW, fetched=_fresh(revoked=frozenset({"gone"})), builtin=_builtin(priv))
    with pytest.raises(RevokedKid):
        _verify(mint_ticket(_claims(kid="gone"), priv), pubkeys=pk, blocklist=SpyBlocklist())


# ── v / iss / aud / transport 各自篡改（签名有效）──


@pytest.mark.parametrize("claim,value", [
    ("v", 2), ("iss", "other"), ("aud", "other"), ("transport", "livekit"), ("visit_id", "Q" * 22),
])
def test_each_fixed_claim_checked(priv, pubkeys, blocklist, claim, value):
    with pytest.raises(ClaimMismatch) as ei:
        _verify(mint_ticket(_claims(**{claim: value}), priv), pubkeys=pubkeys, blocklist=blocklist)
    assert ei.value.claim == claim
    assert ei.value.finalize_reason == "peer_identity_rejected"


def test_bool_version_is_not_one(priv, pubkeys, blocklist):
    with pytest.raises(MalformedTicket):
        _verify(mint_ticket(_claims(v=True), priv), pubkeys=pubkeys, blocklist=blocklist)


def test_transport_must_match_the_room(priv, pubkeys, blocklist):
    ticket = mint_ticket(_claims(transport="livekit"), priv)
    _verify(ticket, pubkeys=pubkeys, blocklist=blocklist, expect_transport="livekit")
    with pytest.raises(ClaimMismatch):
        _verify(ticket, pubkeys=pubkeys, blocklist=blocklist, expect_transport="trtc")


# ── 吊销与钥匙有效期 ──


def test_builtin_kid_in_revoked_list_is_rejected(priv):
    pk = PubkeySet.build(now=NOW, fetched=_fresh(revoked=frozenset({KID})), builtin=_builtin(priv))
    assert KID in pk.keys
    with pytest.raises(RevokedKid):
        _verify(mint_ticket(_claims(), priv), pubkeys=pk, blocklist=SpyBlocklist())


def test_iat_before_key_not_before_rejected(priv, blocklist):
    pk = PubkeySet.build(now=NOW, fetched=_fresh(), builtin=_builtin(priv, nb=NOW - 10))
    with pytest.raises(KeyOutOfWindow):
        _verify(mint_ticket(_claims(iat=NOW - 60), priv), pubkeys=pk, blocklist=blocklist)


def test_iat_after_key_not_after_rejected(priv, blocklist):
    # 下线后泄露的旧私钥新签的票：iat 在钥匙 not_after 之后。
    pk = PubkeySet.build(now=NOW, fetched=_fresh(), builtin=_builtin(priv, na=NOW - 100))
    with pytest.raises(KeyOutOfWindow):
        _verify(mint_ticket(_claims(iat=NOW - 60), priv), pubkeys=pk, blocklist=blocklist)


def test_key_accepted_until_one_longest_ticket_after_retirement(priv, blocklist):
    na = NOW - 1000
    pk_at = PubkeySet.build(now=NOW, fetched=_fresh(), builtin=_builtin(priv, na=na))
    c = _claims(iat=na, exp=na + VISIT_HOST_CREDENTIAL_TTL_S)
    ticket = mint_ticket(c, priv)
    last_ok = na + VISIT_HOST_CREDENTIAL_TTL_S + 300
    _verify(ticket, pubkeys=pk_at, blocklist=blocklist, now=last_ok)
    with pytest.raises(KeyOutOfWindow):
        _verify(ticket, pubkeys=pk_at, blocklist=blocklist, now=last_ok + 1)


# ── 公钥集合：过期、合并、开发键 ──


def test_pubkeys_stale_after_cache_window(priv):
    fetched = FetchedPubkeys(keys={}, revoked=frozenset(), fetched_at=NOW - 86401, ttl_s=86400)
    assert PubkeySet.build(now=NOW, fetched=fetched, builtin=_builtin(priv)).stale
    fresh = PubkeySet.build(now=NOW, fetched=_fresh(), builtin=_builtin(priv))
    assert not fresh.stale
    assert fresh.is_stale_at(NOW - 60 + 86400 + 1)


def test_fetched_ttl_cannot_exceed_cache_cap(priv):
    fetched = FetchedPubkeys(keys={}, revoked=frozenset(), fetched_at=NOW - 90000, ttl_s=10 ** 9)
    assert PubkeySet.build(now=NOW, fetched=fetched, builtin=_builtin(priv)).stale


def test_fetched_key_is_usable(priv, blocklist):
    payload = {
        "keys": [{"kid": "k2", "alg": "Ed25519", "pub": _pub_b64(priv), "not_before": KEY_NB, "not_after": KEY_NA}],
        "revoked": [],
        "ttl_s": 86400,
    }
    fetched = parse_pubkeys_response(payload, fetched_at=NOW)
    pk = PubkeySet.build(now=NOW, fetched=fetched, builtin={})
    _verify(mint_ticket(_claims(kid="k2"), priv), pubkeys=pk, blocklist=blocklist)


def test_fetched_key_without_window_is_skipped(priv):
    payload = {"keys": [{"kid": "k2", "alg": "Ed25519", "pub": _pub_b64(priv)}], "revoked": ["x"], "ttl_s": 60}
    fetched = parse_pubkeys_response(payload, fetched_at=NOW)
    assert fetched.keys == {} and fetched.revoked == frozenset({"x"})


def test_malformed_pubkeys_envelope_raises():
    with pytest.raises(ValueError):
        parse_pubkeys_response({"keys": "nope"}, fetched_at=NOW)


def test_conflicting_kid_between_sources_is_dropped(priv):
    other = Ed25519PrivateKey.generate()
    fetched = parse_pubkeys_response(
        {"keys": [{"kid": KID, "pub": _pub_b64(other), "not_before": KEY_NB, "not_after": KEY_NA}],
         "revoked": [], "ttl_s": 86400},
        fetched_at=NOW,
    )
    pk = PubkeySet.build(now=NOW, fetched=fetched, builtin=_builtin(priv))
    assert KID not in pk.keys


def test_same_kid_in_both_sources_uses_stricter_window(priv):
    fetched = parse_pubkeys_response(
        {"keys": [{"kid": KID, "pub": _pub_b64(priv), "not_before": KEY_NB, "not_after": NOW + 5}],
         "revoked": [], "ttl_s": 86400},
        fetched_at=NOW,
    )
    pk = PubkeySet.build(now=NOW, fetched=fetched, builtin=_builtin(priv))
    assert pk.keys[KID].not_after == NOW + 5


def test_dev_kid_only_from_dev_key(priv, blocklist):
    fetched = parse_pubkeys_response(
        {"keys": [{"kid": VISIT_DEV_KID, "pub": _pub_b64(priv), "not_before": 0, "not_after": KEY_NA}],
         "revoked": [], "ttl_s": 86400},
        fetched_at=NOW,
    )
    assert VISIT_DEV_KID not in fetched.keys
    pk = PubkeySet.build(now=NOW, fetched=fetched, builtin={VISIT_DEV_KID: _builtin(priv)[KID]})
    assert VISIT_DEV_KID not in pk.keys


def test_dev_key_uses_the_same_verification_path(tmp_path, blocklist):
    dev = Ed25519PrivateKey.generate()
    keyfile = tmp_path / "dev.pem"
    keyfile.write_bytes(dev.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    pk = PubkeySet.build(
        now=NOW, fetched=_fresh(), builtin={}, dev_public_key=idm.load_dev_public_key(keyfile),
    )
    _verify(mint_ticket(_claims(kid=VISIT_DEV_KID), dev), pubkeys=pk, blocklist=blocklist)
    with pytest.raises(BadSignature):
        _verify(mint_ticket(_claims(kid=VISIT_DEV_KID), Ed25519PrivateKey.generate()),
                pubkeys=pk, blocklist=blocklist)
    # 开发键同样受吊销与公钥表过期约束。
    stale = PubkeySet.build(now=NOW, fetched=None, builtin={}, dev_public_key=dev.public_key())
    with pytest.raises(PubkeysStale):
        _verify(mint_ticket(_claims(kid=VISIT_DEV_KID), dev), pubkeys=stale, blocklist=blocklist)


@pytest.mark.parametrize("fmt", ["raw", "hex", "b64"])
def test_dev_keyfile_formats(tmp_path, fmt):
    dev = Ed25519PrivateKey.generate()
    seed = dev.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption(),
    )
    data = {"raw": seed, "hex": seed.hex().encode(), "b64": b64url_encode(seed).encode()}[fmt]
    path = tmp_path / "dev.key"
    path.write_bytes(data)
    loaded = idm.load_dev_public_key(path)
    assert loaded.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) == \
        dev.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


# ── 畸形票据 ──


@pytest.mark.parametrize("ticket", [
    "", "onlyone", "a.b.c", "!!!.AAAA", 123, "x" * 5000,
])
def test_malformed_tickets(ticket, pubkeys, blocklist):
    with pytest.raises(MalformedTicket):
        _verify(ticket, pubkeys=pubkeys, blocklist=blocklist)


def test_short_signature_is_malformed(priv, pubkeys, blocklist):
    seg = mint_ticket(_claims(), priv).split(".")[0]
    with pytest.raises(MalformedTicket):
        _verify(f"{seg}.{b64url_encode(b'x' * 10)}", pubkeys=pubkeys, blocklist=blocklist)


def test_duplicate_claim_keys_are_malformed(priv, pubkeys, blocklist):
    body = json.dumps(_claims(), separators=(",", ":"))
    body = body[:-1] + ',"sub":"ffffffffffffffffffffffff"}'
    seg = b64url_encode(body.encode())
    sig = b64url_encode(priv.sign(seg.encode("ascii")))
    with pytest.raises(MalformedTicket):
        _verify(f"{seg}.{sig}", pubkeys=pubkeys, blocklist=blocklist)


def test_missing_claim_is_malformed(priv, pubkeys, blocklist):
    c = _claims()
    del c["jti"]
    with pytest.raises(MalformedTicket):
        _verify(mint_ticket(c, priv), pubkeys=pubkeys, blocklist=blocklist)


def test_every_rejection_is_a_ticket_rejected():
    for cls in (PubkeysStale, MalformedTicket, RevokedKid, UnknownKid, BadSignature,
                KeyOutOfWindow, TicketTimeInvalid, VidMismatch, PeerBlocked, JtiReplay):
        assert issubclass(cls, TicketRejected)
        assert cls.code
    assert ClaimMismatch("v").finalize_reason == "peer_identity_rejected"
    assert PeerBlocked().finalize_reason == "peer_blocked"

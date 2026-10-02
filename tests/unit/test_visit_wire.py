"""Visit wire helpers: envelope, schema, wire budgets and clause splitting (PR-03)."""
from __future__ import annotations

import json
import os
import random
import re

import pytest

from config.visit_settings import (
    VISIT_CLAUSE_MAX_MS,
    VISIT_DELTA_TEXT_MAX_BYTES,
    VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES,
    VISIT_REORDER_BUFFER_MAX,
    VISIT_LP_MAX,
    VISIT_PIECE_MAX_BYTES,
    VISIT_PIECES_MAX,
    VISIT_REASSEMBLY_MAX_ENTRIES,
    VISIT_REASSEMBLY_TIMEOUT_S,
    VISIT_TEXT_MAX_BYTES,
    VISIT_WIRE_PROTO,
)
from utils import visit_wire as vw

VID = "AbCdEfGhIjKlMnOpQrStUv"
U32_MAX = 2 ** 32 - 1
WIDE_LN = "h:" + "9" * 10

_CJK = [chr(c) for c in range(0x4E00, 0x4E00 + 3000)]
_RU = [chr(c) for c in range(0x0410, 0x0450)]
_EMOJI = ["😀", "🐱", "👍🏽", "👨\u200d👩\u200d👧", "🇨🇳", "❤\ufe0f", "1\ufe0f\u20e3"]
_ASCII = list("abcxyz ,.!?\"\\")
_PUNCT = list("。，！？、…\n")


def _rand_text(rng: random.Random, n: int, *, punct: bool = True) -> str:
    pools = [_CJK] * 5 + [_RU] * 2 + [_EMOJI] + [_ASCII] * 2
    if punct:
        pools.append(_PUNCT)
    return "".join(rng.choice(rng.choice(pools)) for _ in range(n))


def _clamp_utf8(s: str, limit: int) -> str:
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _text_msg(txt: str, **kw) -> dict:
    msg = {
        "t": "text", "ln": "g:12", "lp": 40, "seq": 17, "sp": "c", "ad": "hc",
        "rt": "h:11", "wu": False, "final": True, "txt": txt, "truncated": False,
        "i_done": 3,
    }
    msg.update(kw)
    return msg


def _delta_msg(txt: str, i: int = 0, ln: str = "g:7", **kw) -> dict:
    msg = {"t": "line_delta", "ln": ln, "i": i, "lp": 3, "txt": txt}
    if i == 0:
        msg.update({"sp": "c", "ad": "hc", "rt": "", "wu": False})
    msg.update(kw)
    return msg


def _env_bytes(env: dict) -> bytes:
    return json.dumps(env, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _pieces(payload: dict) -> int:
    return vw.wire_size(vw.encode_msg(payload), visit_id=VID)[0]


def _span_redactor(word: str, label: str):
    """Return a fake redact that replaces ``word`` with ``label`` and reports spans."""
    def redact(raw: str):
        out: list[str] = []
        spans = []
        last = 0
        red_len = 0
        for m in re.finditer(re.escape(word), raw):
            gap = raw[last:m.start()]
            out.append(gap)
            red_len += len(gap)
            spans.append((m.start(), m.end(), red_len, red_len + len(label)))
            out.append(label)
            red_len += len(label)
            last = m.end()
        out.append(raw[last:])
        return "".join(out), spans
    return redact


# ── ID gate and paths ──────────────────────────────────────────────────

def test_require_visit_id_accepts_only_the_exact_format():
    assert vw.require_visit_id(VID) == VID
    for bad in [VID[:-1], VID + "x", VID[:-1] + "/", VID[:-1] + ".", VID + "\n", "", None, 22]:
        with pytest.raises(ValueError):
            vw.require_visit_id(bad)


def test_visit_and_revocation_paths_stay_inside_base(tmp_path):
    base = tmp_path / "visit_spool"
    base.mkdir()
    p = vw.visit_path(base, VID, ".jsonl")
    assert p == base.resolve() / f"{VID}.jsonl"
    assert vw.visit_path(base, VID, ".state.json").name == f"{VID}.state.json"
    for bad_suffix in ["/../x", "\\..\\x", ":ads", "\x00"]:
        with pytest.raises(ValueError):
            vw.visit_path(base, VID, bad_suffix)
    with pytest.raises(ValueError):
        vw.visit_path(base, "../" + VID[3:], ".jsonl")
    rev = "0123456789abcdef" * 2
    assert vw.revocation_path(base, rev) == base.resolve() / f"{rev}.json"
    for bad in [VID, rev.upper(), rev[:-1], rev + "0"]:
        with pytest.raises(ValueError):
            vw.revocation_path(base, bad)
    clearing = "clearing-" + rev
    assert vw.id_path(base, clearing, vw.CLEARING_ID_RE, ".json").name == clearing + ".json"


def test_id_path_rejects_symlink_escaping_base(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("x", encoding="utf-8")
    link = base / f"{VID}.jsonl"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    with pytest.raises(ValueError):
        vw.visit_path(base, VID, ".jsonl")


# ── Envelope fragmentation and reassembly ──────────────────────────────

def test_fragment_round_trip_random_payloads_each_piece_within_limit():
    """1000 random CJK / emoji / Cyrillic text payloads: every piece <= 1000 B,
    pieces reassemble (in shuffled order) to the exact payload."""
    rng = random.Random(20261002)
    for k in range(1000):
        txt = _clamp_utf8(_rand_text(rng, rng.randint(0, 1200)), VISIT_TEXT_MAX_BYTES)
        payload = vw.encode_msg(_text_msg(txt, seq=k))
        msg_id = rng.randint(0, U32_MAX)
        pieces = vw.fragment(payload, visit_id=VID, msg_id=msg_id)
        assert all(len(p) <= VISIT_PIECE_MAX_BYTES for p in pieces)
        envs = [json.loads(p.decode("utf-8")) for p in pieces]
        assert "".join(e["p"] for e in envs) == payload
        assert all(e["n"] == len(pieces) and e["r"] == VID[:8] and e["m"] == msg_id for e in envs)
        order = list(range(len(pieces)))
        rng.shuffle(order)
        ra = vw.Reassembler(visit_id=VID)
        results = [ra.feed("peer", pieces[i], now=0.0) for i in order]
        assert all(r is None for r in results[:-1])
        assert results[-1] == json.loads(payload)
        assert ra.dropped == 0 and ra.pending == 0


def test_longest_legal_text_fragments_within_limits():
    """A 4096 B body with every optional field at its widest still splits into
    pieces of <= 1000 B each, and ordinary CJK / emoji / Cyrillic bodies need <= 5."""
    bodies = [
        "好" * 1365 + "a",
        "🐱" * 1024,
        "Ж" * 2048,
        "".join(rng_c for rng_c in ("好", "a", "🐱") * 300)[:2000],
    ]
    for body in bodies:
        body = _clamp_utf8(body, VISIT_TEXT_MAX_BYTES)
        msg = _text_msg(body, ln=WIDE_LN, rt=WIDE_LN, lp=VISIT_LP_MAX, seq=U32_MAX,
                        truncated=True, i_done=255, trunc_reason="human_interrupt",
                        lang="x" * 16, tail_ms=VISIT_CLAUSE_MAX_MS)
        pieces = vw.fragment(vw.encode_msg(msg), visit_id=VID, msg_id=U32_MAX)
        assert all(len(p) <= VISIT_PIECE_MAX_BYTES for p in pieces)
        assert len(pieces) <= 5


def test_reassembler_out_of_order_and_missing_piece_timeout():
    payload = vw.encode_msg(_text_msg("好" * 1000))
    pieces = vw.fragment(payload, visit_id=VID, msg_id=5)
    assert len(pieces) >= 3
    ra = vw.Reassembler(visit_id=VID)
    # missing piece: nothing delivered, dropped once the 6 s window passes
    for p in pieces[1:]:
        assert ra.feed("peer", p, now=100.0) is None
    assert ra.pending == 1
    ra.sweep(100.0 + VISIT_REASSEMBLY_TIMEOUT_S - 0.1)
    assert ra.pending == 1 and ra.dropped == 0
    ra.sweep(100.0 + VISIT_REASSEMBLY_TIMEOUT_S + 0.1)
    assert ra.pending == 0 and ra.dropped == 1 and ra.timeouts == 1
    # the late piece alone cannot complete anything
    assert ra.feed("peer", pieces[0], now=200.0) is None
    # same msg id from another sender is a separate entry
    for p in reversed(pieces):
        out = ra.feed("other", p, now=300.0)
    assert out == json.loads(payload)


def test_reassembler_drops_and_counts_bad_pieces():
    payload = vw.encode_msg(_text_msg("好" * 1000))
    pieces = vw.fragment(payload, visit_id=VID, msg_id=9)
    ra = vw.Reassembler(visit_id=VID)
    foreign = vw.fragment(payload, visit_id="Z" * 22, msg_id=9)[0]
    assert ra.feed("peer", foreign, now=0) is None
    env = json.loads(pieces[0])
    bad_i = _env_bytes(dict(env, i=env["n"]))
    assert ra.feed("peer", bad_i, now=0) is None
    bad_n = _env_bytes(dict(env, n=9))
    assert ra.feed("peer", bad_n, now=0) is None
    assert ra.feed("peer", b"\xff\xfe", now=0) is None
    assert ra.feed("peer", b"x" * (VISIT_PIECE_MAX_BYTES + 1), now=0) is None
    assert ra.dropped == 5
    # n disagreeing with an existing entry discards that entry
    assert ra.feed("peer", pieces[0], now=0) is None
    other_n = _env_bytes(dict(json.loads(pieces[1]), n=env["n"] + 1))
    assert ra.feed("peer", other_n, now=0) is None
    assert ra.pending == 0 and ra.dropped == 6
    # duplicates keep the first copy
    assert ra.feed("peer", pieces[0], now=0) is None
    assert ra.feed("peer", pieces[0], now=0) is None
    assert ra.duplicates == 1


def test_reassembler_evicts_oldest_beyond_cap():
    payload = vw.encode_msg(_text_msg("好" * 1000))
    ra = vw.Reassembler(visit_id=VID)
    for m in range(VISIT_REASSEMBLY_MAX_ENTRIES + 3):
        ra.feed("peer", vw.fragment(payload, visit_id=VID, msg_id=m)[0], now=float(m) * 0.01)
    assert ra.pending == VISIT_REASSEMBLY_MAX_ENTRIES
    assert ra.evicted == 3 and ra.dropped == 3


# ── line_delta budget and clause splitting on the encoded form ─────────

def test_line_delta_encoded_len_matches_double_escaping_and_is_additive():
    rng = random.Random(11)
    empty = vw.line_delta_encoded_len("")
    for _ in range(300):
        txt = _rand_text(rng, rng.randint(0, 60)) + rng.choice(["", "\x01", "\t", '"\\'])
        payload = vw.encode_msg(_delta_msg(txt, ln=WIDE_LN, rt=WIDE_LN, lp=VISIT_LP_MAX, i=0))
        once = json.dumps(payload, ensure_ascii=False)[1:-1].encode("utf-8")
        actual = len(once)
        assert actual <= vw.line_delta_encoded_len(txt)
        a, b = txt[: len(txt) // 2], txt[len(txt) // 2:]
        assert vw.line_delta_encoded_len(a + b) == (
            vw.line_delta_encoded_len(a) + vw.line_delta_encoded_len(b) - empty)


@pytest.mark.parametrize("ch", ['"', "\\"])
def test_quote_dense_800_byte_delta_splits_into_single_piece_deltas(ch):
    """800 quotes (or backslashes) cost ~3.2 KB once double escaped; the splitter
    must cut them into >= 4 clauses that each encode to <= 900 B, so every delta
    is one envelope piece. Mutation: judging the 800 B cap on raw UTF-8 bytes
    yields one clause of ~3.2 KB that needs several pieces."""
    line = ch * VISIT_DELTA_TEXT_MAX_BYTES
    splitter = vw.ClauseSplitter()
    clauses = splitter.feed(line) + splitter.flush()
    assert len(clauses) >= 4
    assert "".join(c.text for c in clauses) == line
    for c in clauses:
        assert vw.line_delta_encoded_len(c.text) <= VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES
        payload = vw.encode_msg(_delta_msg(c.text, ln=WIDE_LN, rt=WIDE_LN, lp=VISIT_LP_MAX))
        assert len(vw.fragment(payload, visit_id=VID, msg_id=U32_MAX)) == 1
    # merging two adjacent clauses (250 ms / backlog merge) would exceed 900 B
    assert not vw.line_delta_can_merge(clauses[0].text, clauses[1].text)
    assert vw.line_delta_encoded_len(clauses[0].text + clauses[1].text) > VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES
    assert vw.line_delta_can_merge("短句。", "又一句。")


def test_line_delta_payload_cap_on_encode_and_decode():
    plain = "a" * VISIT_DELTA_TEXT_MAX_BYTES
    payload = vw.encode_msg(_delta_msg(plain, i=5))
    assert len(payload.encode("utf-8")) <= VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES
    assert len(vw.fragment(payload, visit_id=VID, msg_id=U32_MAX)) == 1
    with pytest.raises(ValueError):
        vw.encode_msg(_delta_msg('"' * 400, i=5))          # escaped form > 900 B
    with pytest.raises(ValueError):
        vw.encode_msg(_delta_msg("a" * (VISIT_DELTA_TEXT_MAX_BYTES + 1), i=5))
    oversize = json.dumps(_delta_msg("a" * 790, i=5, pad="x" * 200), ensure_ascii=False)
    with pytest.raises(ValueError):
        vw.decode_msg(oversize, cmd=2)


def test_random_clauses_always_fit_one_delta_piece():
    """1000 random CJK / emoji / Cyrillic lines: every clause is <= 800 raw bytes,
    <= 900 encoded bytes, encodes as a line_delta and fragments into one piece."""
    rng = random.Random(4242)
    for k in range(1000):
        line = _rand_text(rng, rng.randint(1, 700), punct=bool(k % 3))
        clauses = vw.split_clauses(line)
        assert "".join(clauses) == line
        for c in clauses:
            assert len(c.encode("utf-8")) <= VISIT_DELTA_TEXT_MAX_BYTES
            assert vw.line_delta_encoded_len(c) <= VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES
        payload = vw.encode_msg(_delta_msg(clauses[0], ln=WIDE_LN, rt=WIDE_LN, lp=VISIT_LP_MAX))
        assert len(vw.fragment(payload, visit_id=VID, msg_id=U32_MAX)) == 1


# ── fit_text_to_wire ───────────────────────────────────────────────────

@pytest.mark.parametrize("unit", ['"', "\\", '"\\'])
def test_fit_text_to_wire_truncates_escape_dense_body(unit):
    """A 4096 B body of quotes / backslashes passes clamp_text_utf8(4096) but
    expands ~4x on the wire; fit_text_to_wire must cut it to <= 8 pieces at a
    character boundary. Mutation: dropping the cut loop leaves > 8 pieces."""
    txt = (unit * VISIT_TEXT_MAX_BYTES)[:VISIT_TEXT_MAX_BYTES]
    payload = _text_msg(txt)
    assert _pieces(payload) > VISIT_PIECES_MAX
    out = vw.fit_text_to_wire(payload, visit_id=VID)
    assert out["truncated"] is True and out["trunc_reason"] == "wire_size"
    assert txt.startswith(out["txt"]) and 0 < len(out["txt"]) < len(txt)
    assert _pieces(out) <= VISIT_PIECES_MAX
    longer = dict(out, txt=txt[: len(out["txt"]) + 1])
    assert _pieces(longer) > VISIT_PIECES_MAX
    assert payload["txt"] == txt and payload["truncated"] is False   # input untouched


@pytest.mark.parametrize("body", [
    _clamp_utf8("好" * 2000, VISIT_TEXT_MAX_BYTES),
    "🐱" * 1024,
    _clamp_utf8("👨\u200d👩\u200d👧" * 300, VISIT_TEXT_MAX_BYTES),
])
def test_fit_text_to_wire_keeps_ordinary_4096_byte_bodies(body):
    payload = _text_msg(body)
    out = vw.fit_text_to_wire(payload, visit_id=VID)
    assert out == payload


def test_fit_text_to_wire_is_a_real_cut_for_cat_lines_and_records_diagnostic(caplog):
    """An oversize cat-line final (should not happen) is still cut to <= 8 pieces
    with wire_size, and one diagnostic is emitted. Mutation: recording the
    diagnostic without cutting leaves a final that cannot be sent."""
    diags: list[dict] = []
    final = _text_msg('"' * 3000 + "好" * 300, i_done=40, tail_ms=900, lang="zh-CN")
    with caplog.at_level("WARNING", logger="utils.visit_wire"):
        out = vw.fit_text_to_wire(final, visit_id=VID, on_truncate=diags.append)
    assert _pieces(out) <= VISIT_PIECES_MAX
    assert out["trunc_reason"] == "wire_size" and out["truncated"] is True
    assert final["txt"].startswith(out["txt"])
    assert len(diags) == 1 and diags[0]["kept_bytes"] < diags[0]["orig_bytes"]
    assert any("wire" in r.getMessage() for r in caplog.records)
    fine: list[dict] = []
    vw.fit_text_to_wire(_text_msg("好的。"), visit_id=VID, on_truncate=fine.append)
    assert fine == []


def test_fit_text_to_wire_does_not_split_emoji_clusters():
    family = "👨\u200d👩\u200d👧"
    txt = '"' * 1700 + family * 40
    out = vw.fit_text_to_wire(_text_msg(_clamp_utf8(txt, VISIT_TEXT_MAX_BYTES)), visit_id=VID)
    assert out["txt"].endswith(family) or out["txt"].endswith('"')


# ── WireBudget ─────────────────────────────────────────────────────────

_HEADER = {"ln": "h:12", "sp": "c", "ad": "gc", "rt": "g:11", "wu": False, "lang": "zh-CN"}


def _widest_pieces(header: dict, txt: str) -> int:
    return _pieces(vw.widest_text_payload(header, txt))


def test_wire_budget_cuts_escape_dense_stream_exactly_at_eight_pieces():
    """Escape-dense deltas: the first short take sets exhausted; the accepted text
    in the widest final text encodes to <= 8 pieces and one more char to > 8.
    Mutation: budgeting raw txt bytes (no double escaping) overshoots 8 pieces."""
    rng = random.Random(7)
    budget = vw.WireBudget(visit_id=VID, header=_HEADER)
    accepted: list[str] = []
    delta = got = ""
    for _ in range(5000):
        delta = "".join(rng.choice('"\\a') for _ in range(rng.randint(1, 12)))
        got = budget.take(delta)
        assert delta.startswith(got)
        accepted.append(got)
        if len(got) < len(delta):
            break
    assert budget.exhausted
    assert budget.take("more") == ""
    total = "".join(accepted)
    assert budget.accepted_text == total == budget.outbound_text()
    assert _widest_pieces(_HEADER, total) <= VISIT_PIECES_MAX
    assert _widest_pieces(_HEADER, total + delta[len(got)]) > VISIT_PIECES_MAX
    final = _text_msg(total, ln="h:12", rt="g:11", ad="gc", lang="zh-CN",
                      truncated=True, trunc_reason="wire_size", i_done=200, tail_ms=0)
    assert vw.fit_text_to_wire(final, visit_id=VID) == final


def test_wire_budget_measures_redacted_outbound_form():
    """A 2-char name redacted to a 12-char escape-heavy label: take must budget the
    redacted + sanitized form, so the real final stays <= 8 pieces and
    fit_text_to_wire has nothing to cut. Mutation: budgeting the raw text lets
    the redacted final exceed 8 pieces."""
    name, label = "阿明", '"家"' * 4
    assert len(label) == 12

    def redact(s: str) -> str:
        return s.replace(name, label)

    def sanitize(s: str) -> str:
        return s.replace("\x00", "")

    rng = random.Random(99)
    raw_line = (name + ",") * 2000
    budget = vw.WireBudget(visit_id=VID, header=_HEADER, redact=redact, sanitize=sanitize)
    pos = 0
    while pos < len(raw_line) and not budget.exhausted:
        step = rng.randint(1, 5)
        budget.take(raw_line[pos:pos + step])
        pos += step
    assert budget.exhausted
    outbound = sanitize(redact(budget.accepted_text))
    assert outbound == budget.outbound_text()
    assert _widest_pieces(_HEADER, outbound) >= VISIT_PIECES_MAX - 1   # cut near the limit
    final = _text_msg(outbound, ln="h:12", rt="g:11", ad="gc", lang="zh-CN",
                      truncated=True, trunc_reason="wire_size", i_done=30, tail_ms=0)
    assert _pieces(final) <= VISIT_PIECES_MAX
    assert vw.fit_text_to_wire(final, visit_id=VID) == final


def test_wire_budget_respects_4096_byte_body_cap():
    budget = vw.WireBudget(visit_id=VID, header=_HEADER)
    taken = "".join(budget.take("好" * 50) for _ in range(40))
    assert budget.exhausted
    assert len(taken.encode("utf-8")) <= VISIT_TEXT_MAX_BYTES
    assert len((taken + "好").encode("utf-8")) > VISIT_TEXT_MAX_BYTES


# ── Message schema and codec ───────────────────────────────────────────

def _sample_messages() -> list[dict]:
    return [
        {"t": "hello", "seq": 1, "ticket": "aaa.bbb",
         "caps": {"video": True, "tier": "sd600", "proto": VISIT_WIRE_PROTO,
                  "app_version": "0.7", "crop": "upper"},
         "lang": "zh-CN", "jti_reuse": False},
        {"t": "ready", "seq": 2},
        {"t": "ack", "seq": 9},
        {"t": "hb", "lp_seen": 33, "crop": "full", "hidden": False},
        {"t": "state", "hidden": True, "crop": "upper", "tier": "sd600", "enc": "vp9"},
        {"t": "wrap_up", "seq": 4, "lp": 50, "ph": "speaking", "ln": "g:3",
         "reason": "quiet", "initiated_by": "host"},
        {"t": "leave", "seq": 5, "last_seq": 4, "reason": "home"},
        _delta_msg("你好，", i=0),
        _delta_msg("世界。", i=1),
        _text_msg("你好，世界。", tail_ms=800, lang="zh-CN"),
        {"t": "line_abort", "ln": "g:7", "lp": 3, "i_done": 1, "reason": "human_interrupt"},
        {"t": "typing", "lp": 3, "sp": "c"},
        {"t": "stats", "rx_fps": 29.5, "rx_kbps": 540, "rtt_ms": 80, "loss_pct": 0.5,
         "rx_w": 320, "rx_h": 896, "qlr": "none"},
    ]


def test_every_message_type_round_trips_on_its_channel():
    for msg in _sample_messages():
        text = vw.encode_msg(msg)
        cmd = vw.cmd_of(msg["t"])
        out = vw.decode_msg(text, cmd=cmd)
        assert out["t"] == msg["t"] and out["v"] == 1
        for key, value in msg.items():
            assert out[key] == value
        assert vw.decode_msg(json.loads(text), cmd=cmd) == out
        assert vw.is_reliable(msg["t"]) == (msg["t"] in {"hello", "ready", "wrap_up", "leave", "text"})


def test_cmd_grouping_and_reliable_set():
    assert {t for t in vw._CMD_OF if vw.cmd_of(t) == 1} == {
        "hello", "ready", "ack", "hb", "state", "wrap_up", "leave"}
    assert {t for t in vw._CMD_OF if vw.cmd_of(t) == 2} == {"line_delta", "text", "line_abort"}
    assert {t for t in vw._CMD_OF if vw.cmd_of(t) == 3} == {"typing", "stats"}
    assert vw.RELIABLE_TYPES == {"hello", "ready", "wrap_up", "leave", "text"}
    with pytest.raises(ValueError):
        vw.cmd_of("ping")
    assert not vw.is_reliable("ping")


def test_decode_unknown_type_keeps_seq_and_ignores_unknown_fields():
    out = vw.decode_msg(json.dumps({"t": "fancy_new", "v": 1, "seq": 12, "x": [1]}), cmd=1)
    assert out == {"t": "_unknown", "raw_t": "fancy_new", "cmd": 1, "seq": 12}
    assert vw.decode_msg(json.dumps({"t": "lossy_new", "seq": 3}), cmd=3)["seq"] is None
    assert vw.decode_msg(json.dumps({"t": "no_seq"}), cmd=2)["seq"] is None
    with pytest.raises(ValueError):
        vw.decode_msg(json.dumps({"t": "fancy_new", "seq": -1}), cmd=1)
    with pytest.raises(ValueError):
        vw.decode_msg(json.dumps({"t": "fancy_new", "seq": 1}), cmd=4)
    known = vw.decode_msg(json.dumps({"t": "hb", "lp_seen": 1, "crop": "upper",
                                      "hidden": False, "future_field": 1}), cmd=1)
    assert "future_field" not in known


class _MiniSequencer:
    """In-order reliable consumer mirroring the InboxSequencer contract for this test."""

    def __init__(self) -> None:
        self.acked = 0
        self.buffer: dict[int, dict] = {}
        self.delivered: list[dict] = []

    def on_payload(self, payload: str, cmd: int) -> None:
        msg = vw.decode_msg(payload, cmd=cmd)
        seq = msg.get("seq")
        if seq is None:
            return
        self.buffer[seq] = msg
        while self.acked + 1 in self.buffer:
            item = self.buffer.pop(self.acked + 1)
            self.acked += 1
            if item["t"] not in ("_unknown", "_invalid"):
                self.delivered.append(item)


def test_unknown_reliable_type_is_consumed_as_noop_so_later_seq_flows():
    """An older client receiving a newer reliable type at seq=N must consume N as
    a no-op, ack N and process N+1. Mutation: dropping seq while decoding an
    unknown type leaves a permanent gap at N."""
    seqr = _MiniSequencer()
    seqr.on_payload(vw.encode_msg({"t": "ready", "seq": 1}), 1)
    seqr.on_payload(json.dumps({"t": "brand_new_reliable", "v": 1, "seq": 2, "blob": "x"}), 1)
    seqr.on_payload(vw.encode_msg(_text_msg("后面的台词。", seq=3)), 2)
    assert seqr.acked == 3
    assert [m["t"] for m in seqr.delivered] == ["ready", "text"]


def test_malformed_reliable_message_with_valid_seq_is_consumed_as_invalid():
    bad_text = dict(_text_msg("x", seq=4), ln="z:1")
    out = vw.decode_msg(json.dumps(bad_text), cmd=2)
    assert out["t"] == "_invalid" and out["seq"] == 4 and out["raw_t"] == "text"
    speaking_without_ln = {"t": "wrap_up", "seq": 6, "lp": 1, "ph": "speaking",
                           "reason": "quiet", "initiated_by": "guest"}
    assert vw.decode_msg(json.dumps(speaking_without_ln), cmd=1)["t"] == "_invalid"
    wrong_channel = vw.decode_msg(vw.encode_msg({"t": "ready", "seq": 8}), cmd=2)
    assert wrong_channel["t"] == "_invalid" and wrong_channel["seq"] == 8
    with pytest.raises(ValueError):    # lossy and malformed: plain drop
        vw.decode_msg(json.dumps({"t": "hb", "lp_seen": True, "crop": "upper", "hidden": False}))
    with pytest.raises(ValueError):
        vw.decode_msg(json.dumps({"t": "hb", "lp_seen": VISIT_LP_MAX + 1, "crop": "upper",
                                  "hidden": False}))
    with pytest.raises(ValueError):
        vw.decode_msg("not json")
    with pytest.raises(ValueError):
        vw.decode_msg("[1, 2]")


def test_schema_details():
    # i==0 must carry the line metadata, i>0 drops it
    with pytest.raises(ValueError):
        vw.encode_msg({"t": "line_delta", "ln": "g:1", "i": 0, "lp": 1, "txt": "a"})
    later = json.loads(vw.encode_msg(dict(_delta_msg("a", i=0), i=2)))
    assert "sp" not in later and "wu" not in later
    # wrap_up ln only travels with speaking
    begin = json.loads(vw.encode_msg({"t": "wrap_up", "seq": 1, "lp": 1, "ph": "begin", "ln": "h:2",
                                      "reason": "recall", "initiated_by": "guest"}))
    assert "ln" not in begin
    # leave tolerates values outside the known enum (receiver maps them to peer_left)
    assert vw.decode_msg(json.dumps({"t": "leave", "seq": 2, "last_seq": 1, "reason": "new_reason"}),
                         cmd=1)["reason"] == "new_reason"
    # bool is not an int, float is not an int
    with pytest.raises(ValueError):
        vw.encode_msg({"t": "ack", "seq": True})
    with pytest.raises(ValueError):
        vw.encode_msg({"t": "ack", "seq": 1.0})
    with pytest.raises(ValueError):
        vw.encode_msg({"t": "ack", "seq": U32_MAX + 1})
    with pytest.raises(ValueError):
        vw.encode_msg({"t": "nope"})
    with pytest.raises(ValueError):
        vw.encode_msg(_text_msg("a" * (VISIT_TEXT_MAX_BYTES + 1)))
    with pytest.raises(ValueError):
        vw.encode_msg({"t": "stats", "rx_fps": float("nan"), "rx_kbps": 1, "rtt_ms": 1,
                       "loss_pct": 0.0, "rx_w": 1, "rx_h": 1})


@pytest.mark.parametrize("tail", [-1, VISIT_CLAUSE_MAX_MS + 1, 10 ** 9, 1.5, "800", True])
def test_text_tail_ms_out_of_range_becomes_zero_with_soft_anomaly(tail):
    payload = json.dumps(dict(_text_msg("好。"), tail_ms=tail))
    out = vw.decode_msg(payload, cmd=2)
    assert out["t"] == "text" and out["tail_ms"] == 0
    assert out["_soft_anomalies"] == ["tail_ms"]
    ok = vw.decode_msg(json.dumps(dict(_text_msg("好。"), tail_ms=1200)), cmd=2)
    assert ok["tail_ms"] == 1200 and "_soft_anomalies" not in ok


def test_proto_compatible_and_mismatched_hello():
    assert vw.proto_compatible(1, {"proto": 1})
    assert not vw.proto_compatible(1, {"proto": 2})
    assert not vw.proto_compatible(1, {"proto": True})
    assert not vw.proto_compatible(1, {"proto": "1"})
    assert not vw.proto_compatible(1, {})
    assert not vw.proto_compatible(1, None)  # type: ignore[arg-type]
    future = {"t": "hello", "v": 2, "seq": 1, "caps": {"proto": VISIT_WIRE_PROTO + 1, "shape": "new"}}
    out = vw.decode_msg(json.dumps(future), cmd=1)
    assert out["t"] == "hello" and out["seq"] == 1
    assert not vw.proto_compatible(VISIT_WIRE_PROTO, out["caps"])


# ── Receive-side line_delta assembly ───────────────────────────────────

def test_line_delta_forward_jump_is_loss_not_violation():
    """i=1 lost, i=2..9 arrive: all land in place, the gap shows a mark, no anomaly,
    and the text final restores the full line. A repeated i, i=-1 and i=256 are
    each dropped with one anomaly. Mutation: treating a forward jump as a
    violation makes a 30-piece line with one lost piece pile up anomalies."""
    pieces = [f"第{k}片，" for k in range(10)]
    asm = vw.LineDeltaAssembler()
    assert asm.feed(vw.decode_msg(vw.encode_msg(_delta_msg(pieces[0], i=0)), cmd=2))
    for k in range(2, 10):
        assert asm.feed(vw.decode_msg(vw.encode_msg(_delta_msg(pieces[k], i=k)), cmd=2))
    assert asm.anomalies == 0
    assert asm.render("g:7") == pieces[0] + "…" + "".join(pieces[2:])
    assert not asm.feed(_delta_msg("重复", i=3))
    assert asm.anomalies == 1
    assert not asm.feed(_delta_msg("负", i=-1))
    assert asm.anomalies == 2
    assert not asm.feed(_delta_msg("越界", i=256))
    assert asm.anomalies == 3
    for bad_i in (-1, 256):
        with pytest.raises(ValueError):
            vw.decode_msg(json.dumps(_delta_msg("x", i=bad_i)), cmd=2)
    full = "".join(pieces)
    closed = asm.close(_text_msg(full, ln="g:7"))
    assert closed == full
    assert asm.render("g:7") == full
    assert not asm.feed(_delta_msg("迟到", i=1))
    assert asm.anomalies == 3

    long_line = vw.LineDeltaAssembler()
    for k in range(30):
        if k == 13:
            continue
        long_line.feed(_delta_msg(f"{k};", i=k, ln="h:2"))
    assert long_line.anomalies == 0


def test_line_delta_overlapping_lines_from_one_sender_count_as_anomaly():
    asm = vw.LineDeltaAssembler()
    assert asm.feed(_delta_msg("一", i=0, ln="g:7"))
    assert not asm.feed(_delta_msg("二", i=0, ln="g:9"))
    assert asm.anomalies == 1
    asm.close(_text_msg("一。", ln="g:7"))
    assert asm.feed(_delta_msg("二", i=0, ln="g:9"))
    asm.drop("g:9")
    assert asm.render("g:9") is None
    assert asm.feed(_delta_msg("三", i=0, ln="g:11"))


# ── split_clauses / ClauseSplitter ─────────────────────────────────────

def test_split_clauses_boundaries():
    assert vw.split_clauses("") == []
    assert vw.split_clauses("你好。今天天气不错！走吧") == ["你好。", "今天天气不错！", "走吧"]
    assert vw.split_clauses("Hello there. How are you?") == ["Hello there. ", "How are you?"]
    assert vw.split_clauses("圆周率是3.14左右。") == ["圆周率是3.14左右。"]
    assert vw.split_clauses("真的吗？！」好吧。") == ["真的吗？！」", "好吧。"]
    assert vw.split_clauses("第一行\n第二行") == ["第一行\n", "第二行"]
    # comma split only once 24 CJK characters have accumulated
    c23, c24 = "好" * 23, "好" * 24
    assert vw.split_clauses(c23 + "，后面。") == [c23 + "，后面。"]
    assert vw.split_clauses(c24 + "，后面。") == [c24 + "，", "后面。"]
    w11 = " ".join(["word"] * 11)
    w12 = " ".join(["word"] * 12)
    assert vw.split_clauses(w11 + ", tail.") == [w11 + ", tail."]
    assert vw.split_clauses(w12 + ", tail.") == [w12 + ", ", "tail."]
    # fragments shorter than 2 characters merge into the next clause
    assert vw.split_clauses("。你好。") == ["。你好。"]
    assert vw.split_clauses("！好的。嗯") == ["！好的。", "嗯"]
    assert vw.split_clauses("\n\n好的。") == ["\n\n好的。"]


def test_split_clauses_hard_cap_never_cuts_codepoints_or_clusters():
    family = "👨\u200d👩\u200d👧"
    flags = "🇨🇳"
    for unit in (family, flags, "🐱", "Ж", "好", "e\u0301"):
        line = unit * 400
        clauses = vw.split_clauses(line)
        assert len(clauses) >= 2
        assert "".join(clauses) == line
        for c in clauses:
            assert len(c.encode("utf-8")) <= VISIT_DELTA_TEXT_MAX_BYTES
            assert vw.line_delta_encoded_len(c) <= VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES
            assert re.fullmatch(f"(?:{re.escape(unit)})+", c), (unit, c[:8])


def test_clause_splitter_any_chunking_matches_whole_line():
    rng = random.Random(31337)
    for k in range(250):
        line = _rand_text(rng, rng.randint(1, 600), punct=bool(k % 4))
        expected = vw.split_clauses(line)
        for holdback in (0, 4):
            splitter = vw.ClauseSplitter(holdback_chars=holdback)
            got = []
            pos = 0
            while pos < len(line):
                step = rng.randint(1, 25)
                got.extend(splitter.feed(line[pos:pos + step]))
                pos += step
            got.extend(splitter.flush())
            assert "".join(c.text for c in got) == line
            assert "".join(c.raw for c in got) == line
            if holdback == 0:
                assert [c.text for c in got] == expected
            else:
                whole = vw.ClauseSplitter(holdback_chars=holdback)
                assert [c.text for c in got] == [c.text for c in whole.feed(line) + whole.flush()]


def _filler_for_cut_inside(name: str, chars_before_cut: int) -> str:
    """CJK filler without punctuation such that the encoded cap is hit right after
    ``name[:chars_before_cut]`` (the next name character overflows)."""
    for m in range(1, 400):
        filler = "啊" * m
        fits = vw.line_delta_encoded_len(filler + name[:chars_before_cut])
        over = vw.line_delta_encoded_len(filler + name[:chars_before_cut + 1])
        if fits <= VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES < over:
            return filler
    raise AssertionError("no filler found")


def test_protected_name_across_hard_cut_fed_char_by_char_is_never_released():
    """The hard cut point falls inside a protected name that arrives one character
    at a time: with holdback the released clauses never contain any part of the
    name. Mutation: ignoring holdback releases the first two name characters."""
    name, label = "小林由纪子", "家人"
    filler = _filler_for_cut_inside(name, 2)
    splitter = vw.ClauseSplitter(redact=_span_redactor(name, label), holdback_chars=len(name) - 1)
    out = splitter.feed(filler)
    for ch in name:
        out += splitter.feed(ch)
    out += splitter.feed("来了。")
    out += splitter.flush()
    texts = [c.text for c in out]
    assert len(texts) >= 2
    assert all(ch not in t for t in texts for ch in name)
    assert "".join(texts) == filler + label + "来了。"
    assert "".join(c.raw for c in out) == filler + name + "来了。"


def test_redaction_runs_on_the_buffer_not_per_clause():
    """The name arrives whole but the encoded cap falls inside it: redacting the
    accumulated buffer before cutting keeps the name out of every clause.
    Mutation: redacting each released clause separately leaks a split name."""
    name, label = "小林由纪子", "那位很重要的家里人在这儿"
    filler = _filler_for_cut_inside(name, 2)
    splitter = vw.ClauseSplitter(redact=_span_redactor(name, label), holdback_chars=0)
    out = splitter.feed(filler)
    out += splitter.feed(name + "来了。")
    out += splitter.flush()
    texts = [c.text for c in out]
    assert len(texts) >= 2
    joined = "".join(texts)
    assert name not in joined and label in joined
    assert all(ch not in t for t in texts for ch in name)


def test_holdback_tail_is_released_on_flush():
    line = "啊" * 300
    plain = vw.ClauseSplitter()
    held = vw.ClauseSplitter(holdback_chars=10)
    first_plain = plain.feed(line)
    first_held = held.feed(line)
    assert len(first_plain) == len(first_held) == 1
    assert len(first_held[0].text) == len(first_plain[0].text) - 10
    rest = held.flush()
    assert first_held[0].text + "".join(c.text for c in rest) == line
    assert rest[0].text.startswith("啊" * 10)
    with pytest.raises(RuntimeError):
        held.feed("more")


def test_clause_raw_tracks_original_for_speech_estimates():
    """A 5-character name redacted to a 2-character label: the clause keeps the
    original slice in raw and release offsets are estimated from it. Mutation:
    estimating from the redacted text releases later subtitles too early."""
    name, label = "小林由纪子", "家人"
    line = name + "来了。今天我们一起去公园玩吧。"
    splitter = vw.ClauseSplitter(redact=_span_redactor(name, label), holdback_chars=len(name) - 1)
    clauses = []
    for ch in line:
        clauses += splitter.feed(ch)
    clauses += splitter.flush()
    assert [c.text for c in clauses] == [label + "来了。", "今天我们一起去公园玩吧。"]
    assert clauses[0].raw == name + "来了。"
    assert "".join(c.raw for c in clauses) == line
    offsets = vw.clause_release_offsets_ms(clauses)
    assert offsets[0] == 0
    assert offsets[1] == vw.estimate_speech_ms(name + "来了。") == 5 * 180 + 2 * 180 + 250
    assert offsets[1] > vw.estimate_speech_ms(label + "来了。")


def test_plain_string_redact_falls_back_to_derived_spans():
    splitter = vw.ClauseSplitter(redact=lambda s: s.replace("小林由纪子", "家人"), holdback_chars=4)
    out = splitter.feed("小林由纪子来了。你好。") + splitter.flush()
    assert [c.text for c in out] == ["家人来了。", "你好。"]
    assert out[0].raw == "小林由纪子来了。"


def test_redact_spans_are_validated():
    def bad(raw: str):
        return raw + "x", [(0, 0, 0, 0)]
    splitter = vw.ClauseSplitter(redact=bad)
    with pytest.raises(ValueError):
        splitter.feed("你好。再见。")


# ── Speech estimate and rate budget ────────────────────────────────────

def test_estimate_speech_ms():
    assert vw.estimate_speech_ms("你好世界") == 4 * 180
    assert vw.estimate_speech_ms("hello brave new world") == 4 * 250
    assert vw.estimate_speech_ms("你好 world。") == 2 * 180 + 250 + 250
    assert vw.estimate_speech_ms("Привет мир, друг") == 3 * 250 + 120
    assert vw.estimate_speech_ms("好，好、好。") == 3 * 180 + 2 * 120 + 250
    assert vw.estimate_speech_ms("Hi!? Wait……") == 2 * 250 + 250 + 250
    assert vw.estimate_speech_ms("3.14 and 1,000 don't") == 4 * 250
    assert vw.estimate_speech_ms("。。。") == 400        # only punctuation, clamped up
    assert vw.estimate_speech_ms("") == 400
    assert vw.estimate_speech_ms("好") == 400
    assert vw.estimate_speech_ms("好" * 200) == 12000    # clamped down


def test_max_worst_case_rates_stay_under_vendor_limits():
    """Paper bound of one side, outbound.

    messages/s = line_delta 1000/250 (4) + text 1 line/s x 5 pieces plus one
    5-piece resend (10) + ack 1000/500 (2) + typing 1 + hb 1/5 (0.2) + stats
    1/5 (0.2) = 17.4 <= 30 (TRTC) and <= 20 (message bucket).
    bytes/s demand = 14 pieces x 1000 B + ack 160 + typing 60 + hb 20 + stats 32
    ~= 14.3 KB on paper, but the 5 KB/s byte bucket flattens it, so the
    outbound rate is 5 KB/s <= 8 KB/s (TRTC).
    """
    msgs, kbs = vw.max_worst_case_rates()
    assert msgs <= 30 and msgs <= 20
    assert kbs <= 8
    assert msgs == pytest.approx(17.4)
    assert kbs == pytest.approx(5.0)


def test_unknown_trunc_reason_is_forward_compatible():
    """A newer peer's truncation reason keeps the reliable ``text`` usable; only
    an oversized reason makes it ``_invalid`` (seq still consumed)."""
    msg = _text_msg("hi", truncated=True, trunc_reason="future_reason")
    out = vw.decode_msg(vw.encode_msg(msg), cmd=2)
    assert out["t"] == "text" and out["trunc_reason"] == "future_reason"
    too_long = dict(msg, trunc_reason="x" * 33)
    bad = vw.decode_msg(json.dumps(too_long), cmd=2)
    assert bad["t"] == "_invalid" and bad["seq"] == msg["seq"]


def test_inbound_delta_is_measured_in_its_escaped_wire_form():
    """A peer delta whose compact JSON fits 900 B but whose envelope-escaped form
    does not is rejected, so every accepted delta really is one piece."""
    msg = _delta_msg('"' * 380, i=1)
    compact = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    assert len(compact.encode("utf-8")) <= 900
    with pytest.raises(ValueError):
        vw.decode_msg(compact, cmd=2)
    ok = _delta_msg("好" * 200, i=1)
    assert vw.decode_msg(json.dumps(ok, ensure_ascii=False), cmd=2)["t"] == "line_delta"


def test_newer_line_piece_overtaking_a_delayed_text_is_not_overlap():
    """The previous line's reliable ``text`` may still wait behind a ``seq`` gap
    when the next line's first lossy piece arrives: a larger ``lp`` retires the
    open line instead of counting an anomaly; an equal ``lp`` still does."""
    asm = vw.LineDeltaAssembler()
    assert asm.feed(_delta_msg("旧行", ln="g:1", lp=3))
    assert asm.feed(_delta_msg("新行", ln="g:2", lp=5))
    assert asm.anomalies == 0
    assert asm.render("g:1") == "旧行"
    closed = asm.close(_text_msg("旧行全文", ln="g:1"))
    assert closed == "旧行全文"
    assert asm.feed(_delta_msg("续", i=1, ln="g:2", lp=5))
    assert asm.render("g:2") == "新行续"
    assert not asm.feed(_delta_msg("同 lp", ln="g:3", lp=5))
    assert asm.anomalies == 1


def test_late_pieces_of_a_retired_line_still_land():
    """Once a newer line retired the open one, the old line's late pieces are
    placed normally (no anomaly) and do not take the open slot back."""
    asm = vw.LineDeltaAssembler()
    assert asm.feed(_delta_msg("旧一", ln="g:1", lp=3))
    assert asm.feed(_delta_msg("新一", ln="g:2", lp=5))
    assert asm.feed(_delta_msg("旧二", i=1, ln="g:1", lp=3))
    assert asm.render("g:1") == "旧一旧二"
    assert asm.anomalies == 0
    # 打开位仍是新行（lp=5）：lp 介于两者之间的第三行依然算交叠
    assert not asm.feed(_delta_msg("插", ln="g:3", lp=4))
    assert asm.anomalies == 1
    assert asm.feed(_delta_msg("新二", i=1, ln="g:2", lp=5))
    assert asm.render("g:2") == "新一新二"


def test_unclosed_retired_lines_are_bounded():
    """A peer that only ever sends first pieces cannot grow the assembler forever."""
    asm = vw.LineDeltaAssembler()
    for n in range(1, 400):
        assert asm.feed(_delta_msg("x", ln=f"g:{n}", lp=n))
    assert len(asm._lines) <= VISIT_REORDER_BUFFER_MAX + 1
    assert asm.render("g:399") == "x"
    assert asm.anomalies == 0


def test_leave_watermark_must_be_the_previous_seq():
    """A low ``last_seq`` would let the receiver skip the gap wait; such a leave
    is consumed as ``_invalid`` (its ``seq`` still advances the window)."""
    ok = {"t": "leave", "v": 1, "seq": 10, "last_seq": 9, "reason": "home"}
    assert vw.decode_msg(json.dumps(ok), cmd=1)["t"] == "leave"
    low = dict(ok, last_seq=3)
    bad = vw.decode_msg(json.dumps(low), cmd=1)
    assert bad["t"] == "_invalid" and bad["seq"] == 10

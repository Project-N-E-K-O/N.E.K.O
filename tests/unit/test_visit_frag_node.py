"""The transport page's fragment / reassembly is byte-dual to ``utils/visit_wire``."""

from __future__ import annotations

import json
import random

from tests.visit_js_harness import VISIT_ID, run_visit_js
from utils.visit_wire import Reassembler, fragment

_ALPHABETS = (
    "abcdefghijklmnopqrstuvwxyz 0123456789",
    "猫娘串门你好今天天气不错我们去散步吧。，！？",
    "привет как дела у тебя сегодня",
    "\U0001f431\U0001f408‍⬛\U0001f1ef\U0001f1f5❤️",
    '"\\\n\t\x01/<>&',
)


def _random_payloads(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        alphabet = "".join(rng.sample(_ALPHABETS, k=rng.randint(1, len(_ALPHABETS))))
        size = rng.choice((0, 1, 5, 40, 300, 900, 2000, 4096))
        txt = "".join(rng.choice(alphabet) for _ in range(size))
        payload = {"t": "text", "v": 1, "ln": f"g:{i}", "lp": rng.randint(0, 10**6), "seq": i + 1, "txt": txt}
        out.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return out


def test_js_fragment_matches_python_bytes_and_reassembles():
    payloads = _random_payloads(1000, seed=20261012)
    expected = [[p.decode("utf-8") for p in fragment(text, visit_id=VISIT_ID, msg_id=i)] for i, text in enumerate(payloads)]
    out = run_visit_js(
        f"""
        const ctx = makeContext();
        load(ctx, FILES);
        const NVT = ctx.NekoVisitTransport;
        const payloads = {json.dumps(payloads, ensure_ascii=False)};
        const pieces = payloads.map((p, i) => NVT.fragment(p, {{ visitId: VISIT_ID, msgId: i }}));
        const maxBytes = Math.max(...pieces.flat().map((t) => Buffer.byteLength(t, 'utf8')));
        const r = new NVT.Reassembler({{ visitId: VISIT_ID }});
        let roundTrip = 0;
        let eligible = 0;
        pieces.forEach((list, i) => {{
          // 超过 8 片的消息按规则整条丢弃（后端发送前已保证 ≤8 片）
          if (list.length > 8) return;
          eligible++;
          // 乱序到达也能重组
          const order = list.map((_, k) => k).reverse();
          let got = null;
          for (const k of order) got = r.feed('g_peer', new TextEncoder().encode(list[k]), 0) || got;
          if (got && JSON.stringify(got) === JSON.stringify(JSON.parse(payloads[i]))) roundTrip++;
        }});
        return {{ pieces, maxBytes, roundTrip, eligible, dropped: r.dropped }};
        """
    )
    assert out["pieces"] == expected
    assert out["maxBytes"] <= 1000
    assert out["eligible"] > 800
    assert out["roundTrip"] == out["eligible"]
    assert out["dropped"] == 0


def test_python_reassembles_js_pieces_and_js_reassembles_python_pieces():
    text = json.dumps({"t": "text", "v": 1, "txt": "喵" * 1500 + '"\\' * 30}, ensure_ascii=False, separators=(",", ":"))
    py_pieces = [p.decode("utf-8") for p in fragment(text, visit_id=VISIT_ID, msg_id=7)]
    out = run_visit_js(
        f"""
        const ctx = makeContext();
        load(ctx, FILES);
        const NVT = ctx.NekoVisitTransport;
        const r = new NVT.Reassembler({{ visitId: VISIT_ID }});
        let got = null;
        for (const p of {json.dumps(py_pieces, ensure_ascii=False)}) got = r.feed('h_peer', p, 0) || got;
        return {{ got, js: NVT.fragment({json.dumps(text, ensure_ascii=False)}, {{ visitId: VISIT_ID, msgId: 7 }}) }};
        """
    )
    assert out["got"] == json.loads(text)
    r = Reassembler(visit_id=VISIT_ID)
    got = None
    for piece in out["js"]:
        got = r.feed("g_peer", piece.encode("utf-8"), 0.0) or got
    assert got == json.loads(text)


def test_unknown_type_passes_through_and_foreign_visit_is_dropped():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const NVT = ctx.NekoVisitTransport;
        const recv = [];
        const core = new NVT.TransportCore({ visitId: VISIT_ID, onRecv: (m) => recv.push(m) });
        const mine = NVT.fragment(JSON.stringify({ t: 'future_kind', v: 9, x: 1 }), { visitId: VISIT_ID, msgId: 1 })[0];
        const other = NVT.fragment(JSON.stringify({ t: 'text', v: 1 }), { visitId: 'ZZZZZZZZ' + VISIT_ID.slice(8), msgId: 2 })[0];
        core.onPiece('h_peer', 1, new TextEncoder().encode(mine));
        core.onPiece('h_peer', 2, new TextEncoder().encode(other));
        core.onPiece('h_peer', 9, new TextEncoder().encode(mine));
        return { recv, dropped: core.reassembler.dropped, badCmd: core.droppedUnknownCmd };
        """
    )
    assert out["recv"] == [{"from_vid": "h_peer", "cmd": 1, "payload": {"t": "future_kind", "v": 9, "x": 1}}]
    assert out["dropped"] == 1
    assert out["badCmd"] == 1


def test_reassembly_times_out_after_six_seconds():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const NVT = ctx.NekoVisitTransport;
        const pieces = NVT.fragment(JSON.stringify({ t: 'text', txt: 'x'.repeat(3000) }), { visitId: VISIT_ID, msgId: 3 });
        const r = new NVT.Reassembler({ visitId: VISIT_ID });
        r.feed('g', pieces[0], 0);
        for (let k = 1; k < pieces.length; k++) r.feed('g', pieces[k], 6001);
        return { n: pieces.length, timeouts: r.timeouts, pending: r.pending() };
        """
    )
    assert out["n"] >= 3
    assert out["timeouts"] == 1
    assert out["pending"] == 1  # the later pieces opened a fresh entry

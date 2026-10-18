"""Congestion ladder of the transport page (design §3.4.6): resolution only, never fps."""

from __future__ import annotations

import json

import pytest

from config.visit_settings import (
    VISIT_CONGESTION_LADDER,
    VISIT_CONGESTION_MIN_KBPS,
    VISIT_CONGESTION_RECOVER_S,
    VISIT_CONGESTION_TRIGGER_S,
)
from tests.visit_js_harness import run_visit_js


@pytest.mark.parametrize("crop", ["upper", "full"])
def test_ladder_steps_down_after_ten_bad_seconds_and_up_after_thirty_clean(crop):
    out = run_visit_js(
        f"""
        const ctx = makeContext();
        load(ctx, FILES);
        const P = ctx.NekoVisitTransport.pack;
        const L = new P.LadderController({{ crop: {json.dumps(crop)}, targetFps: 30 }});
        const events = [];
        let t = 0;
        const step = (rxFps, loss) => {{ const lv = L.feed({{ nowMs: t, rxFps, lossPct: loss }}); if (lv !== null) events.push([t, lv]); t += 5000; }};
        for (let k = 0; k < 12; k++) step(20, 0);      // 60 s 持续拥塞
        for (let k = 0; k < 20; k++) step(30, 0);      // 100 s 干净
        const dims = [0, 1, 2].map((lv) => P.packDims({json.dumps(crop)}, lv));
        return {{ events, dims }};
        """
    )
    events = out["events"]
    trigger_ms = VISIT_CONGESTION_TRIGGER_S * 1000
    recover_ms = VISIT_CONGESTION_RECOVER_S * 1000
    assert events[0] == [trigger_ms, 1]
    assert events[1] == [2 * trigger_ms, 2]
    downs = [e for e in events if e[1] == 2]
    assert len(downs) == 1  # Do not lower the lowest tier further.
    ups = [e for e in events if e[0] >= 60000]
    assert ups[0] == [60000 + recover_ms, 1]
    assert ups[1] == [60000 + 2 * recover_ms, 0]
    expected = VISIT_CONGESTION_LADDER[crop]
    assert [(d["cropW"], d["cropH"], d["kbps"]) for d in out["dims"]] == list(expected)
    assert out["dims"][2]["kbps"] == VISIT_CONGESTION_MIN_KBPS


def test_ladder_threshold_follows_the_capture_rate_and_uplink_loss():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const P = ctx.NekoVisitTransport.pack;
        const run = (target, rx, loss) => {
          const L = new P.LadderController({ crop: 'upper', targetFps: target });
          let lv = null;
          for (let t = 0; t <= 10000; t += 5000) lv = L.feed({ nowMs: t, rxFps: rx, lossPct: loss }) ?? lv;
          return lv;
        };
        return { compat15: run(15, 14.5, 0), compatSlow: run(15, 11, 0), direct25: run(30, 25, 0), direct23: run(30, 23, 0), loss: run(30, 30, 20) };
        """
    )
    # Compatibility mode sends at 15 fps: a peer at 14.5 fps is not congested (threshold 0.8 x 15 = 12).
    assert out["compat15"] is None
    assert out["compatSlow"] == 1
    assert out["direct25"] is None
    assert out["direct23"] == 1
    assert out["loss"] == 1


def test_controller_steps_the_ladder_from_peer_stats_in_place():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.startStats();
        h.ws.serverSend({ type: 'media', publish: true, crop: 'full', ladder: 0 });
        h.trtc.emit('CONNECTION_STATE_CHANGED', { prevState: 'CONNECTING', state: 'CONNECTED' });
        await settle();
        const pack = h.c.packer.pack;
        const peerStats = (rx) => {
          const pieces = h.NVT.fragment(JSON.stringify({ t: 'stats', v: 1, rx_fps: rx }), { visitId: VISIT_ID, msgId: Math.floor(Math.random() * 1e6) });
          h.trtc.emit('CUSTOM_MESSAGE', { userId: 'h_peer', cmdId: 3, data: new TextEncoder().encode(pieces[0]).buffer });
        };
        for (let k = 0; k < 4; k++) { peerStats(10); await advance(5000); }
        return { size: [pack.width, pack.height], same: h.c.packer.pack === pack,
                 updates: h.log.filter((e) => e[0] === 'updateLocalVideo').map((e) => [e[1].width, e[1].height, e[1].bitrate]) };
        """
    )
    assert out["same"]
    assert out["updates"][0] == [208, 896, 400]
    assert out["size"] in ([208, 896], [160, 704])


def test_congestion_level_survives_reconnect_and_reload():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const store = new Map();
        h.ctx.sessionStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'full', ladder: 0 });
        await settle();
        h.c.setGeometry('full', 1);                       // 拥塞阶梯降了一级
        // 传输 WS 重连：首发凭证 + 后端快照（ladder 恒 0）
        h.ws.serverClose(1006);
        await advance(1100);
        const ws2 = h.sockets[h.sockets.length - 1];
        ws2.serverOpen();
        ws2.serverSend(credsFor('guest', { crop: 'full' }));
        await settle();
        ws2.serverSend({ type: 'media', publish: true, crop: 'full', ladder: 0 });
        await settle();
        const afterReconnect = [h.c.media.ladder, h.c.packer.pack.width, h.c.packer.pack.height];
        // Pet 页重载（同一标签页）：新控制器从 sessionStorage 接着用
        const c2 = new h.NVT.loader.VisitTransportController({ window: h.ctx, document: h.ctx.document, side: 'guest',
          visitId: VISIT_ID, post: () => {}, log: () => {}, loadSdk: () => Promise.resolve(h.TRTC) });
        c2.preflight = { ok: true };
        c2.onCredentials(credsFor('guest', { crop: 'full' }));
        const afterReload = [c2.media.ladder, c2.packer.pack.width];
        // 换构图从 0 级开始
        c2.onCredentials(credsFor('guest', { crop: 'upper' }));
        return { afterReconnect, afterReload, otherCrop: c2.media.ladder };
        """
    )
    assert out["afterReconnect"] == [1, 208, 896]
    assert out["afterReload"] == [1, 208]
    assert out["otherCrop"] == 0


def test_duplicate_publish_snapshots_while_pending_publish_once():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let finish;
        const orig = h.trtc.startLocalVideo;
        h.trtc.startLocalVideo = (c) => { h.log.push(['start-pending']); return new Promise((r) => { finish = () => r(orig(c)); }); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        finish();
        await settle();
        await settle();
        return kinds(h.log).filter((k) => k.indexOf('LocalVideo') !== -1 || k === 'start-pending');
        """
    )
    # The second snapshot runs after the first; seeing the same track published, it neither calls the SDK again nor unpublishes it.
    assert out == ["start-pending", "startLocalVideo"]


def test_no_fresh_telemetry_neither_steps_up_nor_down():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const L = new ctx.NekoVisitTransport.pack.LadderController({ crop: 'upper', level: 2, targetFps: 30 });
        const events = [];
        for (let t = 0; t <= 60000; t += 5000) { const lv = L.feed({ nowMs: t }); if (lv !== null) events.push(lv); }
        // 有样本后才开始计「干净」
        for (let t = 65000; t <= 95000; t += 5000) { const lv = L.feed({ nowMs: t, rxFps: 30 }); if (lv !== null) events.push([t, lv]); }
        return events;
        """
    )
    assert out == [[95000, 1]]

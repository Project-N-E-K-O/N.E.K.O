"""Packing geometry of the transport page follows the framing, never hard-coded sizes."""

from __future__ import annotations

from config.visit_settings import VISIT_CONGESTION_LADDER, VISIT_TIERS
from tests.visit_js_harness import run_visit_js


def test_dims_and_profile_for_both_framings():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const P = ctx.NekoVisitTransport.pack;
        const upper = P.packDims('upper', 0), full = P.packDims('full', 0);
        return { upper, full, pu: P.profileFor(upper, 30), pf: P.profileFor(full, 15),
                 geo: P.CROP_GEOMETRY, ladder: P.CONGESTION_LADDER,
                 un1: P.unpackGeometry(320, 896), un2: P.unpackGeometry(256, 1120), un3: P.unpackGeometry(240, 672) };
        """
    )
    assert (out["upper"]["cropW"], out["upper"]["cropH"], out["upper"]["packW"], out["upper"]["packH"]) == (320, 448, 320, 896)
    assert (out["full"]["cropW"], out["full"]["cropH"], out["full"]["packW"], out["full"]["packH"]) == (256, 560, 256, 1120)
    assert out["pu"] == {"width": 320, "height": 896, "frameRate": 30, "bitrate": 560}
    assert out["pf"] == {"width": 256, "height": 1120, "frameRate": 15, "bitrate": 560}
    sd = VISIT_TIERS["sd600"]
    assert (out["geo"]["upper"]["w"], out["geo"]["upper"]["h"]) == sd["crop_upper"]
    assert (out["geo"]["full"]["w"], out["geo"]["full"]["h"]) == sd["crop_full"]
    assert {k: [tuple(s) for s in v] for k, v in out["ladder"].items()} == {k: list(v) for k, v in VISIT_CONGESTION_LADDER.items()}
    assert out["un1"]["splitY"] == 448 and abs(out["un1"]["aspect"] - 320 / 448) < 1e-9
    assert out["un2"]["splitY"] == 560 and abs(out["un2"]["aspect"] - 256 / 560) < 1e-9
    # Still derive from videoHeight/2 after libwebrtc reduces the resolution itself.
    assert out["un3"]["splitY"] == 336


def test_switching_framing_resizes_the_same_canvas_and_keeps_the_track():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const pack = h.c.packer.pack, track = h.c.packer.track;
        const src = h.ctx.document.createElement('canvas');
        const frames = [];
        const feed = () => { h.c.packer.onFrame(src, { x: 0, y: 0, w: 100, h: 100 }); frames.push(track.frames); };
        feed();
        const sizes = [[pack.width, pack.height]];
        for (const crop of ['full', 'upper']) {
          h.ws.serverSend({ type: 'media', publish: true, crop, ladder: 0 });
          await settle();
          feed();
          sizes.push([pack.width, pack.height]);
        }
        return { sameCanvas: h.c.packer.pack === pack, sameTrack: h.c.packer.track === track, streams: pack.streams,
                 sizes, frames, updates: h.log.filter((e) => e[0] === 'updateLocalVideo').map((e) => e[1]),
                 starts: h.log.filter((e) => e[0] === 'startLocalVideo').length,
                 geometry: h.posted.filter((m) => m.t === 'geometry').map((m) => m.crop) };
        """
    )
    assert out["sameCanvas"] and out["sameTrack"] and out["streams"] == 1
    assert out["sizes"] == [[320, 896], [256, 1120], [320, 896]]
    assert out["frames"] == [1, 2, 3]
    assert [(u["width"], u["height"]) for u in out["updates"]] == [(256, 1120), (320, 896)]
    assert out["starts"] == 1
    assert out["geometry"][-2:] == ["full", "upper"]


def test_packer_only_draws_while_publishing():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const src = h.ctx.document.createElement('canvas');
        const before = h.c.packer.onFrame(src, { x: 0, y: 0, w: 10, h: 10 });
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const after = h.c.packer.onFrame(src, { x: 0, y: 0, w: 10, h: 10 });
        const ops = h.c.packer.sctx.calls.map((c) => c[0]);
        return { before, after, ops, mode: h.c.packer.sctx.globalCompositeOperation };
        """
    )
    assert out["before"] is False and out["after"] is True
    assert out["ops"] == ["clearRect", "fillRect", "drawImage"]
    assert out["mode"] == "source-over"


def test_out_of_source_crop_is_clipped_not_painted_white():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const P = ctx.NekoVisitTransport.pack;
        const src = { width: 1000, height: 800 };
        return {
          inside: P.clipToSource(src, 100, 100, 320, 448, 320, 448),
          right: P.clipToSource(src, 900, 0, 200, 400, 320, 640),
          outside: P.clipToSource(src, 2000, 0, 100, 100, 320, 448),
        };
        """
    )
    assert out["inside"] == {"sx": 100, "sy": 100, "sw": 320, "sh": 448, "dx": 0, "dy": 0, "dw": 320, "dh": 448}
    # Right half out of bounds: draw only the left half and halve the target width proportionally.
    assert out["right"] == {"sx": 900, "sy": 0, "sw": 100, "sh": 400, "dx": 0, "dy": 0, "dw": 160, "dh": 640}
    assert out["outside"] is None


def test_guest_tells_the_parent_when_it_publishes():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        h.ws.serverSend({ type: 'media', publish: false });
        await settle();
        return h.posted.filter((m) => m.t === 'publish').map((m) => m.on);
        """
    )
    assert out == [True, False]


def test_clip_to_source_honours_the_visible_region():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const P = ctx.NekoVisitTransport.pack;
        const src = { naturalWidth: 1000, naturalHeight: 2000 };
        return {
          plain: P.clipToSource(src, -100, 0, 600, 800, 300, 400),
          visible: P.clipToSource(src, -100, 0, 600, 800, 300, 400, { x: 0, y: 0, w: 200, h: 2000 }),
          none: P.clipToSource(src, 400, 0, 100, 100, 300, 400, { x: 0, y: 0, w: 200, h: 2000 }),
        };
        """
    )
    assert out["plain"] == {"sx": 0, "sy": 0, "sw": 500, "sh": 800, "dx": 50, "dy": 0, "dw": 250, "dh": 400}
    assert out["visible"] == {"sx": 0, "sy": 0, "sw": 200, "sh": 800, "dx": 50, "dy": 0, "dw": 100, "dh": 400}
    assert out["none"] is None


def test_packer_draws_only_the_visible_source_region():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const pk = new ctx.NekoVisitTransport.pack.Packer({ document: ctx.document, crop: 'upper' });
        pk.active = true;
        const draws = [];
        pk.pctx.drawImage = (...a) => { if (a.length === 9) draws.push(a.slice(1, 5)); };
        const src = { naturalWidth: 1000, naturalHeight: 2000 };
        pk.onFrame(src, { x: -100, y: 0, w: 600, h: 840, visible: { x: 0, y: 0, w: 200, h: 2000 } });
        pk.onFrame(src, { x: -100, y: 0, w: 600, h: 840 });
        return draws;
        """
    )
    assert out == [[0, 0, 200, 840], [0, 0, 500, 840]]

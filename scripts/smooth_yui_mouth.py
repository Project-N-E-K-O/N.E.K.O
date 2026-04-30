"""Smooth ParamMouthForm / ParamMouthOpenY in static/yui-origin motion files.

The artist keyframes for the mouth params are too jittery for our use case
(no lipsync, just background animation), causing visible flicker. This script
applies an aggressive low-pass filter and forces start/end of every motion
back to the neutral pose (0).

Pipeline per curve:
  1. Extract artist keyframes (Bezier endpoints) from Segments.
  2. Resample at SAMPLE_HZ on a uniform time grid (raw signal).
  3. Convolve with Gaussian of width SIGMA_S; clamp at boundaries (no special
     loop handling — start/end are forced to neutral instead).
  4. At OUT_HZ, emit Linear segments. Apply a smoothstep fade window of FADE_S
     seconds at each end to ramp the smoothed value to/from `neutral`.
  5. Pin the very first and last keyframe to neutral exactly.

Meta.CurveCount / TotalSegmentCount / TotalPointCount are recomputed using the
formula validated against the original files: per-curve points =
4 + 3 * bezier_count + 1 * (linear|stepped|inverse_stepped)_count.

Usage: `uv run python scripts/smooth_yui_mouth.py`
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "static" / "yui-origin"

TARGET_IDS = ("ParamMouthForm", "ParamMouthOpenY")
NEUTRAL = {"ParamMouthForm": 0.0, "ParamMouthOpenY": 0.0}
SIGMA_S = 3.0       # Gaussian width
SAMPLE_HZ = 60      # internal sampling rate for smoothing
OUT_HZ = 2          # Linear-segment output rate
FADE_S = 1.0        # boundary fade window (smoothstep)

PAY = {0: 2, 1: 6, 2: 2, 3: 2}
POINTS_PER_CMD = {0: 1, 1: 3, 2: 1, 3: 1}


def extract_keys(seg):
    keys = [(seg[0], seg[1])]
    i = 2
    while i < len(seg):
        cmd = int(seg[i])
        i += 1
        if cmd == 1:
            keys.append((seg[i + 4], seg[i + 5]))
        else:
            keys.append((seg[i], seg[i + 1]))
        i += PAY[cmd]
    return keys


def piecewise_linear(keys, t):
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    lo, hi = 0, len(keys) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if keys[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    t0, v0 = keys[lo]
    t1, v1 = keys[lo + 1]
    if t1 == t0:
        return v0
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def gaussian_kernel(sigma_samples):
    radius = max(1, int(math.ceil(3 * sigma_samples)))
    xs = range(-radius, radius + 1)
    ws = [math.exp(-0.5 * (x / sigma_samples) ** 2) for x in xs]
    s = sum(ws)
    return [w / s for w in ws], radius


def fade_weight(t, duration, fade_s):
    """1 in the middle, 0 at the edges, smoothstep transition."""
    if fade_s <= 0:
        return 1.0
    if t <= 0 or t >= duration:
        return 0.0
    if t < fade_s:
        x = t / fade_s
    elif t > duration - fade_s:
        x = (duration - t) / fade_s
    else:
        return 1.0
    return x * x * (3 - 2 * x)


def smooth_curve(keys, neutral):
    if len(keys) < 2:
        return keys
    t_start, _ = keys[0]
    t_end, _ = keys[-1]
    duration = t_end - t_start
    if duration <= 0:
        # 退化时间轴（首尾时间相同）：直接钉回中性位，避免 dt=0 触发除零
        # 让整批 motion 处理中断。
        return [(t_start, neutral), (t_end, neutral)]
    n_samples = max(2, int(round(duration * SAMPLE_HZ)) + 1)
    dt = duration / (n_samples - 1)
    raw = [piecewise_linear(keys, t_start + i * dt) for i in range(n_samples)]

    sigma_samples = SIGMA_S / dt
    kernel, radius = gaussian_kernel(sigma_samples)
    smoothed = []
    for i in range(n_samples):
        acc = 0.0
        for j, w in enumerate(kernel):
            idx = i + j - radius
            if idx < 0:
                idx = 0
            elif idx >= n_samples:
                idx = n_samples - 1
            acc += w * raw[idx]
        smoothed.append(acc)

    n_out = max(2, int(round(duration * OUT_HZ)) + 1)
    out = []
    for i in range(n_out):
        t = t_start + i * (duration / (n_out - 1))
        f_idx = (t - t_start) / dt
        lo = int(math.floor(f_idx))
        hi = min(lo + 1, n_samples - 1)
        frac = f_idx - lo
        v = smoothed[lo] * (1 - frac) + smoothed[hi] * frac
        w = fade_weight(t - t_start, duration, FADE_S)
        v = neutral + w * (v - neutral)
        out.append((t, v))
    out[0] = (t_start, neutral)
    out[-1] = (t_end, neutral)
    return out


def keys_to_linear_segments(keys):
    out = [keys[0][0], keys[0][1]]
    for t, v in keys[1:]:
        out.extend([0, t, v])
    return out


def walk_curve(seg):
    """Recompute (segment_count, point_count) for a curve's Segments list.

    Per-curve TotalPointCount in motion3.json files is
        4 + 3 * bezier_count + 1 * (linear|stepped|inverse_stepped)_count
    The "+4" (vs. just the initial point) was verified across all 40 files.
    """
    points = 4
    segments = 0
    i = 2
    while i < len(seg):
        cmd = int(seg[i])
        i += 1
        segments += 1
        points += POINTS_PER_CMD[cmd]
        i += PAY[cmd]
    return segments, points


def process(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    stats = {}
    for curve in data["Curves"]:
        cid = curve.get("Id")
        if cid not in TARGET_IDS:
            continue
        before = extract_keys(curve["Segments"])
        after = smooth_curve(before, neutral=NEUTRAL[cid])
        curve["Segments"] = keys_to_linear_segments(after)
        stats[cid] = (len(before), len(after))

    seg_total = pts_total = 0
    for c in data["Curves"]:
        s, p = walk_curve(c["Segments"])
        seg_total += s
        pts_total += p
    meta = data.setdefault("Meta", {})
    meta["CurveCount"] = len(data["Curves"])
    meta["TotalSegmentCount"] = seg_total
    meta["TotalPointCount"] = pts_total

    path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    return stats


def main():
    print(f"sigma={SIGMA_S}s  sample={SAMPLE_HZ}Hz  out={OUT_HZ}Hz  fade={FADE_S}s")
    for f in sorted(ROOT.glob("*.motion3.json")):
        s = process(f)
        line = f"{f.name:<28}"
        for tid in TARGET_IDS:
            b, a = s.get(tid, (0, 0))
            line += f" {tid.replace('Param', '')[:6]}: {b:>3} -> {a:<3}   "
        print(line)


if __name__ == "__main__":
    main()

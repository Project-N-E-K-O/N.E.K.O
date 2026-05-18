#!/usr/bin/env python3
from __future__ import annotations

import math
import random
import tempfile
from pathlib import Path

from PIL import Image


WIDTH = 960
HEIGHT = 540
FPS = 30
DURATION_MS = 6200
FRAME_COUNT = round(DURATION_MS / 1000 * FPS)
CENTER_X = WIDTH * 0.5
CENTER_Y = HEIGHT * 0.56
RNG = random.Random(20260514)

ROOT = Path(__file__).resolve().parents[1]
PETAL_DIR = ROOT / "static" / "assets" / "tutorial" / "petals"
SOURCE_PATHS = [
    PETAL_DIR / "yui-guide-petal-1.png",
    PETAL_DIR / "yui-guide-petal-2.png",
]
OUTPUT_PATH = PETAL_DIR / "yui-guide-petal-transition.webp"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def ease_out_cubic(value: float) -> float:
    t = clamp(value, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(value: float) -> float:
    t = clamp(value, 0.0, 1.0)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def smoothstep(value: float) -> float:
    t = clamp(value, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def load_sources() -> list[Image.Image]:
    sources: list[Image.Image] = []
    for path in SOURCE_PATHS:
        sources.append(Image.open(path).convert("RGBA"))
    return sources


def build_particles() -> list[dict]:
    particles: list[dict] = []
    count = 120
    for index in range(count):
        early_bias = RNG.random() < 0.72
        start_delay_ms = RNG.uniform(0, 500) if early_bias else RNG.uniform(160, 760)
        lifespan_ms = RNG.uniform(3200, 4700) if early_bias else RNG.uniform(4000, 5600)
        start_x = CENTER_X + RNG.uniform(-6, 5)
        start_y = CENTER_Y + RNG.uniform(-8, 7)
        right_extent = RNG.uniform(280, 420)
        end_x = RNG.uniform(-220, 120)
        arc_height = RNG.uniform(-120, -48)
        end_spread_x = RNG.uniform(-320, 320)
        end_spread_y = RNG.uniform(-240, 190)
        noise_amp = RNG.uniform(4, 15)
        scale_start = RNG.uniform(0.036, 0.072)
        scale_end = scale_start * RNG.uniform(1.9, 2.5)
        rotation_deg = RNG.uniform(0, 360)
        angular_velocity = RNG.uniform(-180, 180)
        opacity = RNG.uniform(0.58, 0.96)
        fade_out_start = RNG.uniform(0.7, 0.8) if early_bias else RNG.uniform(0.78, 0.88)
        fade_out_length = RNG.uniform(0.12, 0.2) if early_bias else RNG.uniform(0.14, 0.22)
        particles.append({
            "source_index": index % len(SOURCE_PATHS),
            "start_delay_ms": start_delay_ms,
            "lifespan_ms": lifespan_ms,
            "start_x": start_x,
            "start_y": start_y,
            "right_extent": right_extent,
            "end_x": end_x,
            "arc_height": arc_height,
            "end_spread_x": end_spread_x,
            "end_spread_y": end_spread_y,
            "noise_amp": noise_amp,
            "scale_start": scale_start,
            "scale_end": scale_end,
            "rotation_deg": rotation_deg,
            "angular_velocity": angular_velocity,
            "opacity": opacity,
            "fade_out_start": fade_out_start,
            "fade_out_length": fade_out_length,
        })
    return particles


def particle_progress(now_ms: float, particle: dict) -> float | None:
    elapsed = now_ms - particle["start_delay_ms"]
    if elapsed < 0:
        return None
    progress = elapsed / particle["lifespan_ms"]
    if progress > 1.0:
        return None
    return clamp(progress, 0.0, 1.0)


def position_at(progress: float, particle: dict) -> tuple[float, float]:
    if progress < 0.34:
        phase = ease_out_cubic(progress / 0.34)
        x = particle["start_x"] + particle["right_extent"] * phase
        y = particle["start_y"] + particle["arc_height"] * math.sin(phase * math.pi * 0.9)
    else:
        phase = ease_in_out_cubic((progress - 0.34) / 0.66)
        x = (particle["start_x"] + particle["right_extent"]) + (
            particle["end_x"] - (particle["start_x"] + particle["right_extent"])
        ) * phase
        arc_mix = math.sin((1.0 - phase) * math.pi * 0.75)
        y = particle["start_y"] + particle["arc_height"] * arc_mix

    spread = smoothstep(progress ** 0.78)
    x += particle["end_spread_x"] * spread
    y += particle["end_spread_y"] * spread

    wobble = particle["noise_amp"] * (0.35 + 0.65 * spread)
    x += math.sin(progress * math.pi * 3.6 + particle["rotation_deg"] * 0.02) * wobble
    y += math.cos(progress * math.pi * 2.8 + particle["rotation_deg"] * 0.013) * wobble * 0.65
    return x, y


def scale_at(progress: float, particle: dict) -> float:
    growth = smoothstep(progress ** 1.35)
    return particle["scale_start"] + (particle["scale_end"] - particle["scale_start"]) * growth


def alpha_at(progress: float, particle: dict) -> float:
    fade_in = smoothstep(progress / 0.08)
    fade_out = 1.0 - smoothstep(
        (progress - particle["fade_out_start"]) / particle["fade_out_length"]
    )
    return particle["opacity"] * clamp(fade_in * fade_out, 0.0, 1.0)


def render_frame(now_ms: float, sources: list[Image.Image], particles: list[dict]) -> Image.Image:
    frame = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    active: list[tuple[float, Image.Image, int, int]] = []

    for particle in particles:
        progress = particle_progress(now_ms, particle)
        if progress is None:
            continue

        source = sources[particle["source_index"]]
        scale = scale_at(progress, particle)
        target_w = max(6, round(source.width * scale))
        target_h = max(6, round(source.height * scale))
        petal = source.resize((target_w, target_h), Image.Resampling.LANCZOS)
        rotation = particle["rotation_deg"] + particle["angular_velocity"] * progress
        petal = petal.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=True)

        alpha = alpha_at(progress, particle)
        if alpha <= 0:
            continue
        alpha_band = petal.getchannel("A").point(lambda px: round(px * alpha))
        petal.putalpha(alpha_band)

        x, y = position_at(progress, particle)
        active.append((y, petal, round(x - petal.width * 0.5), round(y - petal.height * 0.5)))

    active.sort(key=lambda item: item[0])
    for _, petal, left, top in active:
        frame.alpha_composite(petal, (left, top))
    return frame


def main() -> None:
    sources = load_sources()
    particles = build_particles()
    frame_duration_ms = round(1000 / FPS)
    with tempfile.TemporaryDirectory(prefix="yui-petal-transition-") as temp_dir:
        temp_path = Path(temp_dir)
        frames: list[Image.Image] = []
        for frame_index in range(FRAME_COUNT):
            now_ms = frame_index * frame_duration_ms
            frames.append(render_frame(now_ms, sources, particles))

        output_temp = temp_path / "yui-guide-petal-transition.webp"
        first, rest = frames[0], frames[1:]
        first.save(
            output_temp,
            format="WEBP",
            save_all=True,
            append_images=rest,
            duration=frame_duration_ms,
            loop=1,
            quality=68,
            method=0,
            lossless=False,
        )
        output_temp.replace(OUTPUT_PATH)
    print(f"generated {OUTPUT_PATH} with {FRAME_COUNT} frames at {FPS} fps")


if __name__ == "__main__":
    main()

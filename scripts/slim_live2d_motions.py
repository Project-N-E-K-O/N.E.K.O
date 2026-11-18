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

"""Rewrite Live2D motion files as compact JSON with rounded floats.

Exported motions are pretty-printed with one number per line and carry
float32 values widened to ~16 decimal places, so most of the file size is
whitespace and noise digits. This script keeps every curve and keyframe and
only changes the serialization:
  - no indentation or spaces between tokens;
  - non-integer numbers rounded to DECIMALS places, which is about the
    precision float32 already has for these magnitudes. Fewer places are not
    safe: some Bezier segments are only a few milliseconds long with control
    points far outside the parameter range, so shifting their times by 5e-5 s
    moved the evaluated value by ~1% of the range. Rounding is monotonic, so
    point order and Meta.Duration bounds are preserved.

Meta counts are untouched because no points are added or removed. Running it
again on already slimmed files is a no-op.

Usage: `uv run python scripts/slim_live2d_motions.py --root static/yui-origin`
"""

import argparse
import json
from pathlib import Path

DECIMALS = 6


def _round_floats(value):
    if isinstance(value, float):
        rounded = round(value, DECIMALS)
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    return value


def dump_motion(data) -> str:
    """Serialize motion3.json data in the compact, rounded form."""
    return json.dumps(_round_floats(data), ensure_ascii=False, separators=(",", ":"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Model directory containing *.motion3.json files",
    )
    args = parser.parse_args()
    files = sorted(args.root.glob("*.motion3.json"))
    if not files:
        parser.error(f"no *.motion3.json files under {args.root}")
    before_total = after_total = 0
    for f in files:
        before = f.read_bytes()
        after = dump_motion(json.loads(before.decode("utf-8"))).encode("utf-8")
        if after != before:
            f.write_bytes(after)
        before_total += len(before)
        after_total += len(after)
        print(f"{f.name:<28} {len(before):>9} -> {len(after):>9}")
    print(f"total {before_total} -> {after_total} bytes")


if __name__ == "__main__":
    main()

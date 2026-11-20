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

"""Check that the pinned wake-word runtime wheel is still downloadable.

The "repair resources" flow downloads a custom sherpa-onnx wheel from a fixed
GitHub Release asset. Its URL, size and SHA256 are pinned as constants in
``main_logic/voice_input/wake_word/runtime_bundle.py``. When that Release went
missing, every repair attempt failed with 404 and nobody noticed for a long
time. ``.github/workflows/wake-word-runtime-url-check.yml`` runs this script
daily so the next breakage is visible within a day.

The constants are read with ``ast`` instead of importing the package, so the
check needs nothing beyond the standard library.

Modes:

``--head`` (default)
    Anonymous request that follows redirects; fails unless the final status
    is 2xx. GitHub redirects release assets to a signed object-store URL that
    may answer ``HEAD`` with 403, so on 403/405 the check retries with a
    ``GET`` limited to ``Range: bytes=0-0`` instead of downloading the wheel.
    When the response reveals the total size, it must match ``WHEEL_BYTES``.

``--full``
    Downloads the whole wheel and verifies ``WHEEL_BYTES`` and ``WHEEL_SHA256``.

Requests never carry credentials. Exit status is 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "main_logic" / "voice_input" / "wake_word" / "runtime_bundle.py"
USER_AGENT = "neko-wake-word-runtime-check/1"
DEFAULT_TIMEOUT_S = 60.0
_CHUNK = 1024 * 1024
_RANGE_TOTAL = re.compile(r"^bytes\s+\d+-\d+/(\d+)$")


class CheckError(RuntimeError):
    """A check failed; the message is printed for the workflow log."""


@dataclass(frozen=True)
class WheelPins:
    url: str
    sha256: str
    size: int


def _evaluate(node: ast.AST, known: dict[str, object]) -> object:
    """Evaluate the small expression subset used by the pinned constants."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in known:
        return known[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
        left = _evaluate(node.left, known)
        right = _evaluate(node.right, known)
        try:
            return left + right if isinstance(node.op, ast.Add) else left * right
        except TypeError as exc:
            raise CheckError(f"cannot evaluate expression at line {node.lineno}: {exc}") from exc
    raise CheckError(f"unsupported expression at line {getattr(node, 'lineno', '?')}: {ast.dump(node)}")


def load_pins(path: Path = DEFAULT_BUNDLE) -> WheelPins:
    """Read WHEEL_URL, WHEEL_SHA256 and WHEEL_BYTES from runtime_bundle.py."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise CheckError(f"cannot parse {path}: {exc}") from exc
    known: dict[str, object] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            target, value = statement.target, statement.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id.startswith("WHEEL_"):
            known[target.id] = _evaluate(value, known)
    url, sha256, size = known.get("WHEEL_URL"), known.get("WHEEL_SHA256"), known.get("WHEEL_BYTES")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise CheckError(f"WHEEL_URL missing or not an https URL in {path}")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise CheckError(f"WHEEL_SHA256 missing or not a lowercase hex SHA256 in {path}")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise CheckError(f"WHEEL_BYTES missing or not a positive int in {path}")
    return WheelPins(url=url, sha256=sha256, size=size)


class _KeepMethodRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects without turning HEAD into a full GET."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and req.get_method() == "HEAD":
            new.method = "HEAD"
        return new


def build_opener(*handlers: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """Opener without credentials; redirects keep HEAD and other headers."""
    return urllib.request.build_opener(_KeepMethodRedirectHandler, *handlers)


def _display(url: str) -> str:
    """Drop the query so short-lived signed object-store tokens stay out of logs."""
    return urllib.parse.urlsplit(url)._replace(query="", fragment="").geturl()


def _request(opener, url: str, method: str, timeout: float, headers: dict[str, str] | None = None):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return opener.open(request, timeout=timeout)


def _check_total_size(total: int | None, expected: int, source: str) -> None:
    if total is not None and total != expected:
        raise CheckError(f"size mismatch from {source}: server reports {total} bytes, WHEEL_BYTES is {expected}")


def check_head(pins: WheelPins, opener=None, timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """Cheap reachability probe; returns a one-line success summary."""
    opener = opener or build_opener()
    try:
        with _request(opener, pins.url, "HEAD", timeout) as response:
            length = response.headers.get("Content-Length")
            _check_total_size(int(length) if length and length.isdigit() else None, pins.size, "HEAD")
            return f"HEAD {response.status} {_display(response.url)}"
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code not in (403, 405):
            raise CheckError(f"HEAD failed: HTTP {exc.code} {exc.reason} at {_display(exc.url)}") from exc
        print(f"HEAD returned HTTP {exc.code} at {_display(exc.url)}; retrying with a 1-byte ranged GET")
    except urllib.error.URLError as exc:
        raise CheckError(f"HEAD failed: {exc.reason}") from exc
    try:
        with _request(opener, pins.url, "GET", timeout, {"Range": "bytes=0-0"}) as response:
            match = _RANGE_TOTAL.match(response.headers.get("Content-Range", "").strip())
            if response.status == 206 and match:
                _check_total_size(int(match.group(1)), pins.size, "Content-Range")
            # A server that ignores Range answers 200 with the full body; the
            # response is closed here without reading it.
            return f"ranged GET {response.status} {_display(response.url)}"
    except urllib.error.HTTPError as exc:
        exc.close()
        raise CheckError(f"ranged GET failed: HTTP {exc.code} {exc.reason} at {_display(exc.url)}") from exc
    except urllib.error.URLError as exc:
        raise CheckError(f"ranged GET failed: {exc.reason}") from exc


def check_full(pins: WheelPins, opener=None, timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """Download the whole wheel and verify its size and SHA256."""
    opener = opener or build_opener()
    digest = hashlib.sha256()
    received = 0
    try:
        with _request(opener, pins.url, "GET", timeout) as response:
            while chunk := response.read(_CHUNK):
                received += len(chunk)
                if received > pins.size:
                    raise CheckError(f"size mismatch: more than WHEEL_BYTES={pins.size} bytes received")
                digest.update(chunk)
            final_url = response.url
    except urllib.error.HTTPError as exc:
        exc.close()
        raise CheckError(f"download failed: HTTP {exc.code} {exc.reason} at {_display(exc.url)}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise CheckError(f"download failed: {getattr(exc, 'reason', exc)}") from exc
    if received != pins.size:
        raise CheckError(f"size mismatch: received {received} bytes, WHEEL_BYTES is {pins.size}")
    actual = digest.hexdigest()
    if actual != pins.sha256:
        raise CheckError(f"SHA256 mismatch: got {actual}, WHEEL_SHA256 is {pins.sha256}")
    return f"downloaded {received} bytes from {_display(final_url)}; size and SHA256 match"


def main(argv: list[str] | None = None, opener=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--head", dest="mode", action="store_const", const="head",
                      help="anonymous reachability probe (default)")
    mode.add_argument("--full", dest="mode", action="store_const", const="full",
                      help="download the wheel and verify size and SHA256")
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE,
                        help="path to runtime_bundle.py (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help="socket timeout in seconds (default: %(default)s)")
    args = parser.parse_args(argv)
    check = check_full if args.mode == "full" else check_head
    try:
        pins = load_pins(args.bundle)
        print(f"WHEEL_URL={pins.url}")
        print(f"WHEEL_BYTES={pins.size} WHEEL_SHA256={pins.sha256}")
        print(f"OK: {check(pins, opener=opener, timeout=args.timeout)}")
    except CheckError as exc:
        print(f"FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

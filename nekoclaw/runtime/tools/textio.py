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

"""Decode and re-encode text files without changing their encoding or newlines.

The file tools work on LF-normalized text. This module remembers how a file
was stored (codec, byte-order mark, newline style) so a write-back reproduces
it. UTF-8 is the expected case; a byte-order mark selects UTF-8/16/32, and a
file that is not valid UTF-8 is accepted only when a legacy encoding can be
identified with confidence, which matters for GBK files on Chinese Windows.
"""

from __future__ import annotations

import codecs
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from nekoclaw.runtime.tools.base import ToolError

# A NUL byte in this leading sample marks a file as binary (dsh fs-local uses the same size).
BINARY_SAMPLE_BYTES = 8192
_NEWLINE_SAMPLE_CHARS = 4096
_NUL = bytes(1)

# Checked in this order: the UTF-32 LE mark starts with the UTF-16 LE mark.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


@dataclass(frozen=True, slots=True)
class TextFormat:
    """How a text file is stored on disk."""

    encoding: str = "utf-8"
    bom: bytes = b""
    newline: str = "\n"


UTF8_LF = TextFormat()


def normalize_newlines(text: str) -> str:
    """Return ``text`` with CRLF pairs turned into LF."""
    return text.replace("\r\n", "\n")


def detect_newline(text: str) -> str:
    """Return the dominant newline of ``text``, judged on its beginning."""
    sample = text[:_NEWLINE_SAMPLE_CHARS]
    crlf = sample.count("\r\n")
    lf = sample.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _looks_like_text(text: str) -> bool:
    """Reject decodings that produced control characters: a wrong legacy guess."""
    for ch in text[:BINARY_SAMPLE_BYTES]:
        code = ord(ch)
        if code < 32 and ch not in "\t\n\r\f\v\b":
            return False
        if code == 127:
            return False
    return True


def _detect_legacy_encoding(raw: bytes) -> str | None:
    """Identify a non-UTF-8 text encoding, or return ``None`` when unsure."""
    try:
        from charset_normalizer import from_bytes
    except Exception:  # noqa: BLE001 - optional helper; absent means "unknown"
        return None
    try:
        best = from_bytes(raw[: 256 * 1024]).best()
    except Exception:  # noqa: BLE001 - a detector failure means "unknown"
        return None
    if best is None or not best.encoding:
        return None
    encoding = best.encoding
    try:
        decoded = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None
    return encoding if _looks_like_text(decoded) else None


def decode_text(raw: bytes, *, verb: str, display: str) -> tuple[str, TextFormat]:
    """Decode file bytes into LF-normalized text plus the format to write back.

    Raises ``ToolError`` for binary content and for text whose encoding cannot
    be established.
    """
    for mark, encoding in _BOMS:
        if raw.startswith(mark):
            try:
                decoded = raw[len(mark):].decode(encoding)
            except UnicodeDecodeError as exc:
                raise ToolError(f'cannot {verb} "{display}": invalid {encoding} text') from exc
            return normalize_newlines(decoded), TextFormat(encoding, mark, detect_newline(decoded))
    if _NUL in raw[:BINARY_SAMPLE_BYTES]:
        raise ToolError(f'cannot {verb} "{display}": binary file')
    try:
        decoded = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError as exc:
        legacy = _detect_legacy_encoding(raw)
        if legacy is None:
            raise ToolError(f'cannot {verb} "{display}": invalid UTF-8 text') from exc
        decoded = raw.decode(legacy)
        encoding = legacy
    return normalize_newlines(decoded), TextFormat(encoding, b"", detect_newline(decoded))


def encode_text(text: str, fmt: TextFormat, *, display: str) -> bytes:
    """Encode LF-normalized ``text`` the way ``fmt`` describes."""
    body = normalize_newlines(text)
    if fmt.newline == "\r\n":
        body = body.replace("\n", "\r\n")
    try:
        return fmt.bom + body.encode(fmt.encoding)
    except UnicodeEncodeError as exc:
        raise ToolError(
            f'cannot write "{display}": the file is stored as {fmt.encoding} and the new text '
            "contains characters that encoding cannot represent"
        ) from exc


def split_lines(text: str) -> list[str]:
    """Split LF-normalized text into lines the way ``read`` numbers them.

    A final newline does not start another line, and a trailing carriage
    return left over from a lone CR LF mix is dropped.
    """
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def read_bytes(path: Path, *, verb: str, display: str, max_bytes: int | None = None) -> bytes:
    """Read a regular file, mapping the usual failures to model-facing errors."""
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise ToolError(f'cannot {verb} "{display}": not found') from exc
    except PermissionError as exc:
        raise ToolError(f'cannot {verb} "{display}": permission denied') from exc
    except OSError as exc:
        raise ToolError(f'cannot {verb} "{display}": {exc.strerror or exc}') from exc
    if not stat.S_ISREG(info.st_mode):
        raise ToolError(f'cannot {verb} "{display}": not a regular file')
    if max_bytes is not None and info.st_size > max_bytes:
        raise ToolError(
            f'cannot {verb} "{display}": {info.st_size} bytes exceeds the {max_bytes}-byte limit'
        )
    try:
        return path.read_bytes()
    except PermissionError as exc:
        raise ToolError(f'cannot {verb} "{display}": permission denied') from exc
    except OSError as exc:
        raise ToolError(f'cannot {verb} "{display}": {exc.strerror or exc}') from exc


def write_bytes_atomic(path: Path, data: bytes, *, display: str) -> None:
    """Replace ``path`` with ``data`` through a temporary file in the same directory.

    Missing parent directories are created. An existing file keeps its
    permission bits.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError(f'cannot write "{display}": {exc.strerror or exc}') from exc
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = None
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except PermissionError as exc:
        _discard(temporary)
        raise ToolError(f'cannot write "{display}": permission denied') from exc
    except OSError as exc:
        _discard(temporary)
        raise ToolError(f'cannot write "{display}": {exc.strerror or exc}') from exc


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass

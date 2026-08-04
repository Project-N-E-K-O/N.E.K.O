#!/usr/bin/env python3
"""Minimal asar extractor (no npm required)."""
from __future__ import annotations

import json
import os
import struct
import sys


def extract_asar(asar_path: str, out_dir: str) -> None:
    with open(asar_path, "rb") as f:
        # asar header layout used by Electron:
        # uint32 size_of_size_field (usually 4)
        # uint32 header_json_size (includes padding in some versions)
        # uint32 header_json_size_raw
        # uint32 header_object_size
        # then JSON header string of length header_json_size_raw
        size_field = struct.unpack("<I", f.read(4))[0]
        if size_field != 4:
            # some variants
            f.seek(0)
        header_size = struct.unpack("<I", f.read(4))[0]
        header_object_size = struct.unpack("<I", f.read(4))[0]
        header_string_size = struct.unpack("<I", f.read(4))[0]
        header_json = f.read(header_string_size).decode("utf-8")
        # data base offset = 8 + header_size? Official: 4 + 4 + header_size
        # After reading: we consumed 16 + header_string_size from start if size_field was consumed...
        # Reset and use known algorithm from asar spec:
    with open(asar_path, "rb") as f:
        data = f.read()

    # Robust parse from @electron/asar
    # offset 0: uint32 pickle size (=4)
    # offset 4: uint32 header size
    # offset 8: uint32 header size again (pickle)
    # offset 12: uint32 string length
    # offset 16: json string
    pickle_size = struct.unpack_from("<I", data, 0)[0]
    header_size = struct.unpack_from("<I", data, 4)[0]
    # header payload starts at 8; first uint32 is header_size again inside pickle
    header_start = 8
    header_payload = data[header_start : header_start + header_size]
    # inside pickle: uint32 string_len + string
    str_len = struct.unpack_from("<I", header_payload, 4)[0]
    json_bytes = header_payload[8 : 8 + str_len]
    header = json.loads(json_bytes.decode("utf-8"))
    files_base = 8 + header_size

    def walk(node: dict, prefix: str = "") -> None:
        if "files" in node:
            for name, child in node["files"].items():
                walk(child, os.path.join(prefix, name) if prefix else name)
            return
        if "offset" not in node:
            # directory placeholder / link without payload
            os.makedirs(os.path.join(out_dir, prefix), exist_ok=True)
            return
        size = int(node.get("size", 0))
        offset = int(node["offset"])
        unpacked = bool(node.get("unpacked"))
        dest = os.path.join(out_dir, prefix)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if unpacked:
            # content lives beside asar
            return
        start = files_base + offset
        with open(dest, "wb") as out:
            out.write(data[start : start + size])

    os.makedirs(out_dir, exist_ok=True)
    walk(header)
    print(f"OK extracted to {out_dir}")
    # list top
    for root, dirs, files in os.walk(out_dir):
        rel = os.path.relpath(root, out_dir)
        if rel == ".":
            print("TOP:", ", ".join(sorted(dirs + files)[:40]))
            break


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else r"E:\SteamLibrary\steamapps\common\n.e.k.o\resources\app.asar"
    dst = sys.argv[2] if len(sys.argv) > 2 else r"d:\N.E.K.O\.tmp_asar_extract"
    extract_asar(src, dst)

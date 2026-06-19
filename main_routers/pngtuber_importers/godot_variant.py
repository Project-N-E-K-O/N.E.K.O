# -*- coding: utf-8 -*-
"""Small Godot 4 Variant binary reader used by PNGTubeRemix imports."""

from __future__ import annotations

import struct


class GodotVariantReader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset

    def _u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def _i32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return value

    def _i64(self) -> int:
        value = struct.unpack_from("<q", self.data, self.offset)[0]
        self.offset += 8
        return value

    def _f32(self) -> float:
        value = struct.unpack_from("<f", self.data, self.offset)[0]
        self.offset += 4
        return value

    def _f64(self) -> float:
        value = struct.unpack_from("<d", self.data, self.offset)[0]
        self.offset += 8
        return value

    def _pad(self) -> None:
        self.offset = (self.offset + 3) & ~3

    def _string_payload(self) -> str:
        length = self._u32()
        raw = self.data[self.offset:self.offset + length]
        self.offset += length
        self._pad()
        return raw.decode("utf-8", errors="replace")

    def _bytes_payload(self) -> bytes:
        length = self._u32()
        raw = self.data[self.offset:self.offset + length]
        self.offset += length
        self._pad()
        return bytes(raw)

    def read_variant(self):
        header = self._u32()
        variant_type = header & 0xFFFF
        flags = header >> 16

        if variant_type == 0:
            return None
        if variant_type == 1:
            return self._u32() != 0
        if variant_type == 2:
            return self._i64() if flags & 1 else self._i32()
        if variant_type == 3:
            return self._f64() if flags & 1 else self._f32()
        if variant_type in (4, 21):
            return self._string_payload()
        if variant_type == 5:
            return (self._f32(), self._f32())
        if variant_type == 6:
            return (self._i32(), self._i32())
        if variant_type == 7:
            return (self._f32(), self._f32(), self._f32(), self._f32())
        if variant_type == 9:
            return (self._f32(), self._f32(), self._f32())
        if variant_type == 10:
            return (self._i32(), self._i32(), self._i32())
        if variant_type == 12:
            return (self._f32(), self._f32(), self._f32(), self._f32())
        if variant_type == 20:
            return {"__color__": (self._f32(), self._f32(), self._f32(), self._f32())}
        if variant_type == 22:
            return {"__node_path__": self._string_payload()}
        if variant_type == 24:
            class_name = self._string_payload()
            count = self._u32() & 0x7FFFFFFF
            properties = {}
            for _ in range(count):
                name = self._string_payload()
                properties[name] = self.read_variant()
            return {"__object__": class_name, "properties": properties}
        if variant_type == 27:
            count = self._u32() & 0x7FFFFFFF
            result = {}
            for _ in range(count):
                key = self.read_variant()
                value = self.read_variant()
                try:
                    hash(key)
                except TypeError:
                    key = repr(key)
                result[key] = value
            return result
        if variant_type == 28:
            count = self._u32() & 0x7FFFFFFF
            return [self.read_variant() for _ in range(count)]
        if variant_type == 29:
            return self._bytes_payload()
        if variant_type == 30:
            count = self._u32()
            values = list(struct.unpack_from("<" + "i" * count, self.data, self.offset))
            self.offset += 4 * count
            self._pad()
            return values
        if variant_type == 31:
            count = self._u32()
            values = list(struct.unpack_from("<" + "q" * count, self.data, self.offset))
            self.offset += 8 * count
            self._pad()
            return values
        if variant_type == 32:
            count = self._u32()
            values = list(struct.unpack_from("<" + "f" * count, self.data, self.offset))
            self.offset += 4 * count
            self._pad()
            return values
        if variant_type == 33:
            count = self._u32()
            values = list(struct.unpack_from("<" + "d" * count, self.data, self.offset))
            self.offset += 8 * count
            self._pad()
            return values
        if variant_type == 35:
            count = self._u32()
            return [self._string_payload() for _ in range(count)]
        if variant_type == 36:
            count = self._u32()
            values = [(self._f32(), self._f32()) for _ in range(count)]
            self._pad()
            return values

        raise ValueError(f"Unsupported Godot Variant type {variant_type} at offset {self.offset - 4}")


def load_variant_file(data: bytes):
    offsets = [4, 0] if len(data) >= 8 else [0]
    last_error: Exception | None = None
    for offset in offsets:
        try:
            reader = GodotVariantReader(data, offset)
            value = reader.read_variant()
            if isinstance(value, dict):
                return value
        except Exception as exc:
            last_error = exc
    raise ValueError(f"Unable to parse Godot Variant file: {last_error}")

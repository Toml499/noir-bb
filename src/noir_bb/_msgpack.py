"""A tiny, dependency-free MessagePack codec.

noir-bb stays dependency-free (see the README), but the ``bb msgpack run`` API
that :mod:`noir_bb.msgpack_api` drives speaks MessagePack. Rather than pull in
the ``msgpack`` package, we implement the small subset of the spec bb actually
uses: nil, bool, int, str, bin, array and map. Field elements cross the wire as
32-byte ``bin`` objects, so ``bytes`` round-trips as ``bin`` and ``str`` stays
``str`` (the two must not be conflated -- bb tells command names from field
values by exactly this distinction).

This is not a general-purpose implementation (no floats on the encode side, no
ext types), but it is byte-for-byte compatible with the ``msgpack`` package for
everything bb sends and receives, which the test-suite checks.
"""

from __future__ import annotations

import struct
from typing import Any, Tuple


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def packb(obj: Any) -> bytes:
    out = bytearray()
    _pack(obj, out)
    return bytes(out)


def _pack(obj: Any, out: bytearray) -> None:
    if obj is None:
        out.append(0xC0)
    elif obj is True:
        out.append(0xC3)
    elif obj is False:
        out.append(0xC2)
    elif isinstance(obj, int):
        _pack_int(obj, out)
    elif isinstance(obj, str):
        _pack_str(obj, out)
    elif isinstance(obj, (bytes, bytearray)):
        _pack_bin(bytes(obj), out)
    elif isinstance(obj, (list, tuple)):
        _pack_array(obj, out)
    elif isinstance(obj, dict):
        _pack_map(obj, out)
    else:
        raise TypeError(f"cannot msgpack-encode {type(obj).__name__}: {obj!r}")


def _pack_int(n: int, out: bytearray) -> None:
    if 0 <= n < 0x80:
        out.append(n)
    elif -0x20 <= n < 0:
        out.append(0xE0 | (n & 0x1F))
    elif 0 <= n <= 0xFF:
        out += b"\xcc" + struct.pack("B", n)
    elif 0 <= n <= 0xFFFF:
        out += b"\xcd" + struct.pack(">H", n)
    elif 0 <= n <= 0xFFFFFFFF:
        out += b"\xce" + struct.pack(">I", n)
    elif 0 <= n <= 0xFFFFFFFFFFFFFFFF:
        out += b"\xcf" + struct.pack(">Q", n)
    elif -0x80 <= n < 0:
        out += b"\xd0" + struct.pack(">b", n)
    elif -0x8000 <= n < 0:
        out += b"\xd1" + struct.pack(">h", n)
    elif -0x80000000 <= n < 0:
        out += b"\xd2" + struct.pack(">i", n)
    elif -0x8000000000000000 <= n < 0:
        out += b"\xd3" + struct.pack(">q", n)
    else:
        raise OverflowError(f"integer too large for msgpack: {n}")


def _pack_str(s: str, out: bytearray) -> None:
    data = s.encode("utf-8")
    n = len(data)
    if n < 0x20:
        out.append(0xA0 | n)
    elif n <= 0xFF:
        out += b"\xd9" + struct.pack("B", n)
    elif n <= 0xFFFF:
        out += b"\xda" + struct.pack(">H", n)
    else:
        out += b"\xdb" + struct.pack(">I", n)
    out += data


def _pack_bin(data: bytes, out: bytearray) -> None:
    n = len(data)
    if n <= 0xFF:
        out += b"\xc4" + struct.pack("B", n)
    elif n <= 0xFFFF:
        out += b"\xc5" + struct.pack(">H", n)
    else:
        out += b"\xc6" + struct.pack(">I", n)
    out += data


def _pack_array(seq, out: bytearray) -> None:
    n = len(seq)
    if n < 0x10:
        out.append(0x90 | n)
    elif n <= 0xFFFF:
        out += b"\xdc" + struct.pack(">H", n)
    else:
        out += b"\xdd" + struct.pack(">I", n)
    for item in seq:
        _pack(item, out)


def _pack_map(mapping: dict, out: bytearray) -> None:
    n = len(mapping)
    if n < 0x10:
        out.append(0x80 | n)
    elif n <= 0xFFFF:
        out += b"\xde" + struct.pack(">H", n)
    else:
        out += b"\xdf" + struct.pack(">I", n)
    for key, value in mapping.items():
        _pack(key, out)
        _pack(value, out)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def unpackb(data: bytes) -> Any:
    value, off = _unpack(memoryview(data), 0)
    return value


def _unpack(buf: memoryview, off: int) -> Tuple[Any, int]:
    b = buf[off]
    off += 1
    # fixed-range single-byte families
    if b < 0x80:                      # positive fixint
        return b, off
    if b >= 0xE0:                     # negative fixint
        return b - 0x100, off
    if 0x80 <= b <= 0x8F:             # fixmap
        return _unpack_map(buf, off, b & 0x0F)
    if 0x90 <= b <= 0x9F:             # fixarray
        return _unpack_array(buf, off, b & 0x0F)
    if 0xA0 <= b <= 0xBF:            # fixstr
        return _unpack_str(buf, off, b & 0x1F)

    if b == 0xC0:
        return None, off
    if b == 0xC2:
        return False, off
    if b == 0xC3:
        return True, off

    if b == 0xCC:
        return buf[off], off + 1
    if b == 0xCD:
        return struct.unpack_from(">H", buf, off)[0], off + 2
    if b == 0xCE:
        return struct.unpack_from(">I", buf, off)[0], off + 4
    if b == 0xCF:
        return struct.unpack_from(">Q", buf, off)[0], off + 8
    if b == 0xD0:
        return struct.unpack_from(">b", buf, off)[0], off + 1
    if b == 0xD1:
        return struct.unpack_from(">h", buf, off)[0], off + 2
    if b == 0xD2:
        return struct.unpack_from(">i", buf, off)[0], off + 4
    if b == 0xD3:
        return struct.unpack_from(">q", buf, off)[0], off + 8
    if b == 0xCA:
        return struct.unpack_from(">f", buf, off)[0], off + 4
    if b == 0xCB:
        return struct.unpack_from(">d", buf, off)[0], off + 8

    if b == 0xC4:
        n = buf[off]; off += 1
        return _unpack_bin(buf, off, n)
    if b == 0xC5:
        n = struct.unpack_from(">H", buf, off)[0]; off += 2
        return _unpack_bin(buf, off, n)
    if b == 0xC6:
        n = struct.unpack_from(">I", buf, off)[0]; off += 4
        return _unpack_bin(buf, off, n)

    if b == 0xD9:
        n = buf[off]; off += 1
        return _unpack_str(buf, off, n)
    if b == 0xDA:
        n = struct.unpack_from(">H", buf, off)[0]; off += 2
        return _unpack_str(buf, off, n)
    if b == 0xDB:
        n = struct.unpack_from(">I", buf, off)[0]; off += 4
        return _unpack_str(buf, off, n)

    if b == 0xDC:
        n = struct.unpack_from(">H", buf, off)[0]; off += 2
        return _unpack_array(buf, off, n)
    if b == 0xDD:
        n = struct.unpack_from(">I", buf, off)[0]; off += 4
        return _unpack_array(buf, off, n)

    if b == 0xDE:
        n = struct.unpack_from(">H", buf, off)[0]; off += 2
        return _unpack_map(buf, off, n)
    if b == 0xDF:
        n = struct.unpack_from(">I", buf, off)[0]; off += 4
        return _unpack_map(buf, off, n)

    raise ValueError(f"unsupported msgpack byte 0x{b:02x} at offset {off - 1}")


def _unpack_str(buf: memoryview, off: int, n: int) -> Tuple[str, int]:
    return bytes(buf[off:off + n]).decode("utf-8"), off + n


def _unpack_bin(buf: memoryview, off: int, n: int) -> Tuple[bytes, int]:
    return bytes(buf[off:off + n]), off + n


def _unpack_array(buf: memoryview, off: int, n: int) -> Tuple[list, int]:
    items = []
    for _ in range(n):
        value, off = _unpack(buf, off)
        items.append(value)
    return items, off


def _unpack_map(buf: memoryview, off: int, n: int) -> Tuple[dict, int]:
    out = {}
    for _ in range(n):
        key, off = _unpack(buf, off)
        value, off = _unpack(buf, off)
        out[key] = value
    return out, off

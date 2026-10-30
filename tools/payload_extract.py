#!/usr/bin/env python3
"""Extract partitions from an Android OTA payload.bin without external deps.

Usage:
  payload_extract.py payload.bin --list
  payload_extract.py payload.bin vendor_boot out.img

Supports the op types used by full/ incremental Qualcomm-AB OTA payloads that
store whole images: REPLACE (raw), REPLACE_BZ (bzip2), REPLACE_XZ (xz),
ZERO/DISCARD. Brotli/zucchini/diff ops are reported, not applied.
"""
import bz2
import lzma
import struct
import sys

BLOCK = 4096


def varint(buf, i):
    shift = 0
    val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def fields(buf):
    i = 0
    n = len(buf)
    while i < n:
        key, i = varint(buf, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = varint(buf, i)
        elif wt == 2:
            ln, i = varint(buf, i)
            v = buf[i:i + ln]
            i += ln
        elif wt == 5:
            v = buf[i:i + 4]
            i += 4
        elif wt == 1:
            v = buf[i:i + 8]
            i += 8
        else:
            raise ValueError(f"wire type {wt}")
        yield fn, wt, v


def parse_extents(buf):
    out = []
    cur = {}
    for fn, _, v in fields(buf):
        if fn == 1:
            cur["start"] = v
        elif fn == 2:
            cur["num"] = v
            out.append(cur)
            cur = {}
    return out


def parse_manifest(buf):
    parts = []
    for fn, _, v in fields(buf):
        if fn != 13:
            continue
        name = ""
        ops = []
        for pfn, _, pv in fields(v):
            if pfn == 1:
                name = pv.decode()
            elif pfn == 8:
                op = {"type": 0, "off": 0, "len": 0, "dst": []}
                for ofn, _, ov in fields(pv):
                    if ofn == 1:
                        op["type"] = ov
                    elif ofn == 2:
                        op["off"] = ov
                    elif ofn == 3:
                        op["len"] = ov
                    elif ofn == 6:
                        op["dst"] = parse_extents(ov)
                ops.append(op)
        parts.append({"name": name, "ops": ops})
    return parts


def lz4_block(src):
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        for k in range(ml):
            out.append(out[start + k])
    return bytes(out)


def lz4_frame_decompress(buf):
    if buf[:4] == b"\x02\x21\x4c\x18":  # legacy frame (Android ramdisks)
        i = 4
        out = bytearray()
        while True:
            size = int.from_bytes(buf[i:i + 4], "little")
            i += 4
            if size == 0:
                break
            out += lz4_block(buf[i:i + size])
            i += size
        return bytes(out)
    if buf[:4] != b"\x04\x22\x4d\x18":
        raise ValueError("not an lz4 frame")
    i = 4
    flg = buf[i]
    i += 2  # FLG, BD
    if flg & 0x08:
        i += 8
    if flg & 0x01:
        i += 4
    i += 1  # header checksum
    out = bytearray()
    while True:
        size = int.from_bytes(buf[i:i + 4], "little")
        i += 4
        if size == 0:
            break
        uncompressed = size & 0x80000000
        size &= 0x7FFFFFFF
        blk = buf[i:i + size]
        i += size
        out += blk if uncompressed else lz4_block(blk)
        if flg & 0x10:
            i += 4
    return bytes(out)


def load(path):
    with open(path, "rb") as f:
        hdr = f.read(4)
        if hdr != b"CrAU":
            sys.exit("not a payload (bad magic)")
        version, manifest_size = struct.unpack(">QQ", f.read(16))
        (metadata_sig_size,) = struct.unpack(">I", f.read(4))
        manifest = f.read(manifest_size)
        f.seek(metadata_sig_size, 1)
        data_start = f.tell()
    return manifest, data_start, path


def decompress_op(raw, dst_len):
    """Payload ops are not reliably typed across vendors; sniff instead."""
    if not raw:
        return bytes(dst_len), None
    if raw[:3] == b"BZh":
        return bz2.decompress(raw), None
    if raw[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(raw), None
    if raw[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(raw), None
    if raw[:4] == b"\x04\x22\x4d\x18":
        return lz4_frame_decompress(raw), None
    return raw, None


def extract(path, data_start, part, out_path):
    bad = 0
    total = max(
        (e["start"] + e["num"] for op in part["ops"] for e in op["dst"]),
        default=0,
    ) * BLOCK
    buf = bytearray(total)
    with open(path, "rb") as f:
        for op in part["ops"]:
            dst_len = sum(e["num"] for e in op["dst"]) * BLOCK
            raw = b""
            if op["len"]:
                f.seek(data_start + op["off"])
                raw = f.read(op["len"])
            try:
                chunk, _ = decompress_op(raw, dst_len)
            except Exception as exc:
                print(f"  op type {op['type']} @{op['off']}: {exc}", file=sys.stderr)
                bad += 1
                chunk = bytes(dst_len)
            if len(chunk) < dst_len:
                chunk += bytes(dst_len - len(chunk))
            ci = 0
            for e in op["dst"]:
                n = e["num"] * BLOCK
                start = e["start"] * BLOCK
                buf[start:start + n] = chunk[ci:ci + n]
                ci += n
    with open(out_path, "wb") as f:
        f.write(buf)
    return bad, len(buf)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    p = sys.argv[1]
    manifest, data_start, _ = load(p)
    parts = parse_manifest(manifest)
    if sys.argv[2] == "--list":
        for part in parts:
            total = sum(sum(e["num"] for e in op["dst"]) * BLOCK for op in part["ops"])
            types = sorted({op["type"] for op in part["ops"]})
            print(f'{part["name"]:<24} {total:>12} bytes  op_types={types}')
        return
    name, out = sys.argv[2], sys.argv[3]
    match = [x for x in parts if x["name"] == name]
    if not match:
        sys.exit(f"partition {name} not in payload")
    bad, size = extract(p, data_start, match[0], out)
    print(f"{name}: {size} bytes -> {out}" + (f" ({bad} unsupported ops)" if bad else ""))
    if bad:
        sys.exit(2)


if __name__ == "__main__":
    main()

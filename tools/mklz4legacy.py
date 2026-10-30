#!/usr/bin/env python3
"""Wrap bytes in an LZ4 legacy frame (literal-only blocks, no compression).

The Android bootloader concatenates the generic and vendor ramdisks and the
kernel decompresses the combined LZ4 stream, so the generic ramdisk must be
LZ4 legacy like the stock one.
"""
import argparse, struct, sys

BLOCK = 4 * 1024 * 1024


def lz4_literal_block(data):
    out = bytearray()
    n = len(data)
    if n >= 15:
        out.append(0xF0)
        rem = n - 15
        while rem >= 255:
            out.append(255)
            rem -= 255
        out.append(rem)
    else:
        out.append(n << 4)
    out += data
    return bytes(out)


def wrap(data):
    out = bytearray(b"\x02\x21\x4c\x18")
    for i in range(0, len(data), BLOCK):
        blk = lz4_literal_block(data[i:i + BLOCK])
        out += struct.pack("<I", len(blk)) + blk
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    a = ap.parse_args()
    data = open(a.src, "rb").read()
    frame = wrap(data)
    open(a.out, "wb").write(frame)
    print(f"{a.out}: {len(data)} bytes -> {len(frame)} bytes")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the bootconfig blob the bootloader appends to the initrd.

Format, from Documentation/admin-guide/bootconfig.rst and the checks the kernel
actually makes in init/main.c:273-311:

    [data][size u32 LE][checksum u32 LE]["#BOOTCONFIG\\n"]

  checksum  xbc_calc_checksum() = sum of the data bytes, mod 2**32
            (include/linux/bootconfig.h:39-47)
  size      data length in bytes
  magic     BOOTCONFIG_MAGIC, 12 bytes (include/linux/bootconfig.h:24-25)

The kernel finds the magic in the last four bytes before initrd_end, so the
blob has to sit at the very end of the initrd, which is exactly how ABL hands
it over.

  mkbootconfig.py -o blob.bin androidboot.slot_suffix=_a key=value ...
"""
import argparse
import struct

MAGIC = b"#BOOTCONFIG\n"


def build(params):
    data = "".join(p.rstrip("\n") + "\n" for p in params).encode()
    csum = sum(data) & 0xFFFFFFFF
    # init/main.c:295-297 reads size first, then the checksum, so the
    # order in front of the magic is [size][checksum], not the reverse.
    return data + struct.pack("<II", len(data), csum) + MAGIC


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("params", nargs="+", metavar="key=value")
    a = ap.parse_args()
    for p in a.params:
        if "=" not in p:
            raise SystemExit("not a key=value pair: %r" % p)
    blob = build(a.params)
    with open(a.out, "wb") as f:
        f.write(blob)
    print("bootconfig: %d bytes of data -> %s" % (len(blob) - 20, a.out))


main()

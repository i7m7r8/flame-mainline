#!/usr/bin/env python3
"""Pack a mainline arm64 Image + DTB into the boot_b kernel section for flame.

The bootloader jumps to the first byte of the kernel section with x0 pointing
at the DTB it built from the vendor device tree, which mainline cannot use. So
the payload starts with the real Image header, whose first instruction (`b
primary_entry`) is patched to branch into the shim appended at the end; the
shim then relocates the Image and our own DTB and enters the kernel with
x0 = our DTB. See tools/shim.S for the payload contract.

Nothing here reads a stock image: the load addresses are passed in (defaults
from docs/mainline-port.md, chosen against the vendor reserved-memory map).
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

MAGIC = b"SWEETLNX"
ABI_VERSION = 1
PAGE = 4096
ARM64_IMAGE_MAGIC = 0x644D5241  # "ARM\x64" at offset 0x18 of the Image
HDR = struct.Struct("<8sIIIIIIQQIIII")  # see the field list below
HDR_OFF_FIELDS = """
  0  magic      "SWEETLNX"
  8  version    u32
 12  flags      u32
 16  image_off  u32   payload-relative
 20  image_size u32
 24  dtb_off    u32
 28  dtb_size   u32
 32  image_load u64   physical, must be 2 MiB aligned
 40  dtb_load   u64   physical
 48  entry_off  u32   offset of primary_entry inside the Image
 52  shim_off   u32
 56  hdr_off    u32   == shim_off - 4096
 60  rfu        u32
then an asciiz note, zero padded to a page
"""


def round_up(n, align):
    return (n + align - 1) // align * align


def decode_b(word):
    """Offset of an unconditional arm64 branch encoded in `word` (at pc 0)."""
    if (word >> 26) & 0x7 != 0b000101:
        return None
    imm = word & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return imm * 4


def encode_b(target):
    if target % 4 or not (-128 * 1024 * 1024 <= target < 128 * 1024 * 1024):
        sys.exit(f"cannot branch to {target:#x} from payload start")
    return 0b000101 << 26 | (target // 4) & 0x3FFFFFF


def build(image, dtb, image_load, dtb_load, shim=None, cc_prefix=""):
    if len(image) < 0x20:
        sys.exit("Image too short to be an arm64 kernel image")
    magic = struct.unpack("<I", image[0x18:0x1c])[0]
    if magic != ARM64_IMAGE_MAGIC:
        sys.exit(f"not an arm64 Image (magic at 0x18 is {magic:#x})")
    if image_load % (2 * 1024 * 1024):
        sys.exit("image_load must be 2 MiB aligned (arm64 boot protocol)")
    if dtb_load % PAGE:
        sys.exit("dtb_load must be page aligned")

    first = struct.unpack("<I", image[:4])[0]
    entry_off = decode_b(first)
    if entry_off is None or entry_off <= 0 or entry_off >= 0x1000:
        sys.exit(f"unexpected first Image instruction {first:#010x}")

    image_off = 0
    dtb_off = round_up(len(image), PAGE)
    hdr_off = round_up(dtb_off + len(dtb), PAGE)
    shim_off = hdr_off + PAGE

    # the two destinations may not overlap each other
    for (base_a, size_a, name_a), (base_b, size_b, name_b) in (
        ((image_load, len(image), "image"), (dtb_load, len(dtb), "dtb")),
    ):
        if base_a < base_b + size_b and base_b < base_a + size_a:
            sys.exit(f"{name_a} and {name_b} load ranges overlap")

    if shim is None:
        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, "shim.bin")
            cmd = [sys.executable, os.path.join(HERE, "mkshim.py"), "-o", tmp]
            if cc_prefix:
                cmd += ["--cc-prefix", cc_prefix]
            subprocess.run(cmd, check=True)
            shim = open(tmp, "rb").read()

    payload = bytearray(image)
    payload[0:4] = struct.pack("<I", encode_b(shim_off))
    payload += b"\0" * (dtb_off - len(payload))
    payload += dtb
    payload += b"\0" * (hdr_off - len(payload))
    note = b"flame sm4450 mainline handover\x00"
    hdr = bytearray(HDR.pack(MAGIC, ABI_VERSION, 0, image_off, len(image),
                             dtb_off, len(dtb), image_load, dtb_load,
                             entry_off, shim_off, hdr_off, 0))
    hdr += note
    hdr += b"\0" * (PAGE - len(hdr))
    payload += hdr
    payload += b"\0" * (round_up(shim_off + len(shim), PAGE) - len(payload))
    payload[shim_off:shim_off + len(shim)] = shim
    return bytes(payload), dict(entry_off=entry_off, image_size=len(image),
                                dtb_size=len(dtb), image_off=image_off,
                                dtb_off=dtb_off, hdr_off=hdr_off,
                                shim_off=shim_off, shim_size=len(shim))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="arch/arm64/boot/Image")
    ap.add_argument("--dtb", required=True, help="compiled board .dtb")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--image-load", type=lambda s: int(s, 0), default=0xC0000000)
    ap.add_argument("--dtb-load", type=lambda s: int(s, 0), default=0xBFE00000)
    ap.add_argument("--cc-prefix", default="")
    a = ap.parse_args()

    payload, info = build(open(a.image, "rb").read(), open(a.dtb, "rb").read(),
                          a.image_load, a.dtb_load, cc_prefix=a.cc_prefix)
    with open(a.out, "wb") as f:
        f.write(payload)
    for k in ("entry_off", "image_size", "dtb_size", "dtb_off", "hdr_off",
              "shim_off", "shim_size"):
        print(f"{k:11} = {info[k]:#x} ({info[k]})", file=sys.stderr)
    print(f"{'payload':11} = {len(payload):#x} ({len(payload)}) -> {a.out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()

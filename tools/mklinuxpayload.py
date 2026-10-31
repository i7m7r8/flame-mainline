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
ABI_VERSION = 2
PAGE = 4096
SCRATCH_SIZE = 2 * 1024 * 1024  # record page plus the bootloader's DTB copy
TEXT_OFFSET_MAX = 0x200000      # arm64 images put primary_entry at 0x80000
ARM64_IMAGE_MAGIC = 0x644D5241  # "ARM\x64", u32 at 0x38 of the Image header
IMG_MAGIC_OFF = 0x38
IMG_TEXT_OFFSET_OFF = 0x08
IMG_IMAGE_SIZE_OFF = 0x10
HDR = struct.Struct("<8sIIIIIIQQIIIIQ")  # see the field list below
HDR_OFF_FIELDS = """
  0  magic        "SWEETLNX"
  8  version      u32
 12  flags        u32
 16  image_off    u32   payload-relative
 20  image_size   u32
 24  dtb_off      u32
 28  dtb_size     u32
 32  image_load   u64   physical, must be 2 MiB aligned
 40  dtb_load     u64   physical
 48  entry_off    u32   offset of primary_entry inside the Image
 52  shim_off     u32
 56  hdr_off      u32   == shim_off - 4096
 60  rfu          u32
 64  scratch_load u64   physical, 2 MiB region for the handover record and the
                        copy of the bootloader's DTB; must be reserved without
                        no-map in the board DTS and match
                        CONFIG_ARM64_HANDOVER_CMDLINE
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


def handover_region(dtb):
    """(base, size) of /reserved-memory/handover@<base> in a compiled dtb.

    None if the tree has no such node. This is the flat tree, so it is walked by
    hand: tokens and the header are big endian, strings are NUL terminated and
    padded to four bytes. Only what the check needs is decoded - node names and
    reg, whose cell layout is the two-plus-two the board DTS writes.
    """
    magic, totalsize, off_struct, off_strings = struct.unpack(">4I", dtb[:16])
    if magic != 0xD00DFEED or totalsize > len(dtb):
        sys.exit(f"{len(dtb)} byte dtb is not a flattened tree "
                 f"(magic {magic:#010x}, totalsize {totalsize:#x})")

    def cstr(off):
        return dtb[off_strings + off:dtb.index(b"\0", off_strings + off)].decode()

    def pad(n):
        return n + 1 + (-n - 1) % 4

    p, path, found = off_struct, [], None
    while p < totalsize:
        (token,) = struct.unpack(">I", dtb[p:p + 4])
        p += 4
        if token == 9:				# FDT_END
            break
        if token == 4:				# FDT_NOP
            continue
        if token == 1:				# FDT_BEGIN_NODE
            name = dtb[p:dtb.index(b"\0", p)].decode()
            path.append(name.split("@")[0])
            p += pad(len(name))
        elif token == 2:				# FDT_END_NODE
            path.pop()
        elif token == 3:				# FDT_PROP
            # libfdt's fdt_property is tag, len, nameoff - in that order
            len_, stroff = struct.unpack(">II", dtb[p:p + 8])
            p += 8
            if (path == ["", "reserved-memory", "handover"]
                    and cstr(stroff) == "reg" and len_ == 16):
                found = struct.unpack(">QQ", dtb[p:p + 16])
            p += len_ + (-len_ % 4)
        else:
            sys.exit(f"unknown fdt token {token} at {p - 4:#x}")
    return found


def build(image, dtb, image_load, dtb_load, scratch_load, shim=None, cc_prefix=""):
    if len(image) < 0x40:
        sys.exit("Image too short to carry the arm64 header")
    magic = struct.unpack("<I", image[IMG_MAGIC_OFF:IMG_MAGIC_OFF + 4])[0]
    if magic != ARM64_IMAGE_MAGIC:
        sys.exit(f"not an arm64 Image (magic at {IMG_MAGIC_OFF:#x} is {magic:#x})")
    text_offset = struct.unpack("<Q", image[IMG_TEXT_OFFSET_OFF:IMG_TEXT_OFFSET_OFF + 8])[0]
    eff_size = struct.unpack("<Q", image[IMG_IMAGE_SIZE_OFF:IMG_IMAGE_SIZE_OFF + 8])[0]
    if image_load % (2 * 1024 * 1024):
        sys.exit("image_load must be 2 MiB aligned (arm64 boot protocol)")
    if dtb_load % PAGE:
        sys.exit("dtb_load must be page aligned")
    if scratch_load % SCRATCH_SIZE:
        sys.exit("scratch_load must be 2 MiB aligned (it is the handover region)")

    first = struct.unpack("<I", image[:4])[0]
    entry_off = decode_b(first)
    if entry_off is None or entry_off <= 0 or entry_off >= TEXT_OFFSET_MAX:
        sys.exit(f"unexpected first Image instruction {first:#010x}")
    if entry_off != text_offset:
        sys.exit(f"code0 branches to {entry_off:#x} but text_offset is "
                 f"{text_offset:#x}; the Image would land in the wrong place")
    if eff_size > len(image):
        sys.exit(f"header claims {eff_size:#x} effective bytes, file has {len(image):#x}")

    image_off = 0
    dtb_off = round_up(len(image), PAGE)
    hdr_off = round_up(dtb_off + len(dtb), PAGE)
    shim_off = hdr_off + PAGE

    # the two destinations may not overlap each other, and neither may touch
    # the handover region the shim writes the bootloader's DTB copy into
    for (base_a, size_a, name_a), (base_b, size_b, name_b) in (
        ((image_load, len(image), "image"), (dtb_load, len(dtb), "dtb")),
        ((image_load, len(image), "image"),
         (scratch_load, SCRATCH_SIZE, "handover")),
        ((dtb_load, len(dtb), "dtb"),
         (scratch_load, SCRATCH_SIZE, "handover")),
    ):
        if base_a < base_b + size_b and base_b < base_a + size_a:
            sys.exit(f"{name_a} and {name_b} load ranges overlap")

    # The kernel finds the region by name in this very dtb (find_region in
    # arch/arm64/kernel/handover.c), so a --scratch-load that disagrees with the
    # tree is not a warning: the shim would write where the kernel is not
    # looking, and the bootargs and the ramdisk address would be lost silently.
    region = handover_region(dtb)
    if region is None:
        sys.exit("the dtb has no /reserved-memory/handover node for the shim to "
                 "park the bootloader's tree in")
    if region[0] != scratch_load:
        sys.exit(f"--scratch-load {scratch_load:#x} is not the handover region "
                 f"the dtb names ({region[0]:#x}); the kernel would look in the "
                 f"place the shim did not write")
    if region[1] < SCRATCH_SIZE:
        sys.exit(f"the dtb's handover region is {region[1]:#x} bytes, less than "
                 f"the {SCRATCH_SIZE:#x} the shim may use")

    if shim is None:
        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, "shim.bin")
            cmd = [sys.executable, os.path.join(HERE, "mkshim.py"), "-o", tmp]
            if cc_prefix:
                cmd += ["--cc-prefix", cc_prefix]
            subprocess.run(cmd, check=True)
            shim = open(tmp, "rb").read()
    if len(shim) > PAGE:
        sys.exit(f"shim is {len(shim)} bytes, does not fit its page at {shim_off:#x}")

    payload = bytearray(image)
    payload[0:4] = struct.pack("<I", encode_b(shim_off))
    payload += b"\0" * (dtb_off - len(payload))
    payload += dtb
    payload += b"\0" * (hdr_off - len(payload))
    note = b"flame sm4450 mainline handover\x00"
    hdr = bytearray(HDR.pack(MAGIC, ABI_VERSION, 0, image_off, len(image),
                             dtb_off, len(dtb), image_load, dtb_load,
                             entry_off, shim_off, hdr_off, 0, scratch_load))
    hdr += note
    hdr += b"\0" * (PAGE - len(hdr))
    payload += hdr
    payload += b"\0" * (round_up(shim_off + len(shim), PAGE) - len(payload))
    payload[shim_off:shim_off + len(shim)] = shim
    return bytes(payload), dict(entry_off=entry_off, text_offset=text_offset,
                                image_size=len(image), eff_size=eff_size,
                                dtb_size=len(dtb), image_off=image_off,
                                dtb_off=dtb_off, hdr_off=hdr_off,
                                shim_off=shim_off, shim_size=len(shim),
                                scratch_load=scratch_load)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="arch/arm64/boot/Image")
    ap.add_argument("--dtb", required=True, help="compiled board .dtb")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--image-load", type=lambda s: int(s, 0), default=0xC0000000)
    ap.add_argument("--dtb-load", type=lambda s: int(s, 0), default=0xBFE00000)
    ap.add_argument("--scratch-load", type=lambda s: int(s, 0), default=0xBC000000,
                    help="handover region; must match the reserved-memory node "
                         "in the board DTS and HANDOVER_BASE in setup.c")
    ap.add_argument("--shim", help="prebuilt shim blob (default: assemble "
                                   "tools/shim.S here)")
    ap.add_argument("--cc-prefix", default="")
    a = ap.parse_args()

    shim = open(a.shim, "rb").read() if a.shim else None
    payload, info = build(open(a.image, "rb").read(), open(a.dtb, "rb").read(),
                          a.image_load, a.dtb_load, a.scratch_load,
                          shim=shim, cc_prefix=a.cc_prefix)
    with open(a.out, "wb") as f:
        f.write(payload)
    for k in ("entry_off", "text_offset", "image_size", "eff_size", "dtb_size",
              "dtb_off", "hdr_off", "shim_off", "shim_size", "scratch_load"):
        print(f"{k:12} = {info[k]:#x} ({info[k]})", file=sys.stderr)
    print(f"{'payload':12} = {len(payload):#x} ({len(payload)}) -> {a.out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()

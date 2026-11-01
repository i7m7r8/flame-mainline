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
EFI_SIG_NOP = 0xFA405A4D        # code0 with CONFIG_EFI: ccmp, spelling "MZ"
PLAIN_NOP = 0xD503201F          # code0 without CONFIG_EFI: an ordinary nop
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
    """Offset of an unconditional arm64 `b` encoded in `word` (at pc 0).

    Bits 31..26 separate b from bl: 000101 and 100101 share the low three, so
    the mask has to cover six bits or a call would be mistaken for a jump.
    """
    if (word >> 26) & 0x3F != 0b000101:
        return None
    imm = word & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return imm * 4


def encode_b(from_off, to_off):
    """Encoding of `b` at payload offset from_off, jumping to to_off."""
    disp = to_off - from_off
    if disp % 4 or not (-128 * 1024 * 1024 <= disp < 128 * 1024 * 1024):
        sys.exit(f"cannot branch to {to_off:#x} from {from_off:#x}")
    return 0b000101 << 26 | (disp // 4) & 0x3FFFFFF


def handover_region(dtb):
    """(base, size) of /reserved-memory/handover@<base> in a compiled dtb.

    None if the tree has no such node. This is the flat tree, so it is walked by
    hand: tokens and the header are big endian, strings are NUL terminated and
    padded to four bytes. Only what the check needs is decoded - node names and
    reg, whose cell layout is the two-plus-two the board DTS writes.

    The agreement with arch/arm64/kernel/handover.c has to be exact: both sides
    pick a node by the name "handover" under /reserved-memory, so a tree with
    two of them, or one the kernel would read differently, is refused here
    rather than packed and discovered at boot.
    """
    magic, totalsize, off_struct, off_strings = struct.unpack(">4I", dtb[:16])
    if magic != 0xD00DFEED or totalsize > len(dtb):
        sys.exit(f"{len(dtb)} byte dtb is not a flattened tree "
                 f"(magic {magic:#010x}, totalsize {totalsize:#x})")

    def cstr(off):
        return dtb[off_strings + off:dtb.index(b"\0", off_strings + off)].decode()

    def pad(n):
        return n + 1 + (-n - 1) % 4

    TARGET = ["", "reserved-memory", "handover"]
    p, path, hits, cur = off_struct, [], [], None
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
            cur = {"reg": None, "no_map": False} if path == TARGET else None
            if cur is not None:
                hits.append(cur)
            p += pad(len(name))
        elif token == 2:				# FDT_END_NODE
            cur = None
            path.pop()
        elif token == 3:				# FDT_PROP
            # libfdt's fdt_property is tag, len, nameoff - in that order
            len_, stroff = struct.unpack(">II", dtb[p:p + 8])
            p += 8
            if cur is not None:
                who = cstr(stroff)
                if who == "reg" and len_ == 16:
                    cur["reg"] = struct.unpack(">QQ", dtb[p:p + 16])
                elif who == "no-map":
                    cur["no_map"] = True
            p += len_ + (-len_ % 4)
        else:
            sys.exit(f"unknown fdt token {token} at {p - 4:#x}")

    if not hits:
        return None
    if len(hits) > 1:
        sys.exit(f"the dtb has {len(hits)} /reserved-memory/handover nodes; "
                 "handover.c reads the first one it walks and this packer cannot "
                 "guess which you meant, so name exactly one")
    node = hits[0]
    if node["reg"] is None:
        sys.exit("the dtb's /reserved-memory/handover has no 16 byte reg "
                 "(two address cells plus two size cells)")
    if node["no_map"]:
        sys.exit("the dtb's /reserved-memory/handover is no-map, so after "
                 "paging_init there is no linear map to read the record through "
                 "and the bootloader's /chosen would be silently lost")
    return node["reg"]


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

    # Where the loader's jump ends up, and which word has to be replaced.
    #
    # A gcc-built arm64 Image opens with `efi_signature_nop` in code0 and the
    # branch to primary_entry in code1 (booting.rst: "code0/code1 are
    # responsible for branching to stext"), and since 6.x carries text_offset 0,
    # so the file starts exactly at the 2 MiB aligned base. Patch code1 and keep
    # the signature word intact: the loader jumps to offset 0, executes the NOP,
    # and lands in the shim. An Image that still uses the old code0-branch form
    # is handled too, and anything else is refused rather than guessed at.
    code0, code1 = struct.unpack("<II", image[:8])
    if code0 in (EFI_SIG_NOP, PLAIN_NOP) and decode_b(code1) is not None:
        patch_off, patch_word = 4, code1
    elif decode_b(code0) is not None:
        patch_off, patch_word = 0, code0
    else:
        sys.exit(f"neither code0 {code0:#010x} nor code1 {code1:#010x} is a "
                 f"branch this packer can replace (code0 would be "
                 f"{EFI_SIG_NOP:#010x} or {PLAIN_NOP:#010x} for the nop arm64 "
                 "Images start with, with the branch to primary_entry next)")
    target = decode_b(patch_word)
    if target is None:
        sys.exit(f"word at Image+{patch_off} {patch_word:#010x} is not an "
                 "unconditional branch")
    entry_off = patch_off + target		# absolute, inside the Image
    if not 0 < entry_off < len(image):
        sys.exit(f"the branch at Image+{patch_off} leaves the file: "
                 f"entry would be {entry_off:#x} in a {len(image):#x} byte Image")
    if text_offset:
        sys.exit(f"this Image wants text_offset {text_offset:#x}; the shim copies "
                 "the file to image_load and enters image_load + entry_off, so "
                 "only a text_offset 0 Image can be packed")
    if eff_size > 512 * 1024 * 1024:
        sys.exit(f"header claims {eff_size:#x} effective bytes, which is nonsense "
                 "for an Image this size")
    # The kernel occupies image_load .. +eff_size at runtime (bss included, so
    # eff_size is larger than the file); that whole span has to stay clear of
    # the other destinations, not just the file bytes.
    runtime_size = max(eff_size, len(image))

    image_off = 0
    dtb_off = round_up(len(image), PAGE)
    hdr_off = round_up(dtb_off + len(dtb), PAGE)
    shim_off = hdr_off + PAGE

    # the two destinations may not overlap each other, and neither may touch
    # the handover region the shim writes the bootloader's DTB copy into. The
    # image span is its runtime footprint, not its file length: .bss is zeroed
    # at image_load + file bytes, so a destination inside bss would look free
    # here and be stomped on a few instructions after primary_entry.
    for (base_a, size_a, name_a), (base_b, size_b, name_b) in (
        ((image_load, runtime_size, "image"), (dtb_load, len(dtb), "dtb")),
        ((image_load, runtime_size, "image"),
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
    payload[patch_off:patch_off + 4] = struct.pack("<I", encode_b(patch_off, shim_off))
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

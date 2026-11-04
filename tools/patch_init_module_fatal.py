#!/usr/bin/env python3
"""Take the module-load abort out of the ROM's first stage, in the copy we ship.

Why this file exists. Android's first stage ends its module phase with
`LOG(FATAL) << "Failed to load kernel modules"`, and in this ROM's `/init` that
abort is unconditional on the load result: `Modprobe::LoadListedModules()`
(0x33ce08) clears its accumulator for any entry that fails to load and is not in
`modules.blocklist` (`0x33ceb4 mov w23, wzr`), only an *empty* list escapes
(`0x33ce38 b.eq -> 0x33ceac`), and `FirstStageMain` tests that result alone
(`0x31b328 mov w21, w0`, `0x31b3f0 tbnz w21, #0, 0x31b408`) before falling
through to the fatal at 0x31c960. Our kernel has `CONFIG_MODULES=n`, so all 103
entries in `modules.load` fail with `-ENOSYS`, none are blocklisted, and the
abort is certain. `Insmod` forgives one errno only, EEXIST (`0x33e3d0`), which
does not apply.

The data-level escapes are closed, and by kernel code rather than opinion. The
first stage reads `/lib/modules/modules.load` out of the *vendor* half of the
initramfs, and ABL concatenates our generic half ahead of it, so the vendor
archive is unpacked last - and `clean_path()` in init/initramfs.c:333 resolves
every conflict in favour of the later entry, unlinking or rmdir'ing whatever we
put there first. Writing an empty `modules.load` of our own, or a `lib/modules`
that is a file rather than a directory, therefore cannot win. What *would*
escape is `opendir("/lib/modules")` failing (first_stage_init.cpp:232, an INFO
line and no abort), but that needs the vendor half gone, and the vendor half is
also where the fstab that first-stage mount needs lives.

So the change is one instruction, in the `/init` we already carry: turn that
`tbnz` into the unconditional branch that its own true path takes, which makes
first stage continue exactly as if the modules had loaded. Nothing else in
`/init` reads the result. This is a real behaviour change and it is meant to be
visible: every driver flame needs is built into the Image, so the module phase
has nothing left to do, and a future ROM whose `/init` differs is refused below
rather than silently shipped half-patched.

The bytes come from the ROM, so CI never runs this: it belongs next to
attach_rom_ramdisk.py in the local step. Run it on the ROM's generic ramdisk and
hand the result to that script with --rom-ramdisk.

  python3 tools/patch_init_module_fatal.py ramdisk.img -o ramdisk-patched.img
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mklz4legacy import wrap                    # noqa: E402
from payload_extract import lz4_frame_decompress  # noqa: E402

LZ4_LEGACY = b"\x02\x21\x4c\x18"

# FirstStageMain's module-result test, in the /init of the ROM this port
# targets. /init is static non-PIE with its text segment mapped at
# VA 0x28c000 from file offset 0x8c000, so VA - 0x200000 is the file offset.
VETO_VA = 0x31B3F0
VETO_ELF_DELTA = 0x200000
# tbnz w21, #0, 0x31b408  ->  b 0x31b408, the branch this instruction
# already takes when the loads succeed.
VETO_BEFORE = bytes.fromhex("d5000037")
VETO_AFTER = bytes.fromhex("06000014")

# The shipped /init's size, as a check that we are looking at that binary and
# not at a differently built one whose addresses have moved.
INIT_SIZE = 2763568


def sha(b):
    return hashlib.sha256(b).hexdigest()[:16]


def cpio_walk(cpio):
    """Yield (name, header_offset, data_offset, size) for each newc entry."""
    i = 0
    while cpio[i:i + 6] in (b"070701", b"070702"):
        # The 13 hex fields start right after the 6-byte magic, at offset 6
        # *of this header*, so every read is relative to i.
        field = lambda idx: int(cpio[i + 6 + idx * 8:i + 14 + idx * 8], 16)  # noqa: E731
        size, namesize = field(6), field(11)
        name = cpio[i + 110:i + 110 + namesize - 1].decode("utf8", "replace")
        data = i + 110 + namesize + ((4 - ((110 + namesize) & 3)) & 3)
        yield name, i, data, size
        if name == "TRAILER!!!":
            return
        i = data + size + ((4 - (size & 3)) & 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="the ROM's generic ramdisk (LZ4 legacy or cpio)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    raw = open(a.src, "rb").read()
    compressed = raw[:4] == LZ4_LEGACY
    cpio = lz4_frame_decompress(raw) if compressed else raw

    if cpio[:6] not in (b"070701", b"070702"):
        sys.exit(f"{a.src}: neither an LZ4 legacy frame nor a newc cpio "
                 f"(starts {cpio[:8]!r})")

    inits = [e for e in cpio_walk(cpio) if e[0].lstrip("./") == "init"]
    if len(inits) != 1:
        sys.exit(f"{a.src}: found {len(inits)} entries named init, expected 1")
    _, _, data_off, size = inits[0]
    if size != INIT_SIZE:
        sys.exit(f"{a.src}: /init is {size} bytes, not the {INIT_SIZE} this "
                 "patch was derived from; the addresses below are for that "
                 "build and this ROM may be another one")

    off = data_off + (VETO_VA - VETO_ELF_DELTA)
    here = cpio[off:off + 4]
    if here == VETO_AFTER:
        print(f"{a.src}: already patched (0x{VETO_VA:x} is the unconditional "
              "branch)")
    elif here != VETO_BEFORE:
        sys.exit(f"{a.src}: 0x{VETO_VA:x} holds {here.hex()}, not the "
                 f"tbnz {VETO_BEFORE.hex()} this patch expects; refusing to "
                 "guess")
    else:
        cpio = cpio[:off] + VETO_AFTER + cpio[off + 4:]

    original = cpio if here == VETO_AFTER else (
        cpio[:off] + VETO_BEFORE + cpio[off + 4:])
    diff = sum(1 for x, y in zip(cpio, original) if x != y)

    # Everything but those four bytes has to be the archive we were given.
    if compressed:
        out = wrap(cpio)
        if lz4_frame_decompress(out) != cpio:
            sys.exit("re-framed archive does not read back identically")
    else:
        out = cpio
    open(a.out, "wb").write(out)

    print(f"{a.out}: /init at ramdisk offset 0x{off:x} (VA 0x{VETO_VA:x})")
    print(f"  {VETO_BEFORE.hex()} -> {VETO_AFTER.hex()}"
          f"   tbnz w21,#0 -> b, so a failed vendor insmod no longer aborts")
    print(f"  archive differs in {diff} byte(s), as expected")
    print(f"  ramdisk {sha(raw)} -> {sha(out)}  "
          f"({len(raw)} -> {len(out)} bytes"
          f"{', re-framed literal-only, so larger' if compressed and len(out) > len(raw) else ''})")
    print(f"  next: python3 tools/attach_rom_ramdisk.py --boot <ci.img> "
          f"--rom-ramdisk {a.out} -o <final.img>")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Put the ROM's own first-stage init back into a CI-built boot image.

The Android first stage is /init, and on this device it lives in the generic
ramdisk section of boot.img.  vendor_boot's vendor ramdisk holds only
lib/modules and system/bin (324 entries, no /init), so a boot image packed
with ramdisk_size=0 hands the kernel a rootfs with no init at all: the kernel
unpacks the ramdisk, fails to find /init or /linuxrc, and the device resets.

CI cannot ship those bytes: they come from the ROM, and nothing from the ROM
goes into this repository.  So CI publishes the payload and this script closes
the last step with a file the user already owns:

  python3 tools/attach_rom_ramdisk.py --boot  flame-mainline-boot.img \
      --rom-boot /path/to/stock/boot.img -o out/flame-mainline-boot.img

Everything except the ramdisk section comes from CI: the kernel section stays
byte-identical, and the header fields (os_version, header cmdline) are copied
from the ROM's own image so the result looks like the ROM's generation.
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

PAGE = 4096
HDR_OFF = {
    "kernel_size": 8,
    "ramdisk_size": 12,
    "os_version": 16,
    "header_size": 20,
    "header_version": 40,
    "cmdline": 44,
}
CMDLINE_MAX = 1536


def pageup(n):
    return (n + PAGE - 1) // PAGE * PAGE


def read_boot(path):
    """Return (kernel_bytes, ramdisk_bytes, os_version, cmdline) from a v3/v4 image."""
    data = open(path, "rb").read()
    if data[:8] != b"ANDROID!":
        sys.exit(f"{path}: not an Android boot image (magic {data[:8]!r})")
    u = lambda key: struct.unpack_from("<I", data, HDR_OFF[key])[0]
    ks, rs = u("kernel_size"), u("ramdisk_size")
    hv, hs = u("header_version"), u("header_size")
    if hv not in (3, 4):
        sys.exit(f"{path}: header v{hv}, this script only knows v3 and v4")
    if not 0 < hs <= PAGE:
        sys.exit(f"{path}: header_size {hs} does not fit in the first page")
    if len(data) < PAGE + pageup(ks) + pageup(rs):
        sys.exit(f"{path}: truncated, {len(data)} bytes cannot hold kernel {ks} + ramdisk {rs}")
    cmdline = data[HDR_OFF["cmdline"]:HDR_OFF["cmdline"] + CMDLINE_MAX].split(b"\0")[0]
    return (data[PAGE:PAGE + ks],
            data[PAGE + pageup(ks):PAGE + pageup(ks) + rs],
            u("os_version"),
            cmdline.decode("utf8", "replace"))


def decode_os_version(v):
    """Reverse boot_img_hdr_v3's os_version packing: A B C (Y-2000) M."""
    a, b, c = (v >> 25) & 0x7F, (v >> 18) & 0x7F, (v >> 11) & 0x7F
    year, month = 2000 + ((v >> 4) & 0x7F), v & 0xF
    android = ".".join(str(x) for x in (a, b, c) if x) or str(a)
    return android, f"{year}-{month:02d}"


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--boot", help="CI-built boot image (its kernel section is kept)")
    src.add_argument("--payload", help="payload.bin from CI, packed into a fresh header")
    rom = ap.add_mutually_exclusive_group(required=True)
    rom.add_argument("--rom-boot", help="the ROM's own boot image; its ramdisk is used")
    rom.add_argument("--rom-ramdisk", help="a ramdisk section already extracted by hand")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    if a.boot:
        kernel, _, os_version, cmdline = read_boot(a.boot)
        if not kernel:
            sys.exit(f"{a.boot}: kernel section is empty, nothing to boot")
    else:
        kernel = open(a.payload, "rb").read()
        os_version, cmdline = None, b""

    if a.rom_boot:
        _, ramdisk, rom_os_version, rom_cmdline = read_boot(a.rom_boot)
        if not ramdisk:
            sys.exit(f"{a.rom_boot}: the ROM's boot image carries no generic ramdisk; "
                     "is this the slot the ROM actually boots from?")
    else:
        ramdisk = open(a.rom_ramdisk, "rb").read()
        rom_os_version, rom_cmdline = None, b""
    if not ramdisk:
        sys.exit("empty ramdisk")

    if os_version is None:
        os_version = rom_os_version
    if os_version is None:
        sys.exit("no header to take os_version from; use --boot or --rom-boot")
    if not cmdline:
        cmdline = rom_cmdline

    android, patch = decode_os_version(os_version)
    with tempfile.TemporaryDirectory() as td:
        kp, rp = f"{td}/kernel", f"{td}/ramdisk"
        open(kp, "wb").write(kernel)
        open(rp, "wb").write(ramdisk)
        cmd = f"{td}/cmdline"
        open(cmd, "wb").write(cmdline.encode() + b"\n")
        here = os.path.dirname(os.path.abspath(__file__))
        subprocess.run([sys.executable, f"{here}/mkbootimg_v4.py", "--kernel", kp,
                        "--ramdisk", rp, "--cmdline-file", cmd, "--android", android,
                        "--patch-level", patch, "-o", a.out], check=True)

    out_kernel, out_ramdisk, out_os_version, out_cmdline = read_boot(a.out)
    problems = []
    if out_kernel != kernel:
        problems.append("kernel section changed")
    if out_ramdisk != ramdisk:
        problems.append("ramdisk section changed")
    if out_os_version != os_version:
        problems.append("os_version changed")
    if out_cmdline != cmdline:
        problems.append("header cmdline changed")
    if problems:
        sys.exit(f"{a.out}: " + ", ".join(problems))
    size = os.path.getsize(a.out)
    print(f"{a.out}: kernel {len(kernel)} ramdisk {len(ramdisk)} "
          f"android {android} patch {patch} -> {size} bytes")
    print(f"  /init is present: {len(ramdisk)} bytes of generic ramdisk attached")
    print(f"  flash only to the slot the ROM boots from (see boot-safety.txt)")


if __name__ == "__main__":
    main()

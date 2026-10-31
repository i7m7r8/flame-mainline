#!/usr/bin/env python3
"""Write an Android boot image (header v4) around a prebuilt kernel section.

Only the header layout is taken from the vendor contract
(system/tools/mkbootimg/include/bootimg/bootimg.h, boot_img_hdr_v3/v4); no
stock image is read, so this can run in public CI:

  magic "ANDROID!" | kernel_size | ramdisk_size | os_version | header_size
  | reserved[4] | header_version=4 | cmdline[1536] | signature_size

Sections: header page, kernel, ramdisk, each padded to pagesize (v3/v4 fix
pagesize at 4096). vendor_boot is a separate image and is left alone: the ROM
keeps its own vendor ramdisk, bootconfig and DTBO.
"""
import argparse
import os
import struct
import sys

PAGE = 4096
MAGIC = b"ANDROID!"
HDR_SIZE = 1584  # v4: 1580-byte v3 header + signature_size
CMDLINE_MAX = 1536


def os_version(android, patch_level):
    """A[31:25] B[24:18] C[17:11] (Y-2000)[10:4] M[3:0], per bootimg.h."""
    parts = [int(x) for x in str(android).split(".")]
    parts += [0] * (3 - len(parts))
    v = (parts[0] & 0x7F) << 25 | (parts[1] & 0x7F) << 18 | (parts[2] & 0x7F) << 11
    if patch_level:
        year, month = patch_level.split("-")[:2]
        v |= ((int(year) - 2000) & 0x7F) << 4 | (int(month) & 0xF)
    return v


def pack(kernel, ramdisk=b"", cmdline="", version=17, patch_level=None):
    cmd = cmdline.encode()
    if len(cmd) > CMDLINE_MAX:
        sys.exit(f"cmdline {len(cmd)} bytes > {CMDLINE_MAX}")
    cmd += b"\0" * (CMDLINE_MAX - len(cmd))
    hdr = bytearray()
    hdr += MAGIC
    hdr += struct.pack("<IIII", len(kernel), len(ramdisk),
                       os_version(version, patch_level), HDR_SIZE)
    hdr += struct.pack("<4I", 0, 0, 0, 0)  # reserved
    hdr += struct.pack("<I", 4)            # header_version
    hdr += cmd
    hdr += struct.pack("<I", 0)  # signature_size: no vbmeta chain in this image
    assert len(hdr) == HDR_SIZE, len(hdr)
    out = bytearray(hdr.ljust(PAGE, b"\0"))
    for blob in (kernel, ramdisk):
        out += blob
        out += b"\0" * ((PAGE - len(blob) % PAGE) % PAGE)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True, help="payload from mklinuxpayload.py")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--ramdisk", help="generic ramdisk section (optional)")
    ap.add_argument("--cmdline-file", help="one-line kernel command line")
    ap.add_argument("--android", type=int, default=17)
    ap.add_argument("--patch-level", default=None, metavar="YYYY-MM-DD")
    a = ap.parse_args()

    kernel = open(a.kernel, "rb").read()
    ramdisk = open(a.ramdisk, "rb").read() if a.ramdisk else b""
    cmdline = ""
    if a.cmdline_file:
        cmdline = open(a.cmdline_file).read().strip()
        if "\n" in cmdline:
            sys.exit("cmdline file must be a single line")
    img = pack(kernel, ramdisk, cmdline, a.android, a.patch_level)
    with open(a.out, "wb") as f:
        f.write(img)
    print(f"boot v4: kernel={len(kernel)} ramdisk={len(ramdisk)} "
          f"cmdline={len(cmdline)} -> {a.out} ({len(img)} bytes)",
          file=sys.stderr)


if __name__ == "__main__":
    main()

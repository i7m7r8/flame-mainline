#!/usr/bin/env python3
"""Repack an Android boot image (header v3/v4) with a new ramdisk."""
import argparse, struct, sys

PAGE = 4096


def pad(b):
    return b + b"\0" * ((PAGE - len(b) % PAGE) % PAGE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("boot")
    ap.add_argument("ramdisk")
    ap.add_argument("out")
    ap.add_argument("--cmdline", default=None)
    a = ap.parse_args()

    raw = open(a.boot, "rb").read()
    if raw[:8] != b"ANDROID!":
        sys.exit("not an Android boot image")
    ks, rs, osv, hs = struct.unpack("<IIII", raw[8:24])
    hv = struct.unpack("<I", raw[40:44])[0]
    if hv < 3:
        sys.exit(f"unsupported header version {hv}")

    hdr = bytearray(raw[:hs])
    kernel_off = PAGE
    kernel = raw[kernel_off:kernel_off + ks]
    if len(kernel) != ks:
        sys.exit("short kernel")

    ramdisk = open(a.ramdisk, "rb").read()
    struct.pack_into("<I", hdr, 8, ks)
    struct.pack_into("<I", hdr, 12, len(ramdisk))
    if a.cmdline is not None:
        cmd = a.cmdline.encode()
        if len(cmd) > 1535:
            sys.exit("cmdline too long")
        hdr[44:44 + 1536] = cmd + b"\0" * (1536 - len(cmd))

    img = pad(bytes(hdr)) + pad(kernel) + pad(ramdisk)
    # header v4 boot signature is not produced; stock has none either
    with open(a.out, "wb") as f:
        f.write(img)

    print(f"{a.out}: kernel {ks}, ramdisk {len(ramdisk)}, "
          f"header v{hv}, os_version {osv:#x}, total {len(img)}")
    print(f"cmdline: {bytes(hdr[44:1580]).split(b'\0')[0].decode()!r}")


if __name__ == "__main__":
    main()

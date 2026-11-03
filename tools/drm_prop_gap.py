#!/usr/bin/env python3
"""Which DRM properties does the ROM's display HAL know that mainline msm does not create?

Three inputs, all read from files this repo already keeps or from the kernel
tree it patches:

  * the vendor display driver, out/re/ko/msm_drm.ko - every name in here is one
    the stock kernel really exposed,
  * the HAL that consumed them, libsdedrm.so (extracted from the ROM's vendor
    partition; expected at $LIBSDEDRM or /root/work/vaso/libsdedrm.so),
  * mainline's drivers/gpu/drm/msm, as a set of quoted identifiers.

The interesting set is (driver strings AND HAL strings) minus mainline strings:
names the two agree on that this kernel will not have. It is a superset of the
property names - the filter is deliberately loose, so ELF section names, libc
symbols and log fragments leak in too and are listed anyway rather than
guessed away by a hand-written allow-list. Run it, read the output, and drop
the obvious noise.
"""
import os
import re
import sys

KO = os.environ.get("MSM_DRM_KO", "/root/flame-mainline/out/re/ko/msm_drm.ko")
SO = os.environ.get("LIBSDEDRM", "/root/work/vaso/libsdedrm.so")
MSM = os.environ.get("MSM_SRC", "/root/dl/linux-7.1.2/drivers/gpu/drm/msm")


def file_words(path, lo=3, hi=32):
    """NUL-terminated runs of [a-z0-9_] - the shape a DRM property name has."""
    with open(path, "rb") as f:
        data = f.read()
    return {m.group(0)[:-1].decode()
            for m in re.finditer(rb"[a-z0-9_]{%d,%d}\x00" % (lo, hi), data)}


def mainline_words(root):
    words = set()
    for dirpath, _, files in os.walk(root):
        for name in files:
            if not name.endswith((".c", ".h")):
                continue
            with open(os.path.join(dirpath, name), errors="ignore") as f:
                words |= set(re.findall(r'"([a-z0-9_]{3,32})"', f.read()))
    return words


def main():
    for path in (KO, SO):
        if not os.path.exists(path):
            print("missing input: %s (set %s)" % (path, "MSM_DRM_KO/LIBSDEDRM"),
                  file=sys.stderr)
            return 1
    ko, so, mainline = file_words(KO), file_words(SO), mainline_words(MSM)
    gap = (ko & so) - mainline
    print("msm_drm.ko words %d | libsdedrm.so words %d | mainline msm words %d"
          % (len(ko), len(so), len(mainline)))
    print("agreed on by driver and HAL, absent from mainline: %d" % len(gap))
    for w in sorted(gap):
        print("   ", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())

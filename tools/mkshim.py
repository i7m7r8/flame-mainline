#!/usr/bin/env python3
"""Assemble the flame handover shim (tools/shim.S) into a flat aarch64 blob.

The shim has no relocations left after `as` (its only address reference is a
self-referential `adr`), so objcopy of the .text section is the whole blob.
Kept separate from mklinuxpayload.py so it can be unit tested and disassembled
on its own.
"""
import argparse
import os
import shutil
import subprocess
import sys

SIZE_LIMIT = 4096  # the shim's own page; mklinuxpayload places it at shim_off


def build(src, out, cc_prefix=""):
    """Assemble `src` into a flat blob at `out`; cc_prefix is e.g.
    'aarch64-linux-gnu-' (empty means the host toolchain, arm64 hosts only)."""
    asbin, objcopy, objdump = (f"{cc_prefix}{t}" for t in ("as", "objcopy", "objdump"))
    for tool in (asbin, objcopy, objdump):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} not found: install binutils-aarch64-linux-gnu")
    obj = out + ".o"
    subprocess.run([asbin, "-o", obj, src], check=True)
    # no relocations may survive: a dangling reloc means the blob is not
    # position independent and would jump into garbage on a real device
    r = subprocess.run([objdump, "-r", obj],
                       capture_output=True, text=True, check=True).stdout
    if "R_AARCH64" in r.split("RELOCATION RECORDS", 1)[-1]:
        sys.exit(f"shim is not position independent:\n{r}")
    subprocess.run([objcopy, "-O", "binary", "--only-section=.text", obj, out],
                   check=True)
    size = os.path.getsize(out)
    if size > SIZE_LIMIT:
        sys.exit(f"shim is {size} bytes, does not fit in {SIZE_LIMIT}")
    os.remove(obj)
    return size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "shim.S"))
    ap.add_argument("-o", "--out", default="/dev/stdout")
    ap.add_argument("--cc-prefix", default="",
                    help="cross toolchain prefix, e.g. aarch64-linux-gnu- "
                         "(empty = native, only works on an arm64 host)")
    a = ap.parse_args()
    if a.out == "/dev/stdout":
        tmp = a.src + ".bin"
        size = build(a.src, tmp, a.cc_prefix)
        sys.stdout.buffer.write(open(tmp, "rb").read())
        os.remove(tmp)
        print(f"shim: {size} bytes", file=sys.stderr)
    else:
        print(f"shim: {build(a.src, a.out, a.cc_prefix)} bytes -> {a.out}",
              file=sys.stderr)


if __name__ == "__main__":
    main()

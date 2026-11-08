"""Pull the shipped ramdisk out of flame-mainline-final.img, decompress it and
hand QEMU a bare newc cpio, proving on the way that the /init inside it is the
one with the module-phase veto skipped."""
import os
import sys

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.environ.get("FLAME_QEMU_OUT", "/tmp/flame-qemu")
os.makedirs(OUT, exist_ok=True)

from attach_rom_ramdisk import read_boot
from payload_extract import lz4_frame_decompress
import patch_init_module_fatal as P

SRC = os.environ.get("FLAME_ARTIFACT", "out/flame-mainline-final.img")
# run.sh's usage line passes the unpacked artifact *directory*; read_boot wants
# the image itself. Accept both, so the documented invocation works.
if os.path.isdir(SRC):
    SRC = os.path.join(SRC, "flame-mainline-final.img")
kern, ram, osv, cmdline = read_boot(SRC)
cpio = lz4_frame_decompress(ram)
print(f"ramdisk section {len(ram)} B -> cpio {len(cpio)} B")

entries = list(P.cpio_walk(cpio))
print(f"cpio entries: {len(entries)}, last is {entries[-1][0]!r}")
hits = [e for e in entries if e[0] in ("init", "./init")]
for name, hoff, doff, size in hits:
    at = cpio[doff + P.VETO_VA - P.VETO_ELF_DELTA:
              doff + P.VETO_VA - P.VETO_ELF_DELTA + 4]
    ok = ("PATCHED (veto skipped)" if at == P.VETO_AFTER
          else f"NOT patched, bytes {at.hex()} want {P.VETO_AFTER.hex()}")
    print(f"/init: size {size} ({'expected' if size == P.INIT_SIZE else 'UNEXPECTED'}), {ok}")

want = {b"usr", b"system", b"first_stage_ramdisk", b"init"}
top = sorted({e[0].split("/")[0] for e in entries})[:12]
print("top-level entries:", top)
open(f"{OUT}/initrd.cpio", "wb").write(cpio)
print(f"wrote {OUT}/initrd.cpio")

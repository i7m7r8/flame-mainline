"""Build the harness overlay around the compiled probe.

diaginit is /tmp/qemuvm/probe (static aarch64, built from probe.c with
aarch64-linux-gnu-gcc -static -O1), not the shell script in mkinitrd.py: the
ROM's toolbox_ramdisk has no working shell for what we need, and a binary can
mount proc/sysfs/devtmpfs itself before reading them.

The overlay is concatenated AFTER the shipped ramdisk, so its
system/etc/fstab.default replaces the ROM's copy for the harness run.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.environ.get("FLAME_QEMU_OUT", "/tmp/flame-qemu")
os.makedirs(OUT, exist_ok=True)


from mkinitrd import APPLETS, FSTAB, TOOLBOX, entry  # noqa: E402



def build():
    out, tool = [], open(TOOLBOX, "rb").read()
    probe = open(f"{OUT}/probe", "rb").read()
    entry(out, "bin", 0o040755)
    for a in APPLETS:
        entry(out, f"bin/{a}", 0o100755, tool)
    entry(out, "diaginit", 0o100755, probe)
    entry(out, "system/etc", 0o040755)
    entry(out, "system/etc/fstab.default", 0o100644, FSTAB.encode())
    entry(out, "TRAILER!!!", 0o100644)
    blob = b"".join(out)
    blob += b"\0" * ((512 - len(blob) % 512) % 512)
    open(f"{OUT}/overlay.cpio", "wb").write(blob)

    combined = open(f"{OUT}/initrd.cpio", "rb").read() + blob
    open(f"{OUT}/initrd-test.cpio", "wb").write(combined)
    print(f"overlay.cpio {len(blob)} B  probe {len(probe)} B  "
          f"initrd-test.cpio {len(combined)} B")


if __name__ == "__main__":
    build()

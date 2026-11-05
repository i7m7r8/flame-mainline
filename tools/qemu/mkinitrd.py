"""Build the harness overlay initrd.

Two archives are concatenated; the kernel unpacks them in order into one root,
and a later regular file replaces an earlier one:

  1. the CI image's ramdisk   - the /init we actually ship, patched
  2. overlay.cpio (this file) - a shell, a probe script, and the fstab

The ROM's vendor_ramdisk.cpio is deliberately NOT packed in: it is 43 MB and
the only thing first stage needs from it is system/etc/fstab.default, which the
overlay supplies. That keeps the unpacked root inside a 320 MB guest - the
first attempt failed with "Initramfs unpacking failed: write error", which is
ENOSPC from unpacking 133 MB into a box with 129 MB available.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.environ.get("FLAME_QEMU_OUT", "/tmp/flame-qemu")
os.makedirs(OUT, exist_ok=True)

TOOLBOX = f"{OUT}/toolbox_ramdisk"

# Only what the probe below actually calls. Each applet is a full 818 KB copy,
# so this list is the size of the harness.
APPLETS = ["sh", "ls", "cat", "mkdir", "mount", "umount", "grep", "head", "tr",
           "basename", "uname", "stat", "sleep", "dmesg", "lsblk", "wc"]

PROBE = r"""
#!/bin/sh
echo "===== P1 machine ====="
uname -a
cat /proc/device-tree/model 2>/dev/null; echo
echo "dt soc node: $(ls -d /proc/device-tree/soc* 2>/dev/null)"
echo "===== P2 /sys/devices/platform entries (names Android derives paths from) ====="
for d in /sys/devices/platform/*; do echo "  $(basename $d)"; done
echo "===== P3 block devices and their sysfs paths ====="
for b in /sys/class/block/*; do
    [ -e "$b/uevent" ] || continue
    echo "  $(basename $b): ${b#/sys}"
    echo "      $(tr '\n' ' ' < $b/uevent)"
done
echo "===== P4 the platform path a partition lives under ====="
for p in /sys/devices/platform/*/*/block/* /sys/devices/platform/*/block/*; do
    [ -e "$p" ] || continue
    echo "  $p"
done
echo "===== P5 mount test on the GPT disk ====="
mkdir -p /mnt/probe
for i in 1 2 3 4 5 6 7 8 9; do
    d=/dev/vdb$i
    [ -e "$d" ] || continue
    if mount -t ext4 -o ro "$d" /mnt/probe 2>/dev/null; then
        echo "  vdb$i: mounted, labels $(ls /mnt/probe | head -2 | tr '\n' ' ')"
        umount /mnt/probe
    else
        echo "  vdb$i: node exists, no filesystem (labels we left empty)"
    fi
done
echo "===== P6 by-name before init runs ====="
ls -l /dev/block/by-name 2>&1 | head -2
echo "===== P7 cmdline ====="
cat /proc/cmdline
echo "===== PROBE DONE (idling; the runner times this out) ====="
sleep 3600
"""

FSTAB = """# Harness fstab: three of the ROM's own fstab.default mount points and
# fs_mgr flags, reduced to what the fake disk can serve. Dropped from the real
# file: `logical` (the harness GPT has no LP metadata, so system/vendor/metadata
# are ordinary partitions here), `slotselect` and the `_a` suffixes (every
# PARTNAME on the harness disk is unsuffixed), and all `avb=` (on the phone ABL
# handles that, and /init skips AVB anyway with no
# /proc/device-tree/firmware/android node).
/dev/block/by-name/system   /system   ext4 ro,barrier=1,discard  wait,first_stage_mount
/dev/block/by-name/vendor   /vendor   ext4 ro,barrier=1,discard  wait,first_stage_mount
/dev/block/by-name/metadata /metadata ext4 noatime,nosuid,nodev  wait,check,formattable,first_stage_mount
/dev/block/by-name/misc     /misc     emmc defaults              defaults
"""


def entry(out, name, mode, data=b"", ino=[0]):
    ino[0] += 1
    fields = [ino[0], mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name) + 1, 0]
    out.append(b"070701" + "".join("%08x" % f for f in fields).encode())
    out.append(name.encode() + b"\0")
    out.append(b"\0" * ((-((110 + len(name) + 1) & 3)) & 3))
    out.append(data)
    out.append(b"\0" * ((-len(data) & 3)))


def build():
    out, tool = [], open(TOOLBOX, "rb").read()
    entry(out, "bin", 0o040755)
    for a in APPLETS:
        entry(out, f"bin/{a}", 0o100755, tool)
    entry(out, "diaginit", 0o100755, PROBE.lstrip().encode())
    entry(out, "system/etc", 0o040755)
    entry(out, "system/etc/fstab.default", 0o100644, FSTAB.encode())
    entry(out, "TRAILER!!!", 0o100644)
    blob = b"".join(out)
    blob += b"\0" * ((512 - len(blob) % 512) % 512)
    open(f"{OUT}/overlay.cpio", "wb").write(blob)
    print(f"overlay.cpio: {len(blob)} bytes ({len(APPLETS)} applets x {len(tool)} B)")


if __name__ == "__main__":
    if not os.path.exists(TOOLBOX):
        import patch_init_module_fatal as P
        cpio = open(f"{OUT}/initrd.cpio", "rb").read()
        for name, hoff, doff, size in P.cpio_walk(cpio):
            if name.endswith("toolbox_ramdisk"):
                open(TOOLBOX, "wb").write(cpio[doff:doff + size])
                print(f"toolbox from {name}: {size} bytes")
                break
        else:
            sys.exit("no toolbox_ramdisk in the shipped ramdisk")
    build()

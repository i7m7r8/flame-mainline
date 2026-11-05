"""Build the fake UFS for the QEMU harness.

disk-gpt.img : one disk, GPT, whose PARTNAMEs are the labels this ROM's fstab
               asks for. Android's first stage turns PARTNAME into
               /dev/block/by-name/<label>, gated on androidboot.boot_devices,
               which is the mechanism under test.
disk-bare.img: a plain ext4 disk with no partition table, as a control (it must
               produce no PARTNAME and therefore no by-name entry).

The GPT is written by hand because this host has no sgdisk/parted/fdisk. The
guest kernel is the verifier: a malformed table simply yields no partitions.
"""
import binascii
import os
import struct
import subprocess
import uuid

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
OUT = os.environ.get("FLAME_QEMU_OUT", "/tmp/flame-qemu")
os.makedirs(OUT, exist_ok=True)

L = 512
GENERIC_DATA = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
# sizeof(gpt_entry) in include/linux/partition.h: 16 type + 16 uuid + 8+8+8 lba/flags
# + 72 name. The kernel strides the array by exactly this, so an entry that is
# any other size makes every partition after the first one garbage.
SIZEOF_ENTRY = 128


def efi_guid(text):
    d = bytes.fromhex(text.replace("-", ""))
    return d[0:4][::-1] + d[4:6][::-1] + d[6:8][::-1] + d[8:16]


def put_name(name):
    raw = name.encode("utf-16-le") + b"\x00\x00"
    assert len(raw) <= 72, name
    return raw.ljust(72, b"\0")


def mkfs(label, size_mb):
    path = f"{OUT}/{label}.fs"
    with open(path, "wb") as f:
        f.truncate(size_mb * 1024 * 1024)
    subprocess.run(["mkfs.ext4", "-q", "-F", "-L", label, path], check=True)
    return path


def build(spec, total_mb, target):
    sectors = total_mb * 1024 * 1024 // L
    last_lba = sectors - 1
    entries_lba, first_usable = 2, 34
    backup_entries = last_lba - 32
    cursor, parts = first_usable, []
    for name, backing, mb in spec:
        n = mb * 1024 * 1024 // L
        parts.append((name, backing, cursor, cursor + n - 1))
        cursor += n
    last_usable = sectors - 34 - 1
    assert parts[-1][3] <= last_usable, f"{parts[-1][3]} > {last_usable}"

    ent = b""
    for name, backing, start, end in parts:
        ent += efi_guid(GENERIC_DATA) + efi_guid(str(uuid.uuid4()))
        ent += struct.pack("<QQQ", start, end, 0) + put_name(name)
        assert len(ent) % SIZEOF_ENTRY == 0
    ent = ent.ljust(128 * 128, b"\0")

    # The kernel CRCs exactly header_size bytes, not the padded sector
    # (block/partitions/efi.c is_gpt_valid -> efi_crc32(hdr, hdr->header_size)),
    # and each header must name *itself* in my_lba, so the backup is a separate
    # structure with my_lba/alternate_lba/entries_lba swapped, not a copy.
    ent_crc = binascii.crc32(ent) & 0xFFFFFFFF
    guid_disk = efi_guid(str(uuid.uuid4()))

    def header(my_lba, alt_lba, entries_at):
        h = bytearray(b"EFI PART" + struct.pack("<IIII", 0x10000, 92, 0, 0))
        h += struct.pack("<QQ", my_lba, alt_lba)
        h += struct.pack("<QQ", first_usable, last_usable)
        h += guid_disk
        h += struct.pack("<QIII", entries_at, 128, 128, ent_crc)
        assert len(h) == 92, len(h)
        h[16:20] = struct.pack("<I", binascii.crc32(bytes(h)) & 0xFFFFFFFF)
        return bytes(h).ljust(L, b"\0")

    hdr = header(1, last_lba, entries_lba)
    hdr_b = header(last_lba, 1, backup_entries)

    mbr = bytearray(L)
    mbr[0x1BE + 4] = 0xEE
    # partition_record is 16 B: boot(1) chs(3) type(1) chs(3) start_lba(4) size(4),
    # so the addresses live at +8 and +12 of the record, not +12 and +16.
    mbr[0x1BE + 8:0x1BE + 12] = struct.pack("<I", 1)
    mbr[0x1BE + 12:0x1BE + 16] = struct.pack("<I", min(sectors - 1, 0xFFFFFFFF))
    mbr[0x1FE:0x200] = b"\x55\xaa"

    with open(target, "wb") as f:
        f.truncate(sectors * L)
        f.seek(0), f.write(bytes(mbr))
        f.seek(L), f.write(hdr)
        f.seek(entries_lba * L), f.write(ent)
        f.seek(last_lba * L), f.write(hdr_b)
        f.seek(backup_entries * L), f.write(ent)
        for name, backing, start, end in parts:
            if backing:
                f.seek(start * L)
                f.write(open(backing, "rb").read())
            print(f"  {name:<16} lba {start:>7}..{end:>7}"
                  f"{'' if backing else '  (no filesystem)'}")
    print(f"{target}: {sectors} sectors, {len(parts)} partitions")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print("[1/2] ext4 payloads")
    fs = {n: mkfs(n, mb) for n, mb in [("system", 48), ("vendor", 48),
                                       ("metadata", 8), ("userdata", 16)]}
    print("[2/2] partitioned disk, PARTNAMEs as the fstab spells them")
    build([("system", fs["system"], 48), ("system_ext", None, 16),
           ("product", None, 16), ("vendor", fs["vendor"], 48),
           ("vendor_dlkm", None, 16), ("system_dlkm", None, 16), ("odm", None, 16),
           ("metadata", fs["metadata"], 8), ("userdata", fs["userdata"], 16),
           ("super", None, 32), ("boot_a", None, 4), ("vendor_boot_a", None, 4),
           ("dtbo_a", None, 4), ("recovery_a", None, 4), ("misc", None, 1)],
          total_mb=320, target=f"{OUT}/disk-gpt.img")
    subprocess.run(["dd", "if=/dev/zero", f"of={OUT}/disk-bare.img",
                    "bs=1M", "count=32", "status=none"], check=True)
    subprocess.run(["mkfs.ext4", "-q", "-F", f"{OUT}/disk-bare.img"], check=True)
    print(f"{OUT}/disk-bare.img: plain ext4, no table, so it must yield no PARTNAME")

"""Independent check of disk-gpt.img, mirroring block/partitions/efi.c.

This is the harness's own verifier: the guest kernel is the authority, but it
only says "no partitions" (every rejection in is_gpt_valid is pr_debug). So the
checks are reproduced here in the order the kernel runs them, and the first
failure printed is the reason the disk yielded no /dev/vdaN and therefore no
/dev/block/by-name/<PARTNAME>.
"""
import binascii
import os
import struct
import sys

L = 512
SIG = b"EFI PART"
SIZEOF_GPT_ENTRY = 128


def efi_crc32(buf):
    return binascii.crc32(buf) & 0xFFFFFFFF


def main(path):
    data = open(path, "rb").read()
    sectors = len(data) // L
    last_lba = sectors - 1
    print(f"{path}: {len(data)} B = {sectors} sectors, last_lba={last_lba}")

    # --- find_valid_gpt(): primary, then backup ---
    for name, lba in (("primary", 1), ("backup", last_lba)):
        sec = data[lba * L:(lba + 1) * L]
        if len(sec) != L:
            print(f"FAIL {name}: no sector at lba {lba}")
            continue
        sig = sec[:8]
        _rev, hsize, crc_stored = struct.unpack("<III", sec[8:20])
        my_lba, alt_lba = struct.unpack("<QQ", sec[24:40])
        first_u, last_u = struct.unpack("<QQ", sec[40:56])
        ents_lba, num, entsize, arr_crc = struct.unpack("<QIII", sec[72:92])
        if sig != SIG:
            print(f"FAIL {name}: signature {sig!r} != b'EFI PART'")
            continue
        if hsize > L or hsize < 92:
            print(f"FAIL {name}: header_size {hsize} not in [92, {L}]")
            continue
        # is_gpt_valid zeroes the crc field in the buffer *before* hashing.
        calc = efi_crc32(sec[:16] + b"\0\0\0\0" + sec[20:hsize])
        if calc != crc_stored:
            print(f"FAIL {name}: header crc {calc:#x} != stored {crc_stored:#x}")
            continue
        if my_lba != lba:
            print(f"FAIL {name}: my_lba {my_lba} != {lba}")
            continue
        if first_u > last_lba or last_u > last_lba or last_u < first_u:
            print(f"FAIL {name}: usable range {first_u}..{last_u} vs disk 0..{last_lba}")
            continue
        if entsize != SIZEOF_GPT_ENTRY:
            print(f"FAIL {name}: sizeof_partition_entry {entsize} != {SIZEOF_GPT_ENTRY}")
            continue
        arr = data[ents_lba * L:ents_lba * L + num * entsize]
        if len(arr) != num * entsize:
            print(f"FAIL {name}: entry array runs off the disk")
            continue
        if efi_crc32(arr) != arr_crc:
            print(f"OK   {name} header, but entry array crc "
                  f"{efi_crc32(arr):#x} != {arr_crc:#x}")
            continue
        print(f"OK   {name} header valid; entries at lba {ents_lba}, {num} x {entsize}")
        if name == "primary":
            break

    # --- is_pmbr_valid ---
    mbr = data[:L]
    if struct.unpack("<H", mbr[0x1FE:0x200])[0] != 0xAA55:
        print("FAIL pmbr: no 0x55AA signature")
    else:
        for i in range(4):
            r = mbr[0x1BE + 16 * i:0x1BE + 16 * (i + 1)]
            if r[4] == 0xEE:
                start, size = struct.unpack("<II", r[8:16])
                ok = start == 1
                print(f"pmbr record {i}: type 0xEE starting_lba={start} sectors={size} "
                      f"-> {'OK' if ok else 'FAIL (kernel wants starting_lba == 1)'}")

    # --- the entry array itself, as efi_partition() reads it ---
    ents_lba = struct.unpack("<Q", data[1 * L + 72:1 * L + 80])[0]
    num, entsize = struct.unpack("<II", data[1 * L + 80:1 * L + 88])
    print(f"\nentries as the kernel strides them ({entsize} B apart, from lba {ents_lba}):")
    for i in range(num):
        e = data[ents_lba * L + i * entsize:ents_lba * L + (i + 1) * entsize]
        if len(e) < SIZEOF_GPT_ENTRY or not any(e[:16]):
            continue
        start, end = struct.unpack("<QQ", e[32:48])
        raw = e[56:128]
        name = raw.decode("utf-16-le", "replace").split("\x00")[0]
        nul = raw.find(b"\x00\x00")
        name = raw[:nul if nul >= 0 else 72].decode("utf-16-le", "replace")
        flag = "OK " if start <= end and start >= 34 else "BAD"
        print(f"  [{i:2d}] {flag} lba {start:>7}..{end:>7}  PARTNAME={name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else
                      os.environ.get("FLAME_QEMU_OUT", "/tmp/flame-qemu") + "/disk-gpt.img"))

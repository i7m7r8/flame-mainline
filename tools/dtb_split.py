#!/usr/bin/env python3
"""Split a Qualcomm/Android dtbo.img (or any blob full of flattened DTBs) into
individual .dtb files, then report each one's /model, /compatible and
qcom,board-id so a board's own DTB can be picked out.

  python3 tools/dtb_split.py out/dtbo.img out/dtbs
"""
import re
import struct
import subprocess
import sys
import os

DTB_MAGIC = 0xD00DFEED


def find_dtbs(buf):
    """Every plausible big-endian DTB header: total_size must fit the buffer."""
    hits = []
    for m in re.finditer(struct.pack(">I", DTB_MAGIC), buf):
        off = m.start()
        total = struct.unpack_from(">I", buf, off + 4)[0]
        if 512 <= total <= len(buf) - off:
            hits.append((off, total))
    # drop hits that live inside a previous DTB
    kept, end = [], -1
    for off, total in hits:
        if off >= end:
            kept.append((off, total))
            end = off + total
    return kept


def strings_of(dtb_path, node_regex):
    """Ask dtc for the DTS and pull the first matching line."""
    dts = subprocess.run(["dtc", "-q", "-I", "dtb", "-O", "dts", dtb_path],
                         capture_output=True).stdout.decode("utf-8", "replace")
    for line in dts.splitlines():
        if node_regex.search(line):
            return line.strip()
    return None


def main(img, outdir):
    buf = open(img, "rb").read()
    os.makedirs(outdir, exist_ok=True)
    hits = find_dtbs(buf)
    print(f"{img}: {len(buf)} bytes, {len(hits)} DTBs")
    for i, (off, total) in enumerate(hits):
        path = os.path.join(outdir, f"dtb-{i:03d}.dtb")
        open(path, "wb").write(buf[off:off + total])
        model = strings_of(path, re.compile(r"^\s*model\s*="))
        board = strings_of(path, re.compile(r"^\s*qcom,board-id\s*="))
        soc = strings_of(path, re.compile(r"^\s*qcom,soc-id\s*="))
        print(f"  {i:03d} @{off:#x} {total:>9} {model or ''}")
        if board:
            print(f"       {board}  {soc or ''}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

#!/usr/bin/env python3
"""Build a gzip-compressed newc cpio (Linux initramfs) from a directory."""
import argparse, gzip, io, os, stat, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mklz4legacy import wrap as lz4_wrap


def newc_entry(name, ino, mode, data=b"", rdev=0, nlink=1):
    namez = name.encode() + b"\0"
    fields = [
        ino & 0xFFFFFFFF, mode & 0xFFFFFFFF, 0, 0, nlink & 0xFFFFFFFF, 0,
        len(data), 0, 0, rdev >> 8, rdev & 0xFF, len(namez), 0,
    ]
    hdr = b"070701" + b"".join(b"%08X" % f for f in fields)
    out = hdr + namez
    out += b"\0" * ((4 - len(out) % 4) % 4)
    out += data
    out += b"\0" * ((4 - len(out) % 4) % 4)
    return out


def build(root):
    buf = io.BytesIO()
    ino = 1
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        rel = os.path.relpath(dirpath, root)
        entries.append((dirpath, rel))
        for d in dirnames:
            entries.append((os.path.join(dirpath, d), os.path.join(rel, d) if rel != "." else d))
        for f in filenames:
            entries.append((os.path.join(dirpath, f), os.path.join(rel, f) if rel != "." else f))
    seen = set()
    for path, rel in entries:
        if rel in seen:
            continue
        seen.add(rel)
        name = "." if rel == "." else rel
        st = os.lstat(path)
        ino += 1
        if stat.S_ISDIR(st.st_mode):
            buf.write(newc_entry(name, ino, stat.S_IFDIR | 0o755, b"", 0, 2))
        elif stat.S_ISLNK(st.st_mode):
            buf.write(newc_entry(name, ino, 0o777, os.readlink(path).encode(), 0, 1))
        elif stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
            typ = stat.S_IFCHR if stat.S_ISCHR(st.st_mode) else stat.S_IFBLK
            buf.write(newc_entry(name, ino, typ | (st.st_mode & 0o777), b"",
                                 os.major(st.st_rdev) << 8 | os.minor(st.st_rdev), 1))
        else:
            with open(path, "rb") as fh:
                data = fh.read()
            buf.write(newc_entry(name, ino, stat.S_IFREG | (st.st_mode & 0o777), data, 0, 1))
    buf.write(newc_entry("TRAILER!!!", ino + 1, 0, b"", 0, 1))
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("out")
    ap.add_argument("--lz4legacy", action="store_true",
                    help="Android LZ4 legacy frame instead of gzip")
    a = ap.parse_args()
    cpio = build(a.root)
    if a.lz4legacy:
        blob = lz4_wrap(cpio)
        with open(a.out, "wb") as f:
            f.write(blob)
        kind = "lz4-legacy"
    else:
        with open(a.out, "wb") as f:
            with gzip.GzipFile(fileobj=f, mode="wb", compresslevel=9, mtime=0) as gz:
                gz.write(cpio)
        blob = open(a.out, "rb").read()
        kind = "gzip"
    print(f"{a.out}: {len(cpio)} bytes cpio -> {len(blob)} bytes {kind}")


if __name__ == "__main__":
    main()

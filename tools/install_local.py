#!/usr/bin/env python3
"""Install this repo's out-of-tree sources into a kernel tree.

Board drivers we write ourselves (panel-xiaomi-c3f and friends) live under
patches/local/files/ mirroring their kernel path, and patches/local/manifest
describes the Kconfig/Makefile/driver-table lines that select them, one rule
per line:

  FILE | anchor regex | before|after | MARKER | text (\t and \n are escapes)

MARKER is skipped-if-present so reruns and future kernels that already carry
the change stay quiet. Doing these edits by anchored insert rather than a
.patch keeps them working across kernel point releases, where the context
lines around DRM_PANEL_SIMPLE drift.

  python3 tools/install_local.py <kernel-src> patches/local
"""
import re
import shutil
import sys
from pathlib import Path


def parse_manifest(path):
    rules = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        fields = line.split('|')
        if len(fields) != 5:
            raise SystemExit(f'bad manifest line: {line}')
        fname, regex, where, marker, text = (f.strip() for f in fields)
        if where not in ('before', 'after'):
            raise SystemExit(f'where must be before|after: {line}')
        rules.append((fname, regex, where, marker,
                      text.encode().decode('unicode_escape')))
    return rules


def insert(src, fname, regex, where, marker, text):
    target = src / fname
    if not target.is_file():
        raise SystemExit(f'{fname} not found in {src}')
    lines = target.read_text().splitlines(keepends=True)
    pat = re.compile(regex)
    if any(marker in l for l in lines):
        print(f'  already present: {fname} <- {marker}')
        return
    hits = [i for i, l in enumerate(lines) if pat.search(l)]
    if not hits:
        raise SystemExit(f'anchor {regex!r} matched nothing in {fname}')
    # "after" anchors on the first match, "before" on the last (so a
    # "before ^endmenu" rule lands on the end of the file, not a nested menu)
    idx = hits[-1] if where == 'before' else hits[0]
    if where == 'after':
        idx += 1
    lines.insert(idx, text if text.endswith('\n') else text + '\n')
    target.write_text(''.join(lines))
    print(f'  {fname}: added at line {idx + 1}')


def copy_files(src, root):
    filesdir = root / 'files'
    if not filesdir.is_dir():
        return
    for f in sorted(filesdir.rglob('*')):
        if not f.is_file():
            continue
        rel = f.relative_to(filesdir)
        dest = src / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dest)
        print(f'  installed {rel}')


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src = Path(sys.argv[1]).resolve()
    root = Path(sys.argv[2]).resolve()
    if not (src / 'Kbuild').exists() and not (src / 'Makefile').exists():
        raise SystemExit(f'{src} does not look like a kernel tree')
    print(f'installing {root}/files into {src}')
    copy_files(src, root)
    manifest = root / 'manifest'
    if manifest.exists():
        print('patching build files')
        for rule in parse_manifest(manifest):
            insert(src, *rule)


main()

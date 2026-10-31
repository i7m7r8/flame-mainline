#!/usr/bin/env python3
"""Survey a decompiled vendor DTB: which nodes exist, their compatible, status
and address. Used to build flame's mainline board file from the vendor tree.

  dtc -q -I dtb -O dts merged-000.dtb -o out/dtbs/flame.dts
  python3 tools/dt_survey.py out/dtbs/flame.dts panel touch backlight
"""
import re
import sys

NODE_OPEN = re.compile(r'^([A-Za-z0-9_.-]+)(?:@([0-9a-fA-F,]+))? \{$')


def walk(text):
    """path -> property lines, by tracking brace nesting."""
    stack, bodies = [], {}
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = NODE_OPEN.match(s)
        if m and s.endswith('{'):
            label = f"{m.group(1)}@{m.group(2)}" if m.group(2) else m.group(1)
            stack.append(label)
            bodies.setdefault('/'.join(stack), [])
            continue
        if s in ('};', '};/* end of node */') or s.startswith('};'):
            if stack:
                stack.pop()
            continue
        if '=' in s and stack:
            bodies['/'.join(stack)].append(s)
    return bodies


def prop(body_lines, key):
    for line in body_lines:
        m = re.match(rf'{key} = \((.*)\);', line)
        if m:
            return m.group(1).replace('"', '').replace('\n', ' ').strip()
        m = re.match(rf'{key} = (".*");', line)
        if m:
            return m.group(1).strip('"')
    return ''


def main(dts, keys):
    bodies = walk(open(dts).read())
    print(f"{len(bodies)} nodes in {dts}")
    shown = 0
    for path, body in bodies.items():
        low = path.lower()
        if keys and not any(k.lower() in low for k in keys):
            continue
        compat = prop(body, 'compatible')
        status = prop(body, 'status') or 'okay'
        reg = prop(body, 'reg').replace(' ', '')
        print(f"  {path.split('/')[-1]:36} [{status:11}] {compat[:56]:56} reg={reg[:24]}")
        shown += 1
    print(f"({shown} matching nodes)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])

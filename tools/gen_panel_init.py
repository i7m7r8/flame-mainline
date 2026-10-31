#!/usr/bin/env python3
"""Generate a mainline panel init table from flame's vendor device tree.

The vendor DT stores the panel init sequence as raw dsi_panel command packets:
7 header bytes (type, display, start, count, wait_ms, keep, len) followed by
`len` payload bytes whose first byte is the DCS command. Mainline wants the
same thing as a table of DCS writes plus delays, so convert it.

  python3 tools/gen_panel_init.py out/dtbs/flame.dts c3f \
      > drivers/gpu/drm/panel/panel-xiaomi-c3f-init.h
"""
import re
import sys

HEADER = 7
MAX_PAYLOAD = 64


def find_nodes(dts, needle):
    """Every node whose opening line contains needle -> list of (line, body)."""
    lines = dts.splitlines()
    found = []
    for i, line in enumerate(lines):
        if needle not in line or not re.search(r'\{\s*$', line):
            continue
        depth = 0
        body = []
        for j in range(i, len(lines)):
            depth += lines[j].count('{') - lines[j].count('}')
            if j > i and depth <= 0:
                found.append((i, '\n'.join(body)))
                break
            body.append(lines[j])
    if not found:
        raise SystemExit(f'no node matching {needle!r}')
    return found


def has_on_command(body):
    return 'qcom,mdss-dsi-on-command' in body


def prop_bytes(body, name):
    m = re.search(re.escape(name) + r'\s*=\s*\[([0-9a-fA-F ]*)\];', body)
    if not m:
        raise SystemExit(f'property {name} not found')
    return bytes.fromhex(m.group(1).replace(' ', ''))


def decode(blob, what):
    """Vendor packet stream -> ('ms', wait) / ('dcs', cmd, params) entries."""
    out, i = [], 0
    while i + HEADER <= len(blob):
        _type, _disp, _start, _count, wait, _keep, length = blob[i:i + HEADER]
        payload = blob[i + HEADER:i + HEADER + length]
        if len(payload) != length or length < 1:
            raise SystemExit(f'{what}: truncated command at offset {i}')
        if wait:
            out.append(('ms', wait))
        if len(payload) > MAX_PAYLOAD:
            raise SystemExit(f'{what}: payload of {len(payload)} bytes at {i}')
        out.append(('dcs', payload[0], list(payload[1:])))
        i += HEADER + length
    if i != len(blob):
        print(f'# {what}: {len(blob) - i} trailing bytes ignored', file=sys.stderr)
    return out


def emit_table(name, entries):
    print(f'static const struct c3f_cmd {name}[] = {{')
    for e in entries:
        if e[0] == 'ms':
            print(f'\t{{ .type = C3F_DELAY, .delay = {e[1]} }},')
        else:
            data = ', '.join(f'0x{v:02x}' for v in [e[1]] + e[2])
            print(f'\t{{ .type = C3F_DCS, .len = {len(e[2]) + 1}, .data = {{ {data} }} }},')
    print('};')
    print(f'#define {name.upper()}_COUNT ARRAY_SIZE({name})')
    print()


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    dts = open(sys.argv[1]).read()
    needle = sys.argv[2] if len(sys.argv) > 2 else 'c3f'
    # flame's DT carries one node per LCM lot; take a variant that has the full
    # command set rather than the trimmed ones
    cands = [(i, b) for i, b in find_nodes(dts, needle) if has_on_command(b)]
    if not cands:
        raise SystemExit(f'no {needle} node with qcom,mdss-dsi-on-command')
    full = [c for c in cands if 'qcom,mdss-dsi-screen-off-command' in c[1]]
    start, body = (full or cands)[0]
    pname = re.search(r'qcom,mdss-dsi-panel-name\s*=\s*"([^"]+)"', body)
    width = re.search(r'qcom,mdss-dsi-panel-width\s*=\s*<0x([0-9a-f]+)>', body)
    height = re.search(r'qcom,mdss-dsi-panel-height\s*=\s*<0x([0-9a-f]+)>', body)
    print(f'/* panel: {pname.group(1) if pname else needle} '
          f'{int(width.group(1), 16)}x{int(height.group(1), 16)} '
          f'- from flame.dts line {start + 1}, {len(cands)} variants seen */',
          file=sys.stderr)

    on = decode(prop_bytes(body, 'qcom,mdss-dsi-on-command'), 'on')
    off = decode(prop_bytes(body, 'qcom,mdss-dsi-screen-off-command'), 'off') \
        if 'qcom,mdss-dsi-screen-off-command' in body else []

    print('/* SPDX-License-Identifier: GPL-2.0-only */')
    print('#ifndef PANEL_XIAOMI_C3F_INIT_H')
    print('#define PANEL_XIAOMI_C3F_INIT_H')
    print()
    print('enum c3f_cmd_type { C3F_DELAY, C3F_DCS };')
    print()
    print('struct c3f_cmd {')
    print('\tenum c3f_cmd_type type;')
    print('\tu8 len;\t\t/* payload bytes including the DCS command byte */')
    print('\tu16 delay;\t/* ms, when type == C3F_DELAY */')
    print('\tu8 data[64];')
    print('};')
    print()
    emit_table('c3f_init_cmds', on)
    emit_table('c3f_off_cmds', off)
    print('#endif /* PANEL_XIAOMI_C3F_INIT_H */')


main()

#!/bin/bash
# Run the handover shim against a stand-in Image + DTB under qemu.
# Proves, before anything touches a phone: patched first instruction -> shim ->
# header parse -> vendor tree parked in the handover region -> memmove of image
# and dtb -> cache ops -> jump with x0 = ours.
set -euo pipefail
cd "$(dirname "$0")"
root=../..
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT

if command -v aarch64-linux-gnu-as >/dev/null; then
  AS=aarch64-linux-gnu-as; OC=aarch64-linux-gnu-objcopy; OD=aarch64-linux-gnu-objdump; PFX=aarch64-linux-gnu-
elif [ "$(uname -m)" = aarch64 ] && command -v as >/dev/null; then
  AS=as; OC=objcopy; OD=objdump; PFX=
else
  echo "no aarch64 assembler (need binutils-aarch64-linux-gnu)" >&2; exit 1
fi
command -v qemu-system-aarch64 >/dev/null || { echo "need qemu-system-arm" >&2; exit 1; }
command -v dtc >/dev/null || { echo "need device-tree-compiler" >&2; exit 1; }

$AS -o "$build/fake.o" fakeimage.S
$OC -O binary --only-section=.text "$build/fake.o" "$build/fake.bin"
dtc -q -I dts -O dtb -o "$build/test.dtb" test.dts
dtc -q -I dts -O dtb -o "$build/vendor.dtb" vendor.dts
python3 $root/tools/mkshim.py -o "$build/shim.bin" --cc-prefix "$PFX"

# A `ret` sitting at the address that one of its own routine's `bl` returns onto
# jumps to itself forever: x30 then holds the ret's own address, and the shim
# spins before the kernel is entered with no output and no fault. That bug cost
# two rounds of qemu debugging, so read it out of the disassembly instead.
$OD -D -b binary -m aarch64 "$build/shim.bin" | python3 -c '
import re, sys
ins = {}
for l in sys.stdin:
    p = l.split("\t")
    if len(p) < 3 or not re.match(r"^\s*[0-9a-f]+:$", p[0]):
        continue
    ins[int(p[0].strip()[:-1], 16)] = p[2].strip()
bad = [(a, ins.get(a + 4, "<past end>")) for a, t in ins.items()
       if t.split()[0] == "bl" and ins.get(a + 4, "").split()[0] == "ret"]
for a, t in bad:
    print(f"shim+{a - min(ins):#x}: bl returns onto a ret, which would loop",
          file=sys.stderr)
sys.exit(1 if bad else 0)
'
echo "ok: no bl returns onto a ret"

python3 $root/tools/mklinuxpayload.py \
  --image "$build/fake.bin" --dtb "$build/test.dtb" --shim "$build/shim.bin" \
  --image-load 0x42000000 --dtb-load 0x41e00000 --scratch-load 0x43000000 \
  --cc-prefix "$PFX" -o "$build/payload.bin"

# qemu -kernel with a raw binary places it at the RAM base (0x40000000 on virt,
# 0x40080000 after the low 512 KiB) and jumps to it with the MMU off, which is
# what the phone's loader does. -dtb is what arrives in x0: it stands in for the
# tree ABL builds from the vendor dtb, so the handover copy is checked against a
# tree that is not the one we hand to the kernel.
timeout 90 qemu-system-aarch64 -M virt -cpu max -m 512 -nographic -display none \
  -nic none -no-reboot -no-shutdown \
  -kernel "$build/payload.bin" -dtb "$build/vendor.dtb" \
  >"$build/console.log" 2>&1 || true
sed 's/^/qemu: /' "$build/console.log"
grep -q 'SHIM-ENTRY FDT-OK COPY-OK HAND-OK' "$build/console.log" || {
  echo "FAIL: shim did not hand over correctly" >&2; exit 1; }
echo "PASS: shim copied the image, passed our dtb in x0 and parked the vendor dtb"

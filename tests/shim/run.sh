#!/bin/bash
# Run the handover shim against a stand-in Image + DTB under qemu.
# Proves, before anything touches a phone: patched first instruction -> shim ->
# header parse -> memmove of image and dtb -> cache ops -> jump with x0 = ours.
set -euo pipefail
cd "$(dirname "$0")"
root=../..
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT

if command -v aarch64-linux-gnu-as >/dev/null; then
  AS=aarch64-linux-gnu-as; OC=aarch64-linux-gnu-objcopy; PFX=aarch64-linux-gnu-
elif [ "$(uname -m)" = aarch64 ] && command -v as >/dev/null; then
  AS=as; OC=objcopy; PFX=
else
  echo "no aarch64 assembler (need binutils-aarch64-linux-gnu)" >&2; exit 1
fi
command -v qemu-system-aarch64 >/dev/null || { echo "need qemu-system-arm" >&2; exit 1; }
command -v dtc >/dev/null || { echo "need device-tree-compiler" >&2; exit 1; }

$AS -o "$build/fake.o" fakeimage.S
$OC -O binary --only-section=.text "$build/fake.o" "$build/fake.bin"
dtc -q -I dts -O dtb -o "$build/test.dtb" test.dts
python3 $root/tools/mklinuxpayload.py \
  --image "$build/fake.bin" --dtb "$build/test.dtb" \
  --image-load 0x42000000 --dtb-load 0x41e00000 \
  --cc-prefix "$PFX" -o "$build/payload.bin"

# qemu -kernel with a raw binary places it at the RAM base (0x40000000 on virt)
# and jumps to it with the MMU off, which is what the phone's loader does.
timeout 90 qemu-system-aarch64 -M virt -cpu max -m 512 -nographic -display none \
  -nic none -no-reboot -no-shutdown \
  -kernel "$build/payload.bin" >"$build/console.log" 2>&1 || true
sed 's/^/qemu: /' "$build/console.log"
grep -q 'SHIM-ENTRY FDT-OK COPY-OK' "$build/console.log" || {
  echo "FAIL: shim did not hand over correctly" >&2; exit 1; }
echo "PASS: shim copied the image and passed our dtb in x0"

#!/bin/sh
# QEMU harness for the flame port: run the ROM's own first stage against our CI
# kernel and a fake UFS disk, and print what it does.
#
#   FLAME_ARTIFACT=/tmp/artqemu/unz ./tools/qemu/run.sh probe
#   FLAME_ARTIFACT=/tmp/artqemu/unz ./tools/qemu/run.sh matrix
#
# probe  boots /tmp/qemuvm's static probe as PID 1 and dumps the sysfs shape
#        init sees (device names, PARTNAMEs, the string boot_devices has to
#        equal).
# matrix boots the shipped /init four times, changing only the device-tree
#        spelling of the bus node and androidboot.boot_devices, and prints the
#        verdict of each. That is the experiment behind
#        patches/local/0013-arm64-dts-qcom-sm4450-name-the-bus-node-soc.patch.
#
# Nothing here is a build of the shipped image: the kernel, dtb and ramdisk all
# come from the CI artifact; only the harness device tree and the fake disk are
# made locally.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
OUT=${FLAME_ARTIFACT:-$REPO/out}
WORK=${FLAME_QEMU_OUT:-/tmp/flame-qemu}
IMAGE=$OUT/Image
GPT=$WORK/disk-gpt.img
INITRD=$WORK/initrd-test.cpio
QEMU_TIMEOUT=${QEMU_TIMEOUT:-60}

[ -f "$IMAGE" ] || { echo "no kernel at $IMAGE (unpack the CI artifact, or set FLAME_ARTIFACT)"; exit 1; }
mkdir -p "$WORK"
[ -f "$GPT" ] || python3 "$HERE/mkdisk.py"
python3 "$HERE/gptcheck.py" "$GPT" | sed -n '1,3p'
[ -f "$WORK/initrd.cpio" ] || FLAME_ARTIFACT=$OUT python3 "$HERE/extract.py"
python3 "$HERE/mkinitrd.py" >/dev/null
aarch64-linux-gnu-gcc -static -O1 -o "$WORK/probe" "$HERE/probe.c"
python3 "$HERE/mkoverlay.py" >/dev/null

# QEMU 10's -M virt does not put realized virtio-*-device at 0xa000000: it
# takes the highest free slot and counts down, so two disks land at 0xa003e00
# and 0xa003c00 with SPIs 47 and 46 (read them out of QEMU's own tree with
# -M virt,dumpdtb=). Get the reg right and the irq wrong and the guest hangs
# mid-probe waiting for an interrupt that never arrives; get the reg wrong and
# virtio_mmio returns -ENODEV with no message at all, because an empty slot
# reads device id 0.
for row in "soc:soc {" "socat0:soc@0 {"; do
	tag=${row%%:*}; spelling=${row#*:}
	sed "s|SOCNODE {|$spelling|" "$HERE/harness.dts.in" > "$WORK/h-$tag.dts"
	dtc -q -I dts -O dtb -o "$WORK/harness-$tag.dtb" "$WORK/h-$tag.dts" 2>/dev/null
done

boot() { # boot <dtb-tag> <boot_devices|"" rdinit> <logfile>
	qemu-system-aarch64 -M virt,gic-version=2 -cpu max -smp 2 -m 320 \
		-display none -no-reboot -nic none -serial file:"$3" \
		-kernel "$IMAGE" -dtb "$WORK/harness-$1.dtb" -initrd "$INITRD" \
		-drive file="$GPT",if=none,format=raw,id=gpt \
		-device virtio-blk-device,drive=gpt \
		-append "console=ttyAMA0 loglevel=7 $2" >/dev/null 2>&1 || true
}

case "${1:-matrix}" in
probe)
	boot soc "rdinit=/diaginit" "$WORK/probe.log"
	sed -n '/P2 \/sys/,/PROBE DONE/p' "$WORK/probe.log" | grep -vE '^\[' | head -40
	;;
matrix)
	printf '%-46s %s\n' "soc node + androidboot.boot_devices" "verdict"
	for row in "soc:soc/a003e00.virtio_mmio" "soc:a003e00.virtio_mmio" \
		   "socat0:soc/a003e00.virtio_mmio" "socat0:soc@0/a003e00.virtio_mmio"; do
		tag=${row%%:*}; dev=${row#*:}
		log=$WORK/init-$tag-$(printf '%s' "$dev" | tr '/@' '_-').log
		boot "$tag" "androidboot.hardware=qcom androidboot.fstab_suffix=default androidboot.slot_suffix=_a androidboot.boot_devices=$dev" "$log"
		if grep -q "Failed to mount required partitions" "$log"; then v="FAILS - no by-name symlinks"
		elif grep -q "partition(s) not found" "$log"; then v="FAILS - partition(s) not found"
		elif grep -q "check_fs(): mount(/dev/block/by-name" "$log"; then v="MOUNTS (first stage done)"
		else v="see $log"; fi
		printf '%-46s %s\n' "$tag + $dev" "$v"
	done
	;;
*)	echo "usage: $0 [probe|matrix]"; exit 2;;
esac

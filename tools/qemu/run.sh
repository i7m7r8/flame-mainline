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

boot() { # boot <dtb-tag> <append> <logfile>
	# A panic with -no-reboot halts the guest but does not make QEMU exit, so every boot
	# needs its own timeout or the harness waits forever on a dead kernel.
	timeout ${QEMU_TIMEOUT:-60} qemu-system-aarch64 -M virt,gic-version=2 -cpu max -smp 2 -m 320 \
		-display none -no-reboot -nic none -serial file:"$3" \
		-kernel "$IMAGE" -dtb "$WORK/harness-$1.dtb" -initrd "${BOOT_INITRD:-$INITRD}" \
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
		elif grep -q "partition(s) not found" "$log"; then v="FAILS - boot_devices names a parent with no slices"
		elif grep -q "check_fs(): mount(/dev/block/by-name" "$log"; then v="MOUNTS (first stage done)"
		else v="see $log"; fi
		printf '%-46s %s\n' "$tag + $dev" "$v"
	done
	;;
bootconfig)
	# Prove the kernel parses the blob ABL appends, by putting every
	# androidboot.* key the first stage needs into the blob and *nothing*
	# onto -append. If CONFIG_BOOT_CONFIG(/_FORCE) is missing the kernel
	# silently ignores it and first stage loses slot_suffix; the mount
	# verdict below is what distinguishes the two.
	python3 "$HERE/mkbootconfig.py" -o "$WORK/bc.bin" \
		androidboot.hardware=qcom androidboot.slot_suffix=_a \
		androidboot.fstab_suffix=default \
		androidboot.boot_devices=soc/a003e00.virtio_mmio
	cat "$INITRD" "$WORK/bc.bin" > "$WORK/initrd-bc.cpio"

	printf '%-46s %s\n' "bootconfig transport" "verdict"
	BOOT_INITRD=$WORK/initrd-bc.cpio boot soc "rdinit=/diaginit" "$WORK/bc-probe.log"
	if grep -q "bootconfig checksum failed" "$WORK/bc-probe.log"; then v="FAILS - checksum rejected"
	elif grep -q "No bootconfig data provided" "$WORK/bc-probe.log"; then v="FAILS - kernel never saw the blob"
	elif grep -q "androidboot.slot_suffix = \"_a\"" "$WORK/bc-probe.log"; then
		v="PARSED (slot_suffix in /proc/bootconfig)"
	elif grep -q "Load bootconfig:" "$WORK/bc-probe.log"; then
		v="PARSED (kernel log only; /proc/bootconfig unreadable)"
	else v="see $WORK/bc-probe.log"; fi
	printf '%-46s %s\n' "initrd + #BOOTCONFIG blob -> cmdline" "$v"
	sed -n '/P1b /,/P2 /p' "$WORK/bc-probe.log" | grep -vE '^\[' | head -6

	BOOT_INITRD=$WORK/initrd-bc.cpio boot soc "" "$WORK/bc-init.log"
	if grep -q "check_fs(): mount(/dev/block/by-name" "$WORK/bc-init.log"; then v2="MOUNTS (first stage done)"
	elif grep -q "Failed to mount required partitions" "$WORK/bc-init.log"; then v2="FAILS - no by-name symlinks"
	else v2="see $WORK/bc-init.log"; fi
	printf '%-46s %s\n' "ROM /init, props only from bootconfig" "$v2"
	;;
*)	echo "usage: $0 [probe|matrix|bootconfig]"; exit 2;;
esac

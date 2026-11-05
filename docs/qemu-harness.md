# The QEMU harness: booting the ROM's own first stage without a phone

`tools/qemu/` runs the CI kernel and the ROM's real `/init` under QEMU aarch64
against a fake UFS disk, so the first-stage mount handshake can be tested
without touching the phone. Nothing in the repo's boot path is rebuilt here:
the kernel, the DTB and the ramdisk all come from the CI artifact
(`out/flame-mainline-final.img` and `Image`), and only the harness device tree
and the fake disk are made locally.

```sh
FLAME_ARTIFACT=/tmp/artqemu/unz tools/qemu/run.sh probe    # what init sees
FLAME_ARTIFACT=/tmp/artqemu/unz tools/qemu/run.sh matrix   # the four-way test
```

`probe` boots `tools/qemu/probe.c` as PID 1 and prints the live sysfs shape:
the platform device names, each partition's realpath, the string
`androidboot.boot_devices` has to equal for `DeviceHandler` to symlink it, and
the `PARTNAME` of every slice. `matrix` boots the shipped `/init` four times
with only two things changing. That is the experiment behind
`patches/local/0013-arm64-dts-qcom-sm4450-name-the-bus-node-soc.patch`.

## Result: the bus node name decides whether first stage mounts

Same kernel, same `/init`, same fstab, same disk. `a003e00.virtio_mmio` is the
harness's stand-in for `1d84000.ufshc`:

| DT bus node | `androidboot.boot_devices` | first stage |
|---|---|---|
| `soc` | `soc/a003e00.virtio_mmio` | mounts `/metadata`, then `/system` |
| `soc` | `a003e00.virtio_mmio` | no by-name symlink, `Can't lookup blockdev`, halt |
| `soc@0` | `soc/a003e00.virtio_mmio` | same |
| `soc@0` | `soc@0/a003e00.virtio_mmio` | mounts |

The two failing rows end with the same four lines, so the halting message is
worth quoting exactly (`init-soc-a003e00.virtio_mmio.log`, 49 s in):

```
init: [libfs_mgr] Failed to open '/dev/block/by-name/metadata': No such file or directory
init: /dev/block/by-name/metadata: Can't lookup blockdev
init: Failed to mount /system: No such file or directory
init: Failed to mount required partitions early ...
```

Note what is *not* there: no `partition(s) not found ... polling timeout`.
`BlockDevInitializer` only says that when the named parent has no slices; here
the slices exist (`/dev/vda1..15` came up fine, with the right `PARTNAME`s), the
parent simply does not match, so init builds no `/dev/block/by-name` directory
and libfs_mgr's later lookup is what fails. A phone with the same mismatch
dies in exactly this shape, one line at a time.

The table above is what `tools/qemu/run.sh matrix` prints; re-run
2026-11-05 14:41-14:45 against artifact run 41952876808 (kernel `Image`,
shipped patched `/init`) and reproduced all four verdicts byte for byte.

The third row is what the phone runs today: mainline's
`arch/arm64/boot/dts/qcom/sm4450.dtsi:396` says `soc: soc@0`, the node has
`ranges` and `#address-cells` but no `reg`, and `of_device_make_bus_id()`
falls back to `kbasename(full_name)`, which keeps the unit address - so the
live path is `/sys/devices/platform/soc@0/1d84000.ufshc` while the bootargs
said `1d84000.ufshc`. The fourth row shows the handshake alone could be
satisfied by bending the bootargs; the next section is why we do not.

Everything the ROM hardcodes spells the bus without the unit address:

- `vendor_sepolicy.cil`: 258 `genfscon sysfs "/devices/platform/soc/...`
  entries, and **none** for `soc@0`. sysfs is labelled by path, so with
  `soc@0` every one of those rules misses and the nodes fall back to the
  generic `sysfs` type.
- `init.target.rc:38` waits on `/dev/block/platform/soc/${ro.boot.bootdevice}`
  with `ro.boot.bootdevice` the *bare* `1d84000.ufshc` - which is why
  `androidboot.bootdevice` in our bootargs stays unprefixed while
  `androidboot.boot_devices` gains the `soc/`.
- `fstab.default:51`, the `/data` line: `sysfs_path=/sys/devices/platform/soc/1d84000.ufshc`.
- `vendor.qti.smp2p.rc:8`: `/sys/devices/platform/soc/soc:qcom,smp2p-adsp/power/wakeup`.
  The `soc:` prefix is what a reg-less child of a node named `soc` gets; had
  the parent been `soc@0` that string could not exist.
- `vendor_ueventd.rc:441`: `/dev/block/platform/soc/1d84000.ufshc/by-name/frp`.

What the stock side does *not* tell us, and how the phone settled it: the base
Ravelin trees (`/tmp/flame-dtbs/1[1-4]_dtbdump_*.dts`) name the node plain `soc`
with `#address-cells = <1>`, while a flame board DTB decompiled in an earlier
session (`/tmp/bootorder/flame.dtb.dts`, vendor bootargs, `soc@0` at line 599
with `#address-cells = <2>`) uses the upstream-like spelling, so the DTB record
is mixed and the vendor kernel source is not on disk. That was UNPROVEN until
it was measured on the live phone, which is where this session runs: `uname`
reports `6.6.82-android15-8-gd33b207314e6-ab13612522-4k`, byte for byte the
version string inside `stock/kernel.bin`, so the running system is stock
Android with the stock kernel and the ROM's own DTB. sysfs is not mount
namespaced, and the sandbox this shell sits in (uid 10412, `CapEff: 0`) can
resolve a path even when it cannot read the inode, which makes `EACCES` mean
"exists" and `ENOENT` mean "does not":

```
/sys/devices/platform                      EXISTS
/sys/devices/platform/nonexistent-xyz      No such file or directory   <- control
/sys/devices/platform/soc                  EXISTS
/sys/devices/platform/soc@0                No such file or directory
/sys/devices/platform/soc/1d84000.ufshc    EXISTS
/sys/devices/platform/soc@0/1d84000.ufshc  No such file or directory
/sys/devices/platform/soc/soc:qcom,smp2p-adsp  EXISTS
/sys/devices/platform/soc/c42d000.qcom,spmi    EXISTS
```

So the vendor kernel really does put the UFS host at `soc/1d84000.ufshc`, and
the two naming rules `of_device_make_bus_id()` implements both show up in the
last two lines: a reg-bearing child becomes `<addr>.<name>`, a reg-less one
keeps `soc:<name>`. `dts/sm4450.dtsi`'s `soc@0` in the flame board DTB therefore
cannot be the whole story - either that DTB is not the one ABL boots, or the
vendor kernel names a `soc@0` node `soc`. Either way the port's target is no
longer inferred from policy strings; it is the path measured on the device, and
patch 0013 is what makes our kernel produce it.

Neither stock's `/firmware/android` node (present in the base trees, only
`compatible` plus a `vbmeta parts=` child, no `boot_devices`) nor any stock
artifact supplies `androidboot.boot_devices`: boot.img's header cmdline field
is all zero, vendor_boot's cmdline is four non-`androidboot` tokens, and the
blob at `vendor_boot.img` + 0x1314000 is a **bootconfig** region carrying
exactly six tags (`hardware=qcom`, `memcg=1`, `serialconsole=0`,
`usbcontroller=a600000.dwc3`, `load_modules_parallel=true`,
`vendor.qspa=true`). So ABL injects it at run time and its value cannot be
read off anything on disk - our own copy in the DT is the only place we
control it, which is why it is spelled out in
`dts/sm4450-xiaomi-flame.dts` rather than assumed.

## The four traps this harness already fell into, so the next reader does not

1. **virtio-mmio slot addresses.** QEMU 10 `-M virt` realizes
   `-device virtio-blk-device` at the *highest* free slot and counts down: two
   disks land at `0xa003e00` (SPI 47) and `0xa003c00` (SPI 46), not at
   `0xa000000`. Read them out of QEMU's own tree with
   `qemu-system-aarch64 -M virt,gic-version=2,dumpdtb=x.dtb -nic none ...` and
   `dtc -I dtb -O dts x.dtb`. Wrong `reg` gives a silent `-ENODEV` (an empty
   slot reads device id 0); right `reg` with a wrong SPI hangs the guest
   mid-probe waiting for an interrupt that never comes.
2. **A hand-built GPT has to satisfy the kernel's checks exactly.**
   `block/partitions/efi.c` CRCs only the first `header_size` (92) bytes, not
   the padded sector; the backup header must name *itself* in `my_lba`; an
   entry is exactly 128 bytes (16 type + 16 uuid + 8+8+8 + 72 name); and the
   protective MBR's `starting_lba` is at record offset +8, not +12. Every
   rejection is `pr_debug`, so a bad table looks like "no partitions".
   `tools/qemu/gptcheck.py` re-runs those checks offline - it is what found
   three of the four bugs above.
3. **`-nic none`**, or QEMU aborts on the missing `efi-virtio.rom`, and
   **`-serial file:`**, or the console output goes nowhere captureable.
4. **initrd size.** The first attempt packed the 43 MB `vendor_ramdisk.cpio`
   and died with `Initramfs unpacking failed: write error` - ENOSPC from
   unpacking into a 320 MB guest. Only `system/etc/fstab.default` is needed
   from it, and `mkoverlay.py` supplies that one file instead.

The harness fstab is the ROM's mount points and flags reduced to what the fake
disk can serve: no `logical` (there is no LP metadata, so `system`/`vendor`/
`metadata` are ordinary partitions here), no `slotselect` (every harness
`PARTNAME` is unsuffixed), and no `avb=`. On the phone AVB is skipped by
`/init` anyway because there is no `/proc/device-tree/firmware/android` node -
see the `Failed to read vbmeta partitions.` line that both the stock-shaped and
fixed runs print before anything succeeds or fails.

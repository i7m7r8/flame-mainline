# flame-mainline

Mainline Linux (stable, currently 7.1.2) for the Xiaomi Redmi 14C 5G / POCO M7
5G — codename `flame`, Qualcomm SM4450 "ravelin", Adreno 613, WCN3990, 4 GB
RAM — packed as an Android boot image that boots the **existing Infinity X
Android 17 userspace** in place. No GSI, no Android rebuild, no Debian: the
ROM on `super` stays exactly as it is, only its kernel is replaced.

Everything is built in GitHub Actions (`mainline kernel (flame)`). The one step
that cannot happen in CI is the last one, described under "Use".

## The four things that make this work

1. **The bootloader boots the wrong device tree.** ABL jumps into the boot
   image's kernel section with the vendor DT it assembled from `dtbo`, which a
   mainline kernel probes nothing of. So the kernel section is
   `Image` + `sm4450-xiaomi-flame.dtb` + an EL1 shim (`tools/shim.S`): the
   `b primary_entry` in the Image header is rewritten to reach the shim, the
   shim parks ABL's tree in a reserved region, moves our DTB to `0xBFE00000`
   and the Image to `0xC0000000`, and branches to `primary_entry` with `x0`
   pointing at our tree. `arch/arm64/kernel/handover.c` then grafts back the
   three things only ABL knows: the `androidboot.*` tags, the address of the
   ROM's init ramdisk, and the real DRAM map. See `docs/mainline-port.md`.
2. **The ROM's first stage lives in *this* partition.** `/init` is in boot.img's
   *generic ramdisk* section — `vendor_boot`'s vendor ramdisk holds only
   `/lib/modules` and has no init at all. CI may not carry the ROM's bytes, so
   `flame-mainline-boot.img` arrives with `ramdisk_size=0` and does not boot as
   it is. `tools/attach_rom_ramdisk.py` splices your own ROM's ramdisk section
   back in byte for byte and keeps the stock `os_version` and header cmdline.
3. **`CONFIG_MODULES=n` is not a blocker.** There is no `.ko` channel to this
   ROM, so the tree is built with everything built-in. First-stage init's
   "Failed to load kernel modules" fatal only fires when it *did* load at least
   one module (`module_count_` is bumped on a successful `finit_module`, and is
   0 here), so a kernel that loads nothing passes. No userspace patch, no
   `finit_module` lie.
4. **There is a log channel for boots that do not finish.** `pstore_blk` on the
   `oops` partition carries kmsg out of a panic or an init-triggered reboot —
   256 KiB zone, `max_reason=4` so `KMSG_DUMP_SHUTDOWN` still writes,
   `pstore.kmsg_bytes=262144` on the kernel line. `flash-boot.txt` gives the
   one-line read-back.

## Safety

One partition is ever written: `boot_<slot the ROM boots from>`, the slot that
already holds Infinity X. Never touched: `abl`, `xbl`, `xbl_config`, `tz`,
`hyp`, `devcfg`, `pmic`, `qupfw`, `aop`, `vbmeta`, `vbmeta_system`,
`vendor_boot`, `dtbo`, `super`, recovery. `boot-safety.txt` explains why a
failed boot is recoverable without touching a flash tool.

Restore, from the ROM's own boot.img:

```sh
fastboot flash boot_<slot> boot.img
fastboot reboot
```

## Build

Workflow `mainline kernel (flame)`, on `workflow_dispatch` or any push touching
`dts/`, `patches/`, `config/flame.fragment`, `tools/`, `tests/` or the workflow
itself. It downloads the upstream stable tarball, applies the
ROCKNIX/distribution SM4450 series, installs this repo's local drivers and the
flame DTS through `tools/install_local.py` (anchored rules, idempotent, fails
loudly rather than quietly dropping a driver), applies `config/flame.fragment`
over `tinyconfig` with a value-level audit that fails the build on any dropped
symbol, builds `Image` plus `qcom/sm4450-xiaomi-flame.dtb`, self-tests the
shim, and packs `flame-mainline-boot.img` — boot v4, Android 17, patch level
2026-10, empty header cmdline, field for field like the ROM's own image.

Artifact `flame-mainline-<sha>`: `flame-mainline-boot.img`, `payload.bin`,
`Image`, `sm4450-xiaomi-flame.dtb`, `sm4450-xiaomi-flame.cdt.dts`,
`flame.config`, `enabled.txt`, `packing.txt`, `build.log`, `SHA256SUMS`,
`FLASH.txt`, `BOOT-SAFETY.txt`, `RAMDISK.txt`.

There is deliberately no artifact-content validation job and no rootfs job.

## Use

```sh
# 1. take the ROM's own boot partition off the device
adb shell "su 0 cp /dev/block/by-name/boot$(getprop ro.boot.slot_suffix) /sdcard/stock-boot.img"

# 2. splice its generic ramdisk into the CI image
python3 tools/attach_rom_ramdisk.py --boot flame-mainline-boot.img \
          --rom-boot stock-boot.img -o flame-mainline-final.img
```

Then flash `flame-mainline-final.img` to that same slot, and nothing else.
`flash-boot.txt` (also shipped as `FLASH.txt`) is the full procedure, including
what to read back if the phone resets after a few seconds.

## Layout

| path | what |
|------|------|
| `dts/sm4450-xiaomi-flame.dts` | board tree: reserved regions where this device really has them, UFS inline crypto, panel/touch wiring, bootargs |
| `patches/local/` | this repo's drivers and hooks, installed by the anchor rules in `patches/local/manifest` |
| `patches/rocknix-sm4450.txt` | the upstream SM4450 series CI fetches |
| `config/flame.fragment` | the whole config delta, asserted symbol by symbol |
| `tools/` | shim, payload and boot-image packers, handover self-test, ramdisk attach tool |
| `docs/` | `mainline-port.md` (the port), `hardware-flame.md` (UART, partitions) |
| `stock/` | ROM extracts, kept locally, **never committed** |

## Status

- Boot path (shim → handover → first stage → Android userspace): built and
  reasoned through to the byte level; needs the on-device run, and the pstore
  channel is in place to report back whatever it does.
- Storage: UFS with inline crypto described, `dm-default-key` backported, f2fs
  options matched against what this ROM's `fs_mgr` passes.
- Display: the c3f panel driver and the ft8726 touch id are local, `msm_drm`
  and the SM4450 clock/interconnect/regulator set are enabled; first real
  pixels are the next milestone.
- GPU, camera, audio, Wi-Fi/BT, modem: enabled wherever mainline has an SM4450
  driver; the vendor-only piles (camera, some thermal and sensor clients) are
  open items.
- SELinux is on, with the rest of what the ROM needs taken from the stock
  config.

An earlier Debian-on-microSD route that used the stock vendor kernel was
dropped; `initramfs*/`, `scripts/build-debian-rootfs.sh` and
`tools/{mkinitramfs,repack_bootimg,mklz4legacy}.py` are leftovers of it and are
in no image this repo produces now.

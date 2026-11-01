# flame-mainline

Mainline Linux (stable, currently 7.1.2) for the Xiaomi Redmi 14C 5G / POCO M7
5G — codename `flame`, Qualcomm SM4450 "ravelin", Adreno 613, WCN3990, 4 GB
RAM — packed as an Android boot image that boots the **existing Infinity X
Android 17 userspace** in place. No GSI, no Android rebuild, no Debian: the
ROM on `super` stays exactly as it is, only its kernel is replaced.

The whole build runs in GitHub Actions (`mainline kernel (flame)`), including
the last step that glues the ROM's first stage onto the kernel it just built —
that part comes from a private repo, see below, so nothing proprietary is ever
pushed here.

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
   `/lib/modules` and has no init at all. Those bytes are Xiaomi's, so they are
   not in this repository: CI fetches them from the private `i7m7r8/flame-stock`
   repo, checks them against `config/stock-ramdisk.sha256`, and attaches them
   with `tools/attach_rom_ramdisk.py`. `flame-mainline-final.img` is the result
   and the thing to flash; `flame-mainline-boot.img` beside it is the kernel
   with no ramdisk, which would reset in about ten seconds.
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
shim, packs `flame-mainline-boot.img` — boot v4, Android 17, patch level
2026-10, empty header cmdline, field for field like the ROM's own image — and
then attaches the ROM's first stage from the private `i7m7r8/flame-stock` repo
using the `STOCK_TOKEN` repository secret, hash-checking it against
`config/stock-ramdisk.sha256` before and after. The attached image is the
flashable one.

Artifact `flame-mainline-<sha>`: `flame-mainline-final.img`,
`flame-mainline-boot.img`, `payload.bin`, `Image`, `sm4450-xiaomi-flame.dtb`,
`sm4450-xiaomi-flame.cdt.dts`, `flame.config`, `enabled.txt`, `packing.txt`,
`build.log`, `SHA256SUMS`, `FLASH.txt`, `BOOT-SAFETY.txt`, `RAMDISK.txt`. The
ROM's ramdisk itself is downloaded to `$RUNNER_TEMP`, never to `out/`, so it is
not in the artifact and not published.

There is deliberately no artifact-content validation job and no rootfs job.

## Use

```sh
gh run download --repo i7m7r8/flame-mainline <run-id> -n flame-mainline-<sha>
fastboot flash boot_<slot the ROM boots from> flame-mainline-final.img
fastboot --set-active=<same slot>
fastboot reboot
```

`flash-boot.txt` (shipped as `FLASH.txt`) is the full procedure, including the
restore path and what to read back if the phone resets after a few seconds.

If your ROM is not the build `flame-stock` holds, or you would rather not take a
runner's word for those bytes, dump your own partition and attach it locally —
the same tool, the same byte-identity rules:

```sh
adb shell "su 0 cp /dev/block/by-name/boot$(getprop ro.boot.slot_suffix) /sdcard/boot-stock.img"
python3 tools/attach_rom_ramdisk.py --boot flame-mainline-boot.img \
          --rom-boot boot-stock.img -o flame-mainline-mine.img
```

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

# Boot audit: every blob that decides whether this image boots

Written 2026-11-08 against commit `8d6f4c6`. The question this answers is
narrow: **what, out of everything on this ROM, can still stop the phone from
booting our kernel, and what has been measured rather than assumed.**

Two tools were used, and the split matters:

* `craft-anything-cli` (0.3.0, `--deep`, no LLM backend) for structural
  decoding and string/interface extraction out of the vendor blobs. It reports
  `evidence id` per answer; those ids are what "observed" below refers to.
* `objdump`/`readelf` for the relocation and symbol layers the clean-room
  lifter cannot resolve in a relocatable `.ko` - every `bl` in a vendor module
  is an `R_AARCH64_CALL26` against an unresolved symbol, so a lifted
  pseudocode shows `0x0` targets where `objdump -dr` names the callee. Claims
  that depend on a callee are marked as coming from the second tool.

Nothing here is a build. The CI artifact is the only image, as always.

## Corpus

| blob | how obtained | size |
| --- | --- | --- |
| `stock/payload.bin` | first-stage OTA payload, already in the repo | 2.27 GB |
| `vendor.img` | `tools/payload_extract.py payload.bin vendor` | 1 053 892 608 B, ext4 |
| `vendor_dlkm.img` | same, `vendor_dlkm` | 83 677 184 B, ext4, 258 `.ko` |
| `odm.img` | same, `odm` | 9 785 344 B |
| vendor ramdisk | `stock/vendor_ramdisk.cpio` → newc extractor | 43 814 400 B, 288 `.ko` |
| `boot.img` | stock, v4 | header decoded below |
| `vendor_boot.img` | stock, v4 | header + bootconfig decoded below |
| rc files | `debugfs rdump /etc/init` from `vendor.img` | 96 files |
| HAL binaries | `debugfs dump /bin/hw/*`, `/lib64/*.so` | 17 + 6 |

## The two images, header for header

Both are `ANDROID!` **v4**, so neither carries a cmdline and ABL takes load
addresses from `vendor_boot`, not from us:

| field | stock `boot.img` | our `flame-mainline-final.img` |
| --- | --- | --- |
| `header_version` | 4 | 4 |
| `header_size` | 1584 | 1584 |
| `os_version` | 17.0.0 | 17.0.0 |
| security patch | 2026-10 | 2026-10 |
| `kernel_size` | 36 456 960 (= `stock/kernel.bin`) | 84 992 000 (= our `payload.bin`) |
| `ramdisk_size` | 2 600 656 (= `stock/ramdisk.img`) | 4 912 902 (ROM ramdisk, patched) |
| cmdline | empty | empty |

`vendor_boot.img` (untouched by this port, and it must stay untouched):

```
v4  page_size=4096  header_size=2128
kernel_addr=0x8000  ramdisk_addr=0x1000000  tags=0x100
vendor_ramdisk_size=16134011  dtb_size=3860479 @0x1f00000
vendor ramdisk table: 1 entry x 108 B   bootconfig_size=181 @0x1314000
vendor_cmdline: ... sysctl.kernel.firmware_config.force_sysfs_fallback=1 ...
```

The six bootconfig keys are the only place three assumptions could have been
wrong, and one of them was:

```
androidboot.hardware=qcom        androidboot.memcg=1
androidboot.serialconsole=0      androidboot.usbcontroller=a600000.dwc3
androidboot.load_modules_parallel=true   androidboot.vendor.qspa=true
```

`a600000.dwc3` is not ours to choose, and our USB node produced
`a600000.usb`; see finding **F-1**.

## The boot path, step by step, with what is verified at each step

| # | what happens | state |
| --- | --- | --- |
| 1 | ABL parses our `boot.img`: v4, no cmdline, no addresses - those come from `vendor_boot` | verified: header fields equal stock's |
| 2 | the EL1 shim hands over our dtb; `/chosen/bootargs` (655 B) plus ABL's `androidboot.*` from `vendor_boot`'s 181-byte bootconfig become the cmdline | verified: both strings read back |
| 3 | kernel brings up UFS (`1d84000.ufshc` under `soc`), ICE, RPMh, GENI console, panel | verified: nodes and config; **panel lighting up is unmeasured** |
| 4 | first stage `/init` (patched so a failed module load is not fatal) mounts `/system`, `/vendor` from `super` via `dm-linear` (in `dm-mod`), then `/metadata` | verified: `dm-linear` is in `dm-mod`, F2FS/EXT4 `=y`; QEMU first stage reaches "MOUNTS" |
| 5 | `ueventd` creates `/dev/block/by-name/*` and serves `firmware_directories /vendor/firmware_mnt/image/` | verified: rc contents; `boot_devices=soc/1d84000.ufshc` matches the node |
| 6 | second stage init runs rc; every vendor-only sysfs write fails and is logged, not fatal | verified: 123 distinct targets, providers mapped |
| 7 | `write /sys/kernel/boot_adsp/boot 1` -> our compat node -> `system_wq` -> `rproc_boot` -> `request_firmware` (UEVENT) -> sysfs fallback (`force_sysfs_fallback=1` from `vendor_boot`) -> ueventd -> `adsp.mdt` | verified end to end, **file exists** |
| 8 | USB: `dwc3@a600000` -> UDC `a600000.dwc3` -> ROM's configfs composition | UDC name verified; **composition risk**: `/vendor/bin/init.qcom.usb.sh` defaults include `diag`, which has no mainline function driver |
| 9 | vold unlocks `/data` (`wrappedkey_v0`) through KeyMint | **BLOCKED** - `/dev/qseecom` (B-1) |
| 10 | SurfaceFlinger allocates buffers through gralloc | **BLOCKED** - `/dev/qti-smmu-proxy` (B-2) |

Steps 1-7 are the whole of "does the kernel boot and does first stage get
through". Steps 8-10 are where the ROM's own userspace meets kernel interfaces
this port does not have.

## Every module, fingerprinted

All 288 `.ko` in the vendor ramdisk (and the 70 that only exist in
`vendor_dlkm`) went through `craft-anything-cli` triage and string extraction,
and their undefined-symbol lists were bucketed by subsystem. Of the 288:

| imports | modules |
| --- | --- |
| clock framework | 53 |
| DMA engine | 42 |
| regmap | 38 |
| regulator | 30 |
| `qcom_scm` | 29 |
| thermal | 21 |
| GENI/interconnect | 20 |
| pinctrl/gpio | 19 |
| USB gadget | 14 |
| smp2p/smem | 14 |
| glink/rpmsg | 12 |
| `request_firmware` | 12 |
| iommu | 7 |
| rpmh | 7 |
| remoteproc | 6 |
| ufs | 5 |
| spmi | 5 |

The point of the bucket counts is not the numbers, it is that **none of these
288 can be loaded** (`CONFIG_MODULES=n`), so any interface that only one of
them provides is absent from this image unless an in-tree driver covers it -
which is exactly the DEGRADED table below.

## Findings

### Fixed in this audit (committed, both built green)

**F-1 (BOOT-BLOCKER, fixed `8d6f4c6`) - the UDC name was not the one the ROM
asks for.** `vendor_boot`'s bootconfig fixes `androidboot.usbcontroller` to
`a600000.dwc3`; `init.target.rc` feeds that string to `configfs` as
`sys.usb.controller`. In 7.1.2 there is no wrapper/core split: `dwc3-qcom`
registers the core itself (`drivers/usb/dwc3/dwc3-qcom.c:708`,
`qcom->dwc.dev = dev` at `:703`), so the UDC is named after the *glue* node.
Ours was `usb@a600000` → `a600000.usb` → nothing would ever bind: no adb, no
gadget, no ACM shell. The node is now `dwc3@a600000`, which is what the vendor
tree called it, and `dtc` confirms the blob carries that name.

**F-2 (DEGRADED, fixed `4930f81`) - `boot_adsp` booted inline, the vendor never
does.** `adsp_boot_store` is 14 instructions and its whole job is
`queue_work_on(WORK_CPU_UNBOUND, system_wq, ...)` at `+0x114`; the work does
`rproc_get_by_phandle` (`+0x628`) then `rproc_boot` (`+0x658`). It also parses
with `sscanf(buf, "%du")` (`.rodata+0x221`), returns `-EINVAL` after
`"failed to read boot info from string"` on a bad scan, and treats `0` as
`rproc_shutdown()` at `+0x834`. Ours called `rproc_boot()` from the store,
which is a 60-second wait (fallback `loading_timeout`, `fallback_table.c:22`)
because `force_sysfs_fallback` is already 1 from `vendor_boot`'s own cmdline -
and `init.target.rc:141` issues that write as a plain foreground command.

### Not fixable from this repository (they are userspace/kernel ABI ports)

**B-1 (BOOT-BLOCKER for `/data`, open) - `/dev/qseecom`.** Measured from the
ROM's own binaries, not inferred:

* `libqtikeymint.so`: `"/dev/qseecom"`, *"Could not open /dev/qseecom, errno =
  %d"*.
* `libQSEEComAPI.so`: `"/dev/qseecom"`, *"Error::Failed to open /dev/qseecom
  device"*.

`libqtikeymint.so` is what
`android.hardware.security.keymint-service-qti` loads, and `fstab.default:51`
asks for `fileencryption=...:v2+inlinecrypt_optimized+wrappedkey_v0` with
`metadata_encryption=aes-256-xts:wrappedkey_v0`, `keydirectory=/metadata/...`.
No QSEE → no KeyMint → no unwrap → `/data` never mounts, and `keystore2` being
critical is what drives the reboot-to-bootloader loop already documented in
`docs/mainline-port.md`.

Cost, measured off the blobs: `qseecom_dlkm.ko` is 334 312 B with 1 566
code ranges in the lifter's function list (83 named functions in the
symbol table, 109 kernel imports); `smcinvoke_dlkm.ko` is 284 656 B with
1 102 ranges (190 named, 103 imports); and `.modinfo depends=` on `qseecom_proxy`,
`qcom-scm`, `mem_buf_dev` for the first. No firmware path literal appears in
either blob (`qseecom_dlkm.ko` has no `/vendor/firmware`, no `.mbn`;
`smcinvoke_dlkm.ko` imports `firmware_request_nowarn` but names no file), so
TAs are handed in by `qseecomd` and the gap is entirely kernel-side.
`CONFIG_MODULES=n` means there is no `.ko` channel to hide in.

**B-2 (BOOT-BLOCKER for the UI, open) - the graphics ABI.** Measured:
`libgralloccore.so` opens **`/dev/qti-smmu-proxy`**, which only
`smmu_proxy_dlkm.ko` provides. Not measured: `libsdedrm.so` (the composer
side) contains no `/dev` or `/sys` literal at all, so how it reaches the
kernel is unknown from strings alone - it goes through libdrm, and mainline
`msm` does not implement the vendor SDE ioctls, but that last step is an
inference from stub behaviour, not a decoded contract. Either way the ROM's
allocator cannot get a single buffer on a mainline kernel, so SurfaceFlinger
cannot start even though `msm` + `fbcon` can drive the panel as a console.
Same class as B-1: a port, not a config byte.

### Firmware images: both chains now end at a real file

The previous audit could not answer whether the ADSP image exists, because the
phone's `/vendor/firmware_mnt` is not visible from this namespace. It is
answerable from the OTA instead: `fstab.default:55` mounts partition `modem` at
`/vendor/firmware_mnt`, and that partition, extracted from
`stock/payload.bin`, is FAT16 with one `image/` directory (339 entries)
containing:

| file | size | what asks for it |
| --- | --- | --- |
| `adsp.mdt` + `adsp.b00`..`adsp.b41` | 7164 B + ~19 MB | patch 0003's `adsp.mdt`, via `boot_adsp` -> `rproc_boot` |
| `adspr.jsn`, `adsps.jsn`, `adspua.jsn` | 513 / 515 / 703 B | ADSP sensor PD configuration |
| `adrastea/wpss.mdt` + `wpss.b00`..`wpss.b10` | 4684 B + ~7 MB | the board file's `adrastea/wpss.mdt` |
| `adrastea/bdwlan*` | 240 files | WCN6750 board data (unused without `icnss2`) |
| `clarence/qdsp6m.qdb` | 6.75 MB | modem DSP calibration (no modem rproc here) |
| `mfido.mdt`, `mkeybag.mdt`, `secauth.mdt`, `soter64.mdt`, `widevine.mbn`, `tuiauthapp.mbn` | 4-266 KB | QSEE trustlets, i.e. the TA half of B-1 |

So the ADSP and WPSS firmware names are correct *and* present, and the only
reason a DSP stays down would be the driver side. Note also that the modem
partition is where the KeyMint/KM trustlets live: B-1 needs a kernel driver, not
firmware that is missing.

## DEGRADED - writes that will simply fail, verified harmless

Every one of these is a `write` in an rc action or by a HAL; AOSP's
`Action::ExecuteCommand` logs the failure and continues with the rest of the
action, so none of them gates the boot. Provider names come from the module
corpus (288 + 70 blobs scanned).

| interface the ROM touches | who provides it on stock | our kernel |
| --- | --- | --- |
| `/sys/kernel/icnss/{wpss_boot,wlan_en_delay}` | `icnss2.ko` | absent - no Wi-Fi |
| `/sys/kernel/cnss/fs_ready` | `cnss_utils.ko` | absent |
| `/sys/kernel/dload/emmc_dload` | `qcom-dload-mode.ko`, `dwc3-msm.ko` | absent |
| `/sys/kernel/shutdown_wlan/shutdown`, `/sys/kernel/qcom_rproc/shutdown_in_progress` | vendor | absent |
| `/sys/devices/virtual/cvp/cvp/boot` | CVP stack | absent - no camera video path |
| `/sys/class/leds/vibrator/trigger` | `leds-qpnp-vibrator-ldo.ko` | absent - no vibration |
| `/sys/module/{eud,pil_msa,subsystem_restart,subsys_pil_tz,peripheral_loader}/parameters/*` | those modules | absent; writes fail |
| `/sys/bus/platform/devices/1c00000.qcom,pcie/debug/boot_option` | vendor PCIe | node is `pcie@1c00000` → `1c00000.pcie`; name differs, write fails |
| `/sys/bus/platform/devices/1d84000.ufshc/{auto_hibern8,clkgate_enable,clkscale_enable}` | vendor `ufs_qcom.ko` | mainline takes `auto_hibern8` as a DT property, no sysfs attr |
| `/sys/devices/platform/soc/soc:qcom,smp2p-{adsp,cdsp,modem}/power/wakeup` | vendor DT naming | ours are `soc:smp2p-adsp` / `soc:smp2p-wpss`; cdsp and modem have no node at all. Wakeup source not enabled; suspend behaviour, not boot |
| `/config/usb_gadget/g1/functions/{diag.diag,qdss.*}/*` | `usb_f_diag`, `usb_f_qdss` | `CONFIG_USB_CONFIGFS_F_DIAG`/`F_QDSS` not set; adb uses `f_fs`, which is `=y` |
| `/dev/cpuctl/*/cpu.uclamp.colocate`, `cpu.uclamp.sched_boost_no_override` | vendor scheduler | mainline has neither file |
| `/proc/sys/kernel/sched_lib_{name,mask_check,mask_force}` | vendor scheduler (`libqti-perfd.so` writes them) | absent - perf HAL degraded |
| `/sys/devices/virtual/graphics/fb0/idle_time` | vendor fbdev | `fb0` exists via DRM fbdev emulation; the attribute is vendor-only |

### Userspace to kernel contract, read off the vendor binaries

Every one of these is a string in a binary that runs before or alongside
`system_server` (extracted from `vendor.img` with `debugfs`, scanned with
`craft-anything-cli native-strings-find`):

| vendor binary | kernel object | our kernel |
| --- | --- | --- |
| `qseecomd` | `/dev/qseecom` | absent - B-1 |
| `libqtikeymint.so` | `/dev/qseecom` | absent - B-1 |
| `libQSEEComAPI.so` | `/dev/qseecom` | absent - B-1 |
| `libgralloccore.so` | `/dev/qti-smmu-proxy` | absent - B-2 |
| `dspservice` | `/dev/fastrpc-cdsp` | absent (no CDSP node) |
| `adsprpcd` | via `libadsprpc.so`: `/dev/fastrpc-adsp` | present (`CONFIG_QCOM_FASTRPC=y`) |
| `libqti-perfd.so` | `/dev/cpuset/*/boost`, `/proc/sys/kernel/sched_lib_*` | partly: cpusets exist, `boost` and `sched_lib_*` are vendor-only |
| `mi_thermald` | `/sys/class/thermal/*`, `/sys/class/thermal/thermal_message/*`, `/sys/class/qcom-battery/*`, `/sys/class/backlight/panel0-backlight/brightness` | `thermal` yes; the `thermal_message`, `qcom-battery` and `panel0-backlight` classes are vendor-only, so charging mitigation degrades |
| `libthermalclient.so`, `lowi-server` | sockets only (`/dev/socket/...`) | n/a - no kernel interface |
| `android.hardware.usb.gadget-service.qti` | `/config/usb_gadget/g1/{UDC,functions/*}`, `/dev/usb-ffs/{adb,mtp,ptp}/ep*`, `/sys/class/udc/` | `ffs` and `UDC` are there (F-1); `mtp`/`ptp` functions are not (`F_MTP`/`F_PTP` unset), and the ROM's own composition script prefers `diag`/`qdss`/`serial_cdev` functions that only vendor modules provide |

That last row is the one place where our debug channel is at risk for a reason
that is not a port: `init.qcom.usb.sh:57-133` sets `persist.vendor.usb.config`
to compositions that include `diag`, and `usb_f_diag` has no mainline
equivalent. The HAL surfaces *"Usb Gadget setcurrent functions failed"*, and
what Android does after that - retry with a smaller composition, or leave USB
unbound - is not decided by anything in this image. **UNKNOWN**, and it is
cheap to measure on device: `getprop persist.vendor.usb.config` and
`cat /sys/class/udc/*/state`.

### Device tree gap check against the vendor's own base DTB

`vendor_boot.dtb_size` is not the offset of a blob in the image (the headers'
`dtb_addr` is a load address), but the FDTs are findable by magic: the first one
at `0xf64000` is `"Qualcomm Technologies, Inc. Parrot 4Gb SoC"`, 334 852 B, and
it decompiles to 15 773 lines. It carries **235** `compatible` strings; the
board file carries **145**, and 203 of the vendor's are absent from it.

That number overstates the gap, because vendor and mainline spell the same
device differently (`qcom,i2c-geni` vs `qcom,geni-i2c`) and because most of the
list is deliberately out of scope. What was checked by hand:

*Present in the board file*: `geni-i2c`, `geni-spi`, `cpufreq`,
`qcom,qfprom-sys`-equivalent nvmem, `smem`, `smmu` (18 references), `rsc`,
`sde`, the DWC3 glue plus a real HS phy (`qcom,usb-snps-hs-7nm-phy`) and the
QMP combo phy, and 15 `power-domains` references into RPMh (`MX`, `CX`, `LCX`,
`LMX`), which is the mainline stand-in for the vendor's `qcom,gdsc`.
*Absent, and vendor-only*: `qcom,qseecom`, `qcom,smcinvoke`, `qcom,mem-buf`,
`qcom,sde-kms`, `qcom,msm-vidc*`, `qcom,adreno-gpu-*`(kgsl), the whole
`qcom,lpass-cdc` / `qcom,msm-pcm-*` audio set, `qcom,gh-*` (Gunyah),
`qcom,tz-log`, `qcom,minidump`, `qcom,mem-dump`, `qcom,memshare`,
`qcom,qcedev`, `qcom,secure-buffer`, `qcom,smmu_sde_*`.
*Absent, mainline-supported, harmless without*: `qcom,bam-v1.7.0` and
`qcom,gpi-dma` (GENI falls back to FIFO), `qcom,epss` (the board file's
cpufreq node is the one that binds), `qcom,smp2p-sleepstate`.

## Cleared - things that looked like blockers and are measurably not

These are recorded because each one was a credible way to fail at first stage,
and the answer is worth not re-deriving.

* **`dm-linear`.** Android maps the logical partitions inside `super` with
  `dm-linear`, and `CONFIG_DM_LINEAR` does not exist as a symbol in 7.1.2's
  `.config`. That is not a gap: `drivers/md/Makefile:6` puts `dm-linear.o` in
  `dm-mod` itself, which `CONFIG_BLK_DEV_DM=y` builds. `DM_VERITY`,
  `DM_SNAPSHOT`, `DM_CRYPT`, `DM_DEFAULT_KEY` are all `=y` besides.
* **Inline crypto / ICE.** The symbol is `CONFIG_QCOM_INLINE_CRYPTO_ENGINE`
  (not `CONFIG_QCOM_ICE`) and it is `=y`, with `BLK_INLINE_ENCRYPTION` and its
  `_FALLBACK` `=y`; the DT wires `qcom,ice = <&ice_ufs_mem>` and the bootargs
  already carry `qcom_ice.use_wrapped_keys=1`, which `ice.c:179-186` requires
  before it will use HWKM at all.
* **The rootfs side.** `F2FS_FS`, `EXT4_FS`, `SELINUX` (+`_DEVELOP`),
  `BINDERFS`, `CGROUPS`/`MEMCG`/`CPUSETS`/`BLK_CGROUP`, `ZRAM`, `PSI`,
  `FHANDLE`, `TMPFS_XATTR`, `DMABUF_HEAPS` are all `=y`.
* **Console and logs.** `SERIAL_QCOM_GENI` + `_CONSOLE` are `=y` and the UART
  ports are 8; `FB`+`DRM_FBDEV_EMULATION`+`FRAMEBUFFER_CONSOLE` are `=y`, so
  `console=tty0 fbcon=map:0` is a working channel once the panel probes. The
  panel driver is ours (`CONFIG_DRM_PANEL_XIAOMI_C3F=y`) and mainline has the
  SM4450 DPU catalogue (`dpu_8_6_sm4450.h`).
* **The cmdline fits.** Our `/chosen/bootargs` is 655 bytes of the 2048 that
  `COMMAND_LINE_SIZE` allows, so ABL's `androidboot.*` tags have 1393 bytes to
  land in.
* **`boot_devices`.** Shipped dtb decompiles to `soc {`, our UFS node is
  `ufshc@1d84000` → `/dev/block/platform/soc/1d84000.ufshc`, and the ROM's own
  fstab spells `/dev/block/bootdevice/by-name/userdata`, which is what
  first-stage's DeviceHandler resolves from that path.
* **Remoteproc for the DSPs.** `QCOM_Q6V5_PAS`, `RPROC_COMMON`, `SYSMON`,
  `PIL_INFO`, `FASTRPC`, `APR`, `PDR_HELPERS`, `SCM` are all `=y`; the firmware
  resolution chain itself is unchanged from the last audit.

## Process finding

`.github/workflows/kernel.yml:487` sets `retention-days: 1`. The artifacts for
`4930f81` and `8d6f4c6` were uploaded (artifact ids 13435481534 and friends,
113 773 488 B each) and are already gone from the artifact API - `total_count:
0` for both runs. Any build older than a day cannot be fetched, so an image has
to be downloaded in the same session it is built. A fresh `workflow_dispatch`
run was started for this audit.

## What is still unmeasured

1. Whether `/data` on this device is actually encrypted with `wrappedkey_v0` in
   a form that needs KeyMint, or whether it was wiped and re-created by a
   software key. That changes B-1 from "never boots" to "boots after a wipe".
2. Whether the TrustZone on this device exposes the wrapped-key SCM calls
   (`qcom_scm_has_wrapped_key_support()`), which decides whether HWKM is even
   reachable if B-1 were ported.
3. Whether `PARTLABEL=oops` (`config/flame.fragment:391`) is a real partition.
   The comment there says it is assumed; the ROM's own rc names only `ramdump`,
   `rawdump`, `spunvm`, `rescue` and `logfs`. If it is wrong, the cost is the
   post-mortem log, not the boot.
4. Whether the panel timings inherited for the c3f panel actually produce a
   picture - the DTS says they were inherited from sky and must be replaced
   with the vendor fragment's timings.

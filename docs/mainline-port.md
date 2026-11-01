# Mainline port plan (flame / SM4450)

Goal: boot upstream Linux on the Redmi 14C 5G with **all** hardware working,
using the vendor DT (`docs/hardware-flame.md`) as the spec and other people's
already-merged-out-of-tree SM4450 work instead of reinventing it.

## What upstream v7.2 already has for SM4450

`arch/arm64/boot/dts/qcom/sm4450.dtsi` is 686 lines. Present:

| subsystem | upstream status |
|-----------|-----------------|
| CPUs, idle states, PSCI, cpu power domains, PMU, L2/L3 | ✅ 6xA55 + 2xA78 |
| GICv3 `intc@17200000`, `timer@17420000`, arch timer | ✅ |
| `gcc@100000` + `drivers/clk/qcom/gcc-sm4450.c` (`SM_GCC_4450`) | ✅ |
| `gpucc@3d90000`, `dispcc@af00000`, `camcc@ade0000` (+ clk drivers and dt-bindings headers) | ✅ |
| `tlmm@f100000` + `drivers/pinctrl/qcom/pinctrl-sm4450.c` | ✅ |
| `pdc@b220000`, `tcsr_mutex@1f40000` | ✅ |
| `apps_rsc` rpmh-rsc, `bcm-voter`, `rpmhcc`, `rpmhpd` (`qcom,sm4450-rpmhpd`, cells from `dt-bindings/power/qcom,rpmhpd.h`) | ✅ |
| `cpufreq_hw@17d91000` (`qcom,sm4450-cpufreq-epss`) | ✅ |
| one GenI SE: `qupv3_id_0: geniqup@ac0000` with **only** `uart7: serial@a88000` (= flame console), both `disabled` | ⚠️ |
| board file | only `sm4450-qrd.dts` (enables qupv3_id_0 + uart7) |

Missing upstream entirely — everything a phone needs:

| needed | upstream | source to use |
|--------|----------|---------------|
| interconnect provider + `dt-bindings/interconnect/qcom,sm4450.h` | ❌ none | ROCKNIX `0006-interconnect-qcom-add-sm4450.patch` (57 KB, includes the header) |
| second GenI SE `@9c0000`, all I2C/SPI children, GPI DMA | ❌ | DT we write; `qcom,gpi` driver already exists (needs sm4450 compatible/QUP numbering check) |
| SPMI arb + PMIC set (pmk8350/pm6450/pm8010/pm7250b/pmg1110) | driver ✅ (`qcom,spmi-pmic-arb`), DT ❌ | DT; `0004-regulator-qcom-rpmh-add-pm6450`, `0022-*pbs-type`, `0060-*switch-sd-ldos-through-pbs` |
| LPG/PWM (backlight alt, vibrator) | DT ❌ | `0023-leds-qcom-lpg-add-pm6450-pwm`, `0060-leds-qcom-lpg-support-inversed-pwm-polarity` |
| power-domain: LCDX GDSC for sm4450, GX/GDSC fixes | partial | `0002-pmdomain-qcom-rpmhpd-add-lcx-for-sm4450`, `0005-soc-qcom-pd-mapper-add-sm4450` |
| GPU SMMU + Adreno 613 | ❌ catalog has a610/a612/a615/a616/a618/a619 only | `0001-drm-msm-adreno-add-a613-catalog-entry`, `0008-iommu-arm-smmu-qcom-add-sm4450`, `0013/0014/0015/0017/0018` gpucc+gmu fixes |
| display: DPU/DSI sm4450 glue, splash handover | ❌ | `0009-drm-msm-add-sm4450-display`, `0011`, `0012`, `0019-drm-msm-dpu-stop-boot-scanout` |
| remoteproc ADSP/WPSS | ❌ | `0003-remoteproc-qcom-pas-add-sm4450-adsp-wpss` |
| USB: QMP/USB3+DP combo PHY, UFS PHY | ❌ for sm4450 | `0040-phy-qcom-qmp-combo-add-sm4450`, `0041`, `0042`, `0043-phy-qcom-qmp-ufs-add-sm4450` |
| `dwc3-qcom` | ✅ now matches generic `qcom,snps-dwc3` | DT only |
| Type-C: UCSI over glink | partial | `0049-usb-typec-ucsi_glink-add-sm4450-quirk` |
| battery/charger via pmic_glink | ✅ driver | `0051`, `0052`, `0054` battmgr fixes |
| touch | no Focaltech I2C driver exists upstream (`drivers/input/touchscreen/` has only `edt-ft5x06` for this family; `focaltech_ft6236_i2c.c` is not in 7.1.y) | `0061-input-edt-ft5x06-follow-the-panel-power-state` + our own `focaltech,ft8726` match entry (`patches/local/manifest`); flame's vendor `ic-type 0x8726081c` is an FT8726, and the vendor blob in the ramdisk is `focaltech_spi.ko`, so if the frame format turns out to be FTS rather than FT5x06 the fallback is porting the FTS protocol parser |
| Wi-Fi/BT | ❌ `icnss`/WCN3990 not upstream; `ath10k/ahb.c` matches only `qcom,ipq4019-wifi` | `0045`/`0046`/`0047`/`0048` are for **WCN3950** (`hci_qca`), which *is* the same WCN39xx family as flame's WCN3990 — useful, but the WLAN side still needs a port |
| audio (LPASS/CDSP, wcd/va/rx/wsa macros, soundwire, aw88166) | partial (`qcom,sc8280xp` machine driver) | `0030`, `0031`–`0034`, `0039`, `0056` |
| thermal | `qcom,tsens-v2` ✅ | DT (2 controllers on flame) |
| `qcom,ravelin-qfprom` nvmem | ❌ compatible | one-line addition in `nvmem/qcom-qfprom.c` |

Conclusion: upstream gives a bootable CPU/GIC/timer/clock/pinctrl base and
nothing else. The ROCKNIX SM4450 series (49 patches, kept in
`packages/…/patches/linux` for the anbernic RG55G1) is exactly the missing
display/GPU/ICC/PHY/PMIC/remoteproc/Type-C layer, and it is maintained against
a current kernel, so the port is mostly *assemble + fix up DT*, not write drivers.

## Can the stock Infinity X Android 17 ROM run on this kernel?

No — and it is an ABI fact, not a missing-driver problem. `stock/vendor_boot.img`
header v4 carries the vendor ramdisk as an uncompressed newc cpio with **288
`.ko` files** (`python3 - <<'PY'` over the cpio header fields, or
`bsdtar -tf stock/vendor_ramdisk.cpio`):

`msm_drm.ko`, `qcom_q6v5_pas.ko`, `qcom_glink*.ko`, `qti_pmic_glink.ko`,
`qcom_ice.ko`, `focaltech_spi.ko`, `nt36xxx_spi.ko`, `goodix_fp.ko`,
`mi_thermal_interface.ko`, `gh_*` (Gunyah), `pinctrl-ravelin.ko`,
`gcc/dispcc/gpucc/camcc/debugcc-sm4450.ko`, …

Two independent blockers:

1. **Module loading.** They are built against `6.6.82-android15-8` with
   modversions CRCs and a GKI `vermagic`. A mainline kernel exports different
   CRCs, so `fopen`+`finit_module` fails with `ENOEXEC`/`EBADFD` for all 288,
   and the first-stage vendor init that expects them errors out.
2. **UAPI.** Even if a mainline tree carried a driver for the same block, the
   HALs use the *vendor* driver interface: `msm_drm`'s `sde_drm` extensions,
   `kgsl`, `msm_vidc`, `qcom,ipa`, `wcd*` Q6 ioctls. Mainline `drm/msm`,
   `v4l2`/`mem2mem`, and ALSA expose different nodes and ioctls.

So "mainline kernel + unchanged stock ROM" cannot reach a working UI. The two
routes that do reach Android on mainline, both of which need our kernel only
as a *base*:

| route | userspace | what it costs |
|-------|-----------|---------------|
| A: the ROM's own Android 17 userspace, unchanged | Infinity X as flashed today, with everything the first stage ramdisk insmods built into our Image | a kernel that carries every driver the ROM needs as `=y`, because there is no channel to deliver `.ko` files |

Route A is the mission. Debian was built and is dropped: the ROM is what has to
boot. The kernel work below (P0..P9) serves exactly one goal, a mainline
`boot.img` that runs the existing Android 17 install.

## /data: metadata encryption is the first thing that has to be right

The ROM's fstab (extracted from `stock/vendor_ramdisk.cpio` to
`/tmp/system/etc/fstab.default`) mounts `/data` as f2fs with

```
fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0,
keydirectory=/metadata/vold/metadata_encryption,
metadata_encryption=aes-256-xts:wrappedkey_v0
```

so `userdata` is never mounted directly: `fs_mgr` asks `dmctl` for a
`default-key` target over it, and vold feeds that target the hardware-wrapped
storage key KeyMint produced. `strings out/re/system.img` shows the calls
("Failed to create/populate/wait for default-key device",
`/metadata_encryption_dir`, `ro.crypto.metadata.enabled`). Three consequences:

1. **The target is not in mainline.** `drivers/md/dm-default-key.c` is an
   Android-only file, so it is backported as
   `patches/local/files/drivers/md/dm-default-key.c`, and the bio flag it reads
   (`bio->bi_skip_dm_default_key`, set by `fscrypt_set_bio_crypt_ctx()` and by
   f2fs's GC path, which writes a raw bounce page and therefore never reaches
   fscrypt) comes with it in
   `patches/local/0007-blk-crypto-add-bi_skip_dm_default_key.patch`. Without the
   flag every bio of an encrypted file's contents would hit
   `WARN_ON_ONCE(bio_has_crypt_ctx(bio))` in the target and be killed.
   The mainline `blk-crypto` API it calls is the same one Android uses
   (`blk_crypto_init_key()`, `blk_crypto_start_using_key()`,
   `bio_crypt_set_ctx()`); only `BLK_CRYPTO_KEY_TYPE_STANDARD` was renamed
   `..._RAW`, `hex2bin()` moved to `<linux/hex.h>`, and `prepare_ioctl` gained
   the `(cmd, arg, *forward)` arguments and `bdev_nr_sectors()`.

2. **Wrapped keys are upstream, so this needs no vendor blob.**
   `drivers/soc/qcom/ice.c` reads the ICE version from hardware, and
   `qcom_scm_has_wrapped_key_support()` asks TrustZone whether the four
   `QCOM_SCM_SVC_ES` calls (`DERIVE_SW_SECRET`, `GENERATE_ICE_KEY`,
   `PREPARE_ICE_KEY`, `IMPORT_ICE_KEY`) exist; if they do and
   `qcom_ice.use_wrapped_keys=1` is on the command line, the profile advertises
   `BLK_CRYPTO_KEY_TYPE_HW_WRAPPED` and `ufs-qcom.c` passes it to the disk. Our
   DTS carries the block as `ice_ufs_mem: crypto@1d88000` and the host
   references it, so `/data` is reachable with the firmware that is already in
   `tz_b` - which we never write. `qcom_scm` registers at `subsys_initcall` and
   ICE at `device_initcall`, so `__scm` is set before the version read.
   The boot log decides it: "Found QC Inline Crypto Engine (ICE) v…" and either
   "Using HWKM. Supporting wrapped keys only." or "A supported HWKM is not
   present", which would mean this device stores raw keys and the parameter has
   to go.

3. **`/data` mount options are all mainline.** `reserve_root`, `resgid`,
   `fsync_mode`, `atgc`, `gc_merge`, `age_extent_cache`, `inlinecrypt`, `atgc`
   and `checkpoint=fs` are recognised by 7.1.2's `fs/f2fs/super.c`, so no f2fs
   patch is needed. `dm-bow` is in the GKI config but nothing in this ROM's
   `fs_mgr` builds one; it is deliberately not backported.

## Boot handover (why a shim is needed)

ABL on this device loads the boot image as an Android boot image v4, then boots
the "kernel" with the **vendor** DTB it built from `dtbo_b`. A mainline `Image`
booted that way receives ravelin's vendor DT and probes nothing (incompatible
`compatible` strings, no console). So, like the Redmi 12 5G (sky) bring-up:

1. `Image` + `sm4450-xiaomi-flame.dtb` + a `SWEETLNX` header block + the shim
   are packed into the boot image's kernel section by `tools/mklinuxpayload.py`
   (`tools/shim.S`, assembled by `tools/mkshim.py`);
2. ABL jumps to the first byte of that section as it always does. With
   `CONFIG_EFI` the Image opens with the "MZ" `ccmp` pseudo-nop and the branch
   to `primary_entry` next, and it is that branch which is rewritten to reach
   the shim, so nothing has to believe our payload is a kernel;
3. ABL enters with the MMU and D-cache off, interrupts masked, at EL1, because
   that is what `Documentation/arch/arm64/booting.rst` requires of the stock
   kernel it is loading, and the shim is entered under exactly those
   conditions;
4. the shim copies the bootloader's DTB into the reserved `handover` region and
   leaves a record of it, then moves our DTB to `0xBFE00000` and the Image to
   `0xC0000000`, cleans to the PoC, invalidates the I-cache and branches to
   `image_load + entry_off` with `x0 = our dtb`, `x1 = x2 = x3 = 0`;
5. `arch/arm64/kernel/handover.c`, called from `setup_arch()` right after
   `paging_init()`, reads that record back and grafts on the two things only
   ABL knows: `/chosen/bootargs`' `androidboot.*` tags and the physical address
   of the ROM's first-stage ramdisk.

Safety is unchanged: only `boot_<slot the ROM lives in>` is ever written, and
the stock `boot.img` stays next to the output for a byte-exact restore through
recovery. See `boot-safety.txt`.

## Build recipe (CI)

```
KERNEL   = stable 7.1.2 (upstream tag, not a vendor fork)
PATCHSET = ROCKNIX/distribution@next projects/ROCKNIX/devices/SM4450/patches/linux/*.patch
LOCAL    = dts/sm4450-xiaomi-flame.dts, flame panel/touch drivers, handover hook,
           config fragment (CONFIG_MODULES=n: no .ko is loadable or deliverable)
OUTPUT   = out/flame-mainline-boot.img  (+ Image, dtb, payload.bin, SHA256SUMS)
```

`.github/workflows/kernel.yml` is the only workflow: it self-tests the shim
under qemu, builds `Image` and flame's dtb, packs the payload and the boot
image, and asserts the packing math against the bytes it produced. Nothing in
CI builds a rootfs any more; the ROM is the rootfs.

## Milestones

| # | exit condition | how it is measured |
|---|----------------|--------------------|
| P0 | patchset applies to 7.1.2 and `Image` + flame dtb build, every fragment symbol still `=y` | CI green |
| P1 | shim boots mainline and the console comes up on `ttyGS0` (ACM gadget) + `earlycon` on uart@a88000 | USB serial log from the phone |
| P2 | the ROM's own userspace reaches boot: handover recovers `androidboot.*` and the first-stage ramdisk, `super`/`/vendor` mount | `adb shell getprop`, no first-stage panic |
| P3 | display: c3f DSI panel driven by `drm/msm` + `panel-xiaomi-c3f`, and SurfaceFlinger composes on it | screen on, `dumpsys SurfaceFlinger` |
| P4 | touch: focaltech@38 on `i2c@984000` (irq 13, reset 12) | `getevent -l` |
| P5 | power: regulator set, battmgr/charger, pon power key, thermal, vibrator | `dumpsys battery`, `getevent` |
| P6 | GPU: a613 + a6xx GGU/zap, SMMU re-parented off `kgsl-iommu` | `dumpsys gpu`, glmark2 from a terminal app |
| P7 | Wi-Fi/BT: WCN3990 path (`hci_qca` for BT) | `cmd wifi status`, `hcitool scan` |
| P8 | audio: LPASS macros + WCD938x + AW88166 smartpa over glink/APR | audio out of the speaker at boot |
| P9 | camera and modem: vendor ISP/RIL blobs against a mainline kernel, declared the hardest part of the port | preview in the stock camera app |

P2 is the whole reason the handover shim exists: without the ROM's
`androidboot.*` tags and its ramdisk, first-stage init never gets to mounting
`super`, and nothing after that can be observed. Each milestone is one
`boot_<slot>` flash by the user (this workspace has no fastboot), so every CI
artifact must be a single flashable image plus a one-line `FLASH.txt`, and a
failure must always be reversible by flashing the ROM's own `boot.img` back.


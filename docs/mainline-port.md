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
KERNEL   = stable 7.2.x (upstream tag, not a vendor fork)
PATCHSET = ROCKNIX/distribution@next projects/ROCKNIX/devices/SM4450/patches/linux/*.patch
LOCAL    = dts/sm4450-xiaomi-flame.dts, flame panel/touch drivers, config fragment
OUTPUT   = out/flame-mainline-boot_b.img  (+ Image, dtb, SHA256SUMS)
```

`make ARCH=arm64 defconfig` + `config/flame.fragment`, then
`make Image dtbs modules`, then the shim/boot packing steps from
`tools/` (`mklinuxpayload.py`, `mkshim.py`, `repack_bootimg.py`).

## Milestones

| # | exit condition | how it is measured |
|---|----------------|--------------------|
| P0 | patchset applies to 7.2 and `Image`+`dtbs` build | CI green |
| P1 | shim boots mainline and the console comes up on `ttyGS0` (ACM gadget) + `earlycon` on uart@a88000 | USB serial log from the phone |
| P2 | rootfs mount: UFS (`ufshc@1d84000`, ICE) and microSD (`sdhci@8804000`, CD gpio 101) | `lsblk`, boot into Debian |
| P3 | screen: c3f DSI panel (720x1640@120, DCS init blob, DCS backlight) | visible Phosh/desktop |
| P4 | touch: focaltech@38 on `i2c@984000` (irq 13, reset 12) | `evtest` |
| P5 | power: regulator set, battmgr/charger, pon power key, thermal, vibrator | `powercap`/`iio` readouts |
| P6 | GPU: a613 + a6xx GGU/zap, SMMU re-parented off `kgsl-iommu` | `glmark2` |
| P7 | Wi-Fi/BT: icnss→mainline path or WCN3990 port (`hci_qca` for BT) | `iwctl` scan |
| P8 | audio (LPASS macros + AW88166 smartpa), camera = declared out of scope | `wpctl` |
| P9 | modem/RIL: MBIM over the vendor's own USB mode or give up | `mmcli` |

Each milestone is a `boot_b` flash by the user (this workspace has no fastboot),
so every CI artifact must be a single flashable image plus a one-line
`FLASH.txt`, and a failure must always be recoverable with
`fastboot --set-active=a`.

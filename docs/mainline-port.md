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
| touch | `edt-ft5x06`, `focaltech-ft8709` exist | `0050-drm-panel-add-focaltech-ft7131m`, `0061-input-edt-ft5x06-follow-the-panel-power-state`; flame's IC is a `fts` part (`ic-type 0x8726081c`) → may need the vendor `focaltech,fts` driver ported |
| Wi-Fi/BT | ❌ `icnss`/WCN3990 not upstream; `ath10k/ahb.c` matches only `qcom,ipq4019-wifi` | `0045`/`0046`/`0047`/`0048` are for **WCN3950** (`hci_qca`), which *is* the same WCN39xx family as flame's WCN3990 — useful, but the WLAN side still needs a port |
| audio (LPASS/CDSP, wcd/va/rx/wsa macros, soundwire, aw88166) | partial (`qcom,sc8280xp` machine driver) | `0030`, `0031`–`0034`, `0039`, `0056` |
| thermal | `qcom,tsens-v2` ✅ | DT (2 controllers on flame) |
| `qcom,ravelin-qfprom` nvmem | ❌ compatible | one-line addition in `nvmem/qcom-qfprom.c` |

Conclusion: upstream gives a bootable CPU/GIC/timer/clock/pinctrl base and
nothing else. The ROCKNIX SM4450 series (49 patches, kept in
`packages/…/patches/linux` for the anbernic RG55G1) is exactly the missing
display/GPU/ICC/PHY/PMIC/remoteproc/Type-C layer, and it is maintained against
a current kernel, so the port is mostly *assemble + fix up DT*, not write drivers.

## Boot handover (why a shim is needed)

ABL on this device loads `boot_b` as an Android boot image, then boots the
"kernel" with the **vendor** DTB it built from `dtbo_b`. A mainline `Image`
booted that way receives ravelin's vendor DT and probes nothing (incompatible
`compatible` strings, no console). So, like the Redmi 12 5G (sky) bring-up:

1. `Image` + `sm4450-xiaomi-flame.dtb` appended with a `SWEETLNX` payload header;
2. that blob is wrapped in a tiny **EL1 shim** (freestanding aarch64) that ABL
   accepts as the boot image "kernel";
3. ABL jumps to the shim at `0xA8000000` with MMU on and its own DTB;
4. the shim copies the real `Image` to `0xC0000000`, places our DTB 2 MiB below
   it, disables the MMU and branches to `Image` with `x0 = dtb`, `x1 = x2 = x3 = 0`.

Safety stays identical to the Debian work: only `boot_b` (plus `vendor_boot_b`
/ `dtbo_b` copies) is ever written, `fastboot --set-active=a` returns to Android
17 instantly, and the stock `boot.img` is kept next to the output for a
byte-exact restore.

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

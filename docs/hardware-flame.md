# flame hardware map (from the device's own vendor device tree)

Everything below was extracted from the DT that the stock Android 17 firmware
actually loads, not from a downstream kernel source tree. That makes it the
authoritative source for the mainline port: addresses, GPIO numbers, panel
timings and regulator phandles are the ones the vendor drivers run with.

Reproduce with:

```sh
python3 tools/payload_extract.py stock/payload.bin dtbo out/dtbo.img
python3 tools/dtb_split.py out/dtbo.img out/dtbs            # 4 overlays + 11 base DTBs
fdtoverlay -i out/dtbs/vb/dtb-005.dtb -o out/dtbs/merged-000.dtb out/dtbs/dtb-000.dtb
dtc -I dtb -O dts out/dtbs/merged-000.dtb > out/dtbs/flame.dts
python3 tools/dt_survey.py out/dtbs/flame.dts <path fragments...>
```

`out/dtbs/flame.dts`: 25510 lines, 2769 nodes, `model = "Ravelin 4Gb SoC"`,
`qcom,board-id = <0x00 0x600>`, msm-id `<0x25a 0x10000>` (= SM4450).
Three panel variants are present; the ones that differ only by `43 02 0g` /
`35 02 0d` are LCM lots with identical timings, so one mainline panel node
covers all of them.

## SoC

| item | value |
|------|-------|
| SoC | Qualcomm SM4450 "ravelin" (msm-id 0x25a) |
| CPUs | 6x Cortex-A55 (`cpu@0..500`) + 2x Cortex-A78 (`cpu@600`,`700`), `qcom,kryo` in vendor DT |
| GPU | `qcom,gpu-model = "Adreno613v1"` |
| PSCI | `enable-method = "psci"`, `arm,psci-1.0` (upstream dtsi has it) |
| L2/L3 | per-core L2, shared L3 (`cache-level = <3>`) |
| idle | little/big `arm,idle-state` + 2 cluster `domain-idle-state` |
| cpufreq | `qcom,sm4450-cpufreq-epss` @ `0x17d91000` |
| RAM | 4 GiB variant; **no `/memory` node in the DT** — ABL fills it from SMEM |

## Console / boot

| item | value |
|------|-------|
| `chosen/stdout-path` | `/soc/qcom,qup_uart@a88000:115200n8` |
| aliases | `serial0`=uart@a88000 (qupv3_1@ac0000), `serial1`=uart@980000, `hsuart0`=uart@988000 (both qupv3_0@9c0000) |
| pstore | `ramoops@80D00000`, `compatible = "ramoops"`, `"qcom,ramoops"` |
| earlycon | `qcom_geni` on 0xa88000 — upstream node exists as `uart7: serial@a88000`, `qcom,geni-debug-uart`, currently `status = "disabled"` |
| bootargs | long vendor list; only useful items for us: `kpti=0`, `swiotlb=noforce`, `cgroup.memory=nokmem,nosocket`, `loop.max_part=7`, `pcie_ports=compat`, `android12_only.will_be_removed_soon.memblock_nomap_remove=on` |

`a88000` in the vendor DT is exactly upstream's `uart7` — so `earlycon=qcom_geni,0xa88000`
plus `console=ttyGS0` (USB gadget) is the bring-up console; the same trick the
Debian-on-stock-kernel image uses.

## GenI QUP buses

Vendor has **two** SE controllers; upstream `sm4450.dtsi` only describes one.

| QUP | vendor addr | upstream | children that matter |
|-----|-------------|----------|----------------------|
| `qupv3_0_geni_se@9c0000` | 0x9c0000 | **missing upstream** | `i2c@984000` = touch (focaltech@38), `spi@984000` = Novatek/Focaltech SPI alt, `spi@990000`, `uart@980000`, `hsuart@988000` (BT HCI) |
| `qupv3_1_geni_se@ac0000` | 0xac0000 | `qupv3_id_0: geniqup@ac0000` (only uart7 child, disabled) | `uart@a88000` = console, `i2c@a8c000` = **external pm8010n PMIC** |

Both need `qcom,geni-se-qup` + `qcom,geni-se-ixp` style clocks and the GPI DMA
controller (`qcom,gpi-dma`) to get working I2C/SPI — see the gap list.

## Power management (SPMI)

`qcom,spmi@c42d000`, `compatible = "qcom,spmi-pmic-arb"`, reg `cnfg 0xc42d000`,
`core 0xc400000`, `chnls 0xc500000/0x400000`, `obsrvr 0xc440000`, `intr 0xc4c0000`,
`cell-index = <0>`, `qcom,ee = <0>`; 4 address cells, periph IRQ via pdc/GIC.

| PMIC | SID | what it does on flame |
|------|-----|----------------------|
| `qcom,pmk8350@0` | 0 | XO/CLK, `qcom,pm8998-pon` power key, pmic-pon-log, SPS, pinctrl |
| `qcom,pm6450@1` | 1 | main SMPS/LDO set (`s1,s5,s6,s7,s8,s9`, `l1..l*`), `pinctrl@8800` incl. **`lcd_backlight_en_default` / `lcd_backlight_pwm_default`**, `qcom,pwms@e800`, vibrator/ADC |
| `qcom,pm8010@4` | 4 | `pm8010-e-temp-alarm@2400`, `rtc@6100`, PWMs |
| `qcom,pm7250b@2` | 2 | camera AF / `pinctrl@c000` (`cam_afvdd`) |
| `qcom,pm7250b@3` | 3 | `qcom,vibrator@5300`, `usb_conn_therm`, audio |
| `pmg1110` (debug@6) | 6 | Xiaomi/NXP-style companion PMIC, regulators + ADC |
| `pm8010n@d` | (I2C) | **pm8010n on `i2c@a8c000`**: `regulator@4200/4300/4500` = L3/L4/L6N — these feed the panel/touch rails |

Also under the arb: `fsa4480@42` (USB-C flanker), `qcom,battery_charger` +
`qcom,ucsi` + `qcom,altmode` (pmic_glink clients), `charger-therm`, `pa_therm1/2`,
`cpu_therm`, `sys-therm-{3,4,6,9}`, `quiet_therm`, `bcl@1d00` + `pm7250b-bcl-lvl0..2`,
`pm7250b-ibat-lvl0/1`, `pm7250b_tz`, `adc_tm@3400/@3500`.

Backlight note: the c3f node uses `qcom,mdss-dsi-bl-pmic-control-type =
"bl_ctrl_dcs"` with `bl-min-level = <7>`, `bl-max-level = <0xfff>` and
`qcom,mdss-dsi-bl-inverted-dbv` — brightness goes over **DSI DCS (0x51), 12-bit**,
not the PM6450 LPG. The `lcd_backlight_pwm_default` pinctrl group is for the
alternative PMW path, so mainline needs no PM6450 PWM driver for this panel.

## Display

| item | value |
|------|-------|
| panel | `c3f 43 02 0g video mode hd dsi panel` (+ `35 02 0d`, `null` variants) |
| type | `dsi_video_mode`, `non_burst_sync_event`, 4 data lanes (`lane_map_0123`), all lanes enabled |
| ctrl/phy | `qcom,dsi-ctrl-num = <0>`, `qcom,dsi-phy-num = <0>` |
| resolution | 0x2d0 x 0x668 = **720 x 1640**, bpp 24, `rgb_swap_rgb` |
| porches | h: fp 25, pulse 3, bp 89 → 837; v: fp 331, pulse 2, bp 20 → 1993 |
| fps | 120 (`framerate = <0x78>`); `qcom,dsi-supported-dfps-list = <120 90 60>` with `pan-enable-dynamic-fps`, `dfps_immediate_porch_mode_vfp` |
| pixel clock | 837 x 1993 x 120 = **200.37 MHz** → lane rate ~1.203 Gbps @ 4 lanes |
| phy timings | `qcom,mdss-dsi-panel-phy-timings = [00 28 0a 0b 1b 1a 0a 0b 0a 02 04 00 21 0f]` |
| init | `qcom,mdss-dsi-on-command`: ~100 DCS writes (0xFF/0xFB bank switches, 0x11, 0x29, gamma 0x30-0x67, 0xD0-0xDF, 0xB4) — full blob in `flame.dts:16919` |
| off | `0x28` display-off, `0x10` enter-sleep (+ `screen-off-command` 28, 10) |
| reset | `qcom,platform-reset-gpio` = tlmm **92** (`<0x124 0x5c 0x0>`), sequence `<0 5><1 5><0 5><1 10>` |
| LCM enables | `qcom,lcm-enp-gpio` = tlmm **26**, `qcom,lcm-enn-gpio` = tlmm **27** |
| ESD | `qcom,esd-err-irq-gpio` = tlmm **33**, `te-pin-select = <1>` |
| topology | `qcom,display-topology = <1 0 1>` (1 INTF, 1 DSC-ish pipe config) |
| physical | 70 x 160 mm (`0x46`, `0xa0`) |
| supply | `qcom,panel-supply-entries` (preset node list) + `lcd_bias@3e` |
| SMMU | vendor uses `qcom,smmu_sde_unsec/sec` + `rot` callbacks (ring-tones); mainline wants `qcom,adreno-smmu` + `iommus` on mdss |

Because the init blob is mandatory and mainline `panel-simple` cannot carry a
DCS sequence, a dedicated `drm/panel/panel-xiaomi-c3f.c` (or `panel-simple-dsi`
variant with `power_on` writing `mipi_dsi_dcs_write_seq`) is required; the blob
above is the content.

## Touch

| item | value |
|------|-------|
| node | `focaltech@38` under `i2c@984000` (`qcom,i2c-geni`) — I2C address **0x38** |
| IC | `focaltech,ic-type = <0x8726081c>` (FT8x46 class), `focaltech,max-touch-number = <10>` |
| irq | `irq-gpio` = tlmm **13**, flags 0x2008 (edge/falling + pull up) |
| reset | `reset-gpio` = tlmm **12** |
| coords | `display-coords = <0 0 0x438 0x968>` = 1080 x 2408 vendor-scaled; real panel is 720 x 1640 (0x2d0/0x668) — scale by 2/3 |
| alt | `spi@984000` / `spi@a90000` with `"novatek,NVT-ts-spi","focaltech,fts"` and `touch@0`; plus `xiaomi_touch` (`xiaomi-touch`) report node |

Mainline path: `touchscreen@38` with `compatible = "focaltech,ft8726"`-style
(`focaltech_ft8709_i2c.c` supports ft5x06/ft8709 only) — the vendor DT says the
IC is a `fts` family part, so `focaltech,fts` from the vendor driver must be
ported or an existing `edt-ft5x06`/`it32802`-class driver tried. The SPI option
must stay disabled (touch is on I2C for this LCM).

## Storage

| item | value |
|------|-------|
| UFS | `ufshc@1d84000`, `compatible = "qcom,ufshc"`, reg `ufs_mem 0x1d84000/0x3000`, **`ice 0x1d88000/0x18000`** (hardware crypto), `non-removable`, `lanes-per-direction = <2>`, IRQ `<0 0x109 0x4>` = GIC 265, `phys` → ufsiphy, `resets` from gcc, `iommus` from apps SMMU, nvmem cells `ufs_dev`, `boot_conf` from qfprom |
| microSD | `sdhci@8804000`, `qcom,sdhci-msm-v5`, bus-width **4**, `cd-gpios` = tlmm **101**, `vdd-supply` regulator |
| SDCC1 | `sdhci@7c4000`, `qcom,sdhci-msm-v5`, reg `0x7c4000`, `0x7c5000`, `0x7c8000/0x18000` (inline crypto), bus-width **8**, no card-detect → not the user slot; leave disabled until verified |
| aliases | `ufshc1`, `mmc0` = 7c4000, `mmc1` = 8804000 |

## USB

`ssusb@a600000` (`qcom,dwc-usb3-msm`) wrapping `dwc3@a600000` (`snps,dwc3`),
`usb_nop_phy` for the HS port plus `usb3_phy_wrapper_gcc_usb30_pipe_clk`;
Type-C handling is `qcom,ucsi`/`qcom,altmode` over **pmic_glink** and a vendor
`fsa4480@42` flanker. Mainline needs `dwc3-qcom` (it matches `qcom,snps-dwc3`
generically now), a QMP/USB3 PHY node and either `typec` + `pmic_glink` or the
GL865/it6603-style CC driver; the vendor `qcom,dwc-usb3-msm` glue cannot be
mainlined as-is. First milestone can use peripheral-only gadget (ACM + NCM +
ADB-less SSH over the gadget) exactly as the stock kernel setup does.

## Wireless

| item | value |
|------|-------|
| Wi-Fi | `icnss@22800000`, `compatible = "qcom,icnss"` + `wpss-support-enable`; regions `wpss_moselle_region@85300000`, `wlan_msa_mem_region@82a00000` |
| combo | **WCN3990**: `bt_wcn3990` (`compatible = "qcom,wcn3990"`), BT HCI on `hsuart0` (0x988000), audio over **SLIM bus** (`btfmslim-driver`, `slim217,221`) |
| mainline | `qcom,icnss`/`icnss2` is *not* upstream and ath10k's `ahb`/`snoc` path has no WCN3990 binding (`ath10k/ahb.c` matches only `qcom,ipq4019-wifi`). Also SLIM bus (`slimcomm`) support for the audio side is vendor-only. Wi-Fi/BT is the hardest item in this port |

## Audio / compute / misc

- LPASS: `spf_core_platform` with `lpass-cdc` macros — `wsa-macro@3240000`,
  `wsa2-macro@31e0000`, `rx-macro@3200000`, `va-macro@33f0000`, aliases
  `swr0..swr3`, 4x DMIC pin groups, `lpass_tbu@151f5000` (`qcom,qsmmuv500-tbu`),
  sound card `qcom,model = "ravelin-qrd-snd-card"`, external codec clk 0x124f800.
- ADSP: `remoteproc-adsp@03000000` (`qcom,ravelin-adsp-pas`) with `glink-edge`
  and `qcom,fastrpc` `compute-cb@1/2/3`; `adsp_heap_region` = `shared-dma-pool`.
- Other remote regions: `mpss_region@8a000000` (modem), `camera_region@84b00000`,
  `gpu_microcode_region@89c1a000` (zap), `cpucp_fw_region@80b00000`,
  `cpusys_vm_region@e0600000`.
- GPU: `qcom,kgsl-iommu@3da0000` with `qcom,smmu-kgsl-cb` clients (a *separate*
  GPU SMMU — mainline wants the GPU on the apps SMMU with `adreno-smmu` +
  PP/PGTable, this mismatch is why sky's GPU probes then time out);
  `gpu_speed_bin@119` nvmem; `gpucc@3d90000` exists upstream.
- NVMEM: `qfprom@221c8000` = `qcom,ravelin-qfprom`, `qcom,qfprom` (needs a new
  compatible in `nvmem/qcom-qfprom.c`, trivial); `qfprom@0` = `qcom,qfprom-sys`.
- Thermal: two `qcom,tsens-v2` controllers + PMIC ADC/`adc_tm` sensors.
- `tcsr_mutex` @ `0x1f40000` (upstream has it), `pdc@b220000` (upstream has it).

## What cannot be mainlined as-is

| vendor stack | verdict |
|--------------|---------|
| `qcom,dwc-usb3-msm` | replace with `dwc3-qcom` + `typec`; glue is vendor-only |
| `fsa4480` USB-C flanker | no mainline driver; use `tcpm`/`ptn36022`-style or GPIO-only orientation |
| `icnss` / WCN3990 + SLIM BT audio | needs a port (ath11k/ath10k-snoc rework) — treat as out of scope for bring-up |
| camera ISP (`camcc` exists but camx stack) | vendor-only, effectively not portable |
| modem/RIL (`mpss_region`, `qcom,rmem`) | vendor-only; MBIM over USB gadget is the fallback |
| `xiaomi_touch` / `qcom,panel-*` / `qcom,uart-bt` extras | vendor-only report nodes, drop them |
| `pm8010n` I2C PMIC, `pmg1110` | no upstream driver — port from the vendor `qcom,spmi-pmic`-style regulator code |
| GPU on `kgsl-iommu` | must be re-parented to apps SMMU/`adreno-smmu`, not ported |

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
2. **UAPI.** Even if a mainline tree carried a driver for the same block, some
   HALs use the *vendor* driver interface: `msm_drm`'s `sde_drm` property
   extension, `msm_vidc`, `qcom,ipa`, the Q6 `wcd*` ioctls. This is not true of
   the whole stack though, and the difference matters enough that it was
   measured rather than assumed - see "What the ROM's HALs actually open"
   below. The GPU stack, which looked like the worst case, turns out to carry a
   backend that speaks mainline's `drm/msm` UAPI byte for byte.

So "mainline kernel + unchanged stock ROM" cannot reach a working UI. The two
routes that do reach Android on mainline, both of which need our kernel only
as a *base*:

| route | userspace | what it costs |
|-------|-----------|---------------|
| A: the ROM's own Android 17 userspace, unchanged | Infinity X as flashed today, with everything the first stage ramdisk insmods built into our Image | a kernel that carries every driver the ROM needs as `=y`, because there is no channel to deliver `.ko` files |

Route A is the mission. Debian was built and is dropped: the ROM is what has to
boot. The kernel work below (P0..P9) serves exactly one goal, a mainline
`boot.img` that runs the existing Android 17 install.

## What the ROM's HALs actually open

Everything in this section was read out of the ROM's own binaries, not inferred
from the module list: `debugfs -R "dump /lib64/X"` out of `out/re/vendor.img`
(which is ext4, not erofs), then disassembly and decompilation. Pseudo-C,
string cross-references and function inventories came from Craft Anything in
Deep mode; the dumps are under `/root/work/vaso`, its evidence project under
`/root/work/cany`.

Two sweeps made the picture tractable, and both are worth re-running when the
ROM changes: a device-node and sysfs inventory (every `/dev/` and `/sys/`
string in each HAL binary, with the function that references it) and an ioctl
decode (fold the `mov`/`movk` pairs in front of every `ioctl@plt`/`drmIoctl`
call, then compare the request word against `_IOWR('d', DRM_COMMAND_BASE + n,
sizeof(struct))` computed from *this tree's* headers, so a vendor struct that
grew a field shows up as a different size and not as a lucky match).

### GPU: the result that changed the plan

`libgsl.so` is the Adreno system layer that EGL, Vulkan, CamX and the codecs
all sit on. `gsl_ioctl_init` (@0xc8054) picks its kernel backend with `access()`:

```
access("/dev/kgsl-3d0", F_OK)        -> kgsl ops table
access("/dev/dri/renderD129", F_OK)  -> DRM ops table
otherwise                            -> "Device access failed, stub the gpu"
```

So the userspace already knows how to run on a DRM-only kernel, and the DRM
backend is not a vendor interface: the request words it issues are mainline's
own - `GEM_NEW 0xc0106442`, `GEM_INFO 0xc0186443`, `GEM_SUBMIT 0xc0486446`,
`SUBMITQUEUE_NEW 0xc00c644a`, `SUBMITQUEUE_CLOSE 0x4004644b`, `GET_PARAM
0xc0186440`, each matching this tree's command number *and* struct size.
`ioctl_drm_driver_entry` (@0x21d3e0) then `opendir`s `/dev/dri`, `readdir`s the
`render*` entries, opens each and compares `DRM_IOCTL_VERSION`'s driver name,
so it finds the msm node regardless of which minor it ended up with.

That leaves three things, all of which are ours to fix:

1. something must *exist* at `/dev/dri/renderD129`, or the DRM backend is never
   entered. `CONFIG_DRM_VGEM=y` registers a second, hardware-less driver whose
   only job is to own the extra render minor - and *which* minor it owns is not
   a detail. Primary and render minors come out of one xarray in registration
   order (`drm_drv.c: drm_minor_get_xa()`, `DRM_MINOR_LIMIT()`), and
   `/vendor/etc/selinux/vendor_file_contexts` labels exactly three `/dev/dri`
   names as `graphics_device`: `card0`, `controlD64`, `renderD128`. Everything
   else is plain `device`, which `plat_sepolicy.cil` neverallows any domain to
   open. vgem's own initcall used to win the race, because msm only registers
   once its components bind: vgem ate `card0`/`renderD128` and the real GPU
   landed on `renderD129`, unopenable, dark screen. `patches/local/0008` drops
   the initcall and the manifest rule calls `vgem_init()` from `msm_drm_init()`
   right after msm's own `drm_dev_register()` succeeds, so the three labelled
   names are the real driver's and vgem is the placeholder at `renderD129`.
   Permissions are not a problem either way: `/system/etc/ueventd.rc:60` is
   `/dev/dri/* 0666 root graphics` and the stock Image has no devtmpfs, so
   ueventd has always owned those nodes.
2. the GPU node in the DT has to be enabled - mainline describes SM4450's DPU
   (`qcom,sm4450-dpu`, `dpu_8_6_sm4450.h`) and its GPU (`adreno_is_a613`,
   chip id `0x06010300`, the `a6xx_gmuwrapper_funcs` path, `A613 (gen6_3_26_0)`
   GBIF quirk at `a6xx_gpu.c:1321`), but upstream `sm4450.dtsi` carries **no
   gpu node at all**, so the board file is the only place it can come from.
3. the zap shader has to be findable under the name the ROM ships. The catalog
   asks for `a613_zap.mbn`; the ROM has `gen6_3_26_0_zap.{elf,mbn,mdt,b0x}` and
   no `a613_zap.mbn`. Setting `firmware-name` on the `zap-shader` child makes
   `adreno_gpu.c:75` use that name and skip the catalog, and it has to be the
   `.elf` because mainline's `mdt_loader.c` only parses `elf32_hdr`. The one
   file is self-contained (one `PT_LOAD`, `p_offset 0x1000` + `p_filesz 0x7b0`
   = the whole 6064 bytes), so the split `.b02` request never happens.

The last piece needs one more thing, and it is not the thing it first looked
like. `init.qti.kernel.rc:67` does write `force_sysfs_fallback 1`, but that knob
only controls the *sysfs userhelper*, and `adreno_gpu.c` calls
`request_firmware_direct()`, which passes `FW_OPT_NOFALLBACK_SYSFS` - a flag
`firmware.h` documents as taking precedence over `FW_OPT_UEVENT` and
`FW_OPT_USERHELPER`. So ueventd is never asked and cannot serve
`/vendor/firmware` for this lookup; the ROM's own knob is irrelevant to it. What
works is `firmware_class.path=/vendor/firmware` on the command line, which adds
a directory to the direct filesystem search, and that is what the board file's
`chosen` node now sets. If the GPU probes before init mounts /vendor the zap
request still fails, and that is survivable by design: a failed request sets
`zap_available = false` rather than returning an error, which costs protected
contexts (Widevine L1) and nothing else. Stock never needed any of this - its
`msm_kgsl.ko` was the backend.

One ceiling here is the ROM's own policy and not something a kernel can fix.
`graphics_device` chr_file is granted to `surfaceflinger`,
`hal_graphics_composer`, `gpuservice`, `charger_type` and `slideshow`, and
`plat_sepolicy.cil` neverallows it to `untrusted_app`, `isolated_app` and
`shell`. `/dev/kgsl-3d0` (`gpu_device`) is granted only to `gpu_sphal_domain`,
`mmd` and `rs`. So no application process may hold a DRM fd on this build at
all: what the GPU accelerates is the compositor, composer and GL service, while
app-side rendering stays on whatever path the platform allows. That is how the
ROM is already configured; it is stated here so a "why is my game slow" report
is not mistaken for a port bug.

### Display and gralloc

| interface | who | mainline | outcome |
|-----------|-----|----------|---------|
| `/dev/dma_heap/qcom,system{,-uncached,-movable}`, `qcom,display` | `SnapDMAAllocator::GetHeapInfo` (snapalloc-impl.so @0x2ca58), `gralloc::DmaManager::GetHeapInfo` (libgralloccore.so @0x178f8) | registers `system` and `reserved` only | **fatal, fixed**: `CONFIG_DMABUF_HEAPS_QCOM_COMPAT` aliases the names onto the system heap. `AllocBuffer` (@0x2c444) calls `BufferAllocator::Alloc` once and returns error 3; `MapNameToIonHeap` (libdmabufheap.so @0x9540) is a two-instruction stub, so there is no retry and no fallback anywhere. |
| `/dev/qti-smmu-proxy` | string built in `DmaManager::GetInstance` (@0x16db8) and the snapalloc ctor (@0x2c39c) | no such driver | **resolved, and it is not a gap.** The vendor module (`out/re/ko/smmu_proxy_dlkm.ko`) is a Gunyah client: it registers `smmu-proxy-sender`, reads `qti,smmu-proxy-cb-id`, and its `smmu_proxy_dev_ioctl` forwards map/unmap/`switch_sid` to *another VM* over `gh_msgq_send`/`gh_msgq_recv`, lending the buffer with `mem_buf_lend`. Its `.modinfo` depends are `smcinvoke_dlkm,mem_buf_dev,gh_msgq,qcom_dma_heaps`. Nothing normal-allocation ever opens it - which matches the decompile, where the path is only constructed and stored - and no display driver registers its callbacks: `msm_drm.ko`, `msm_kgsl.ko`, `camera.ko` and `heap_mem_ext_v01.ko` contain zero "proxy" strings. So the loss is trusted-UI/secure buffer mapping into a protected context bank, which is already an accepted loss with the secure heaps. |
| `sde_drm_*` DRM properties | `libsdedrm.so` (`DRMPropertyManager::GetPropertyEnum` @0x42254, `DRMCrtc/DRMConnector/DRMPlane::ParseProperties`, `DRMPlane::GetTypeInfo`) | mainline dpu1 creates none of them | **still the top display risk, narrowed to one site.** The names are not in the file - they are built from length-switched character compares, so `strings` shows only `sde_drm_csc_v1`, `sde_drm_roi_v1`, `sde_drm_scaler_v2`. `ParseProperties` walks `drmModeObjectGetProperties()` and maps each name through `GetPropertyEnum`, so a property the kernel does not have is skipped with `DRMProperty %s missing from global property mapping` and an extra one is simply not in the map. What makes this more than cosmetic is that the library also links libc++'s `map::at`, whose `key not found` path is referenced from nine places: `DRMConnectorManager::Free`, `DRMEncoderManager::DumpById`, `DRMCrtcManager::{DumpById,Perform,Free,PostValidate,PostCommit}`, `DRMPlaneManager::DumpById` and `DRMPlane::GetTypeInfo`. Eight of those are keyed by DRM *object id* and only throw on a teardown race. One is not: `DRMPlane::GetTypeInfo(const map<DRMProperty, tuple<uint64_t, drmModePropertyPtr>> &)` is keyed by **property enum**, so a plane-classification property the vendor driver would have created and mainline does not is an uncaught `std::out_of_range` inside the composer, at plane enumeration, before any frame is ever posted. Outcomes are one boot apart: either logcat is noisy with "missing from global property mapping" and the UI works (degraded colour, demura, LTM), or `vendor.qti.hardware.display.composer-service` dies on the first commit. Read it with `logcat -s SDM libsdedrm` and `dumpsys SurfaceFlinger \| head` before changing anything. If it is the second case the fix is to hand the HAL the properties it enumerates, not to change the compositor. |
| `/sys/module/msm_drm/parameters/` | `HWInfoDRM::GetPanelBootParamString` (libsdmdal.so @0x1dbd0) | built-in, and the module is `msm` | read fails, panel boot strings unavailable. Cosmetic. |
| `/sys/class/backlight/panel%d-backlight/` | `HWPeripheralDRM::GetHWPanelMaxBrightness` (@0x31c80) | DSI panel brightness in mainline is a DRM property, not a backlight device | **gap.** Fixable in our panel driver by registering a `panel0-backlight` device. |
| `/dev/video`, `/sys/class/video4linux/video` (rotator) | `HWInfoDRM::GetHWRotatorInfo` (@0x1b30c) | no SM4450 rotator driver | HWC loses the rotator and falls back to GPU. |

### The property set the vendor display driver had and mainline does not

`grep -rn drm_property_create drivers/gpu/drm/msm/` returns **nothing**: mainline
msm creates no custom DRM properties at all, on any object, in any generation of
the driver. Every atomic property on a mainline crtc/plane/connector comes from
DRM core. The vendor tree, by contrast, creates a couple of dozen, and the
composer HAL was written against that set.

Which set is not something to guess at. `tools/drm_prop_gap.py` intersects the
strings in the stock `msm_drm.ko` with the strings in `libsdedrm.so` and removes
everything mainline's msm driver mentions as a quoted identifier; what is left is
what the two agreed on and this kernel will not have (101 words, a superset: ELF
section names and libc symbols leak in because the filter is a shape match, not
a curated list). The property-shaped ones, grouped by what they are for:

```
plane   alpha blend_op h_decimate v_decimate hfp vfp multirect_mode
        excl_rect_v1 src_config scaler_v1 scaler_v2 dim_layer_v1
        csc_v1 csc_dma_v1 lut_cir lut_ed lut_sep ds_lut_cir ds_lut_ed ds_lut_sep
crtc    topology_control early_fence_line frame_trigger_mode fb_translation_mode
        output_fence output_fence_offset input_fence mode_properties
postproc demura dnsc_blur spr spr_pack_type dspp dspp_caps pma inverse_pma
        dimming_bl_lut dimming_dyn_ctrl dimming_min_bl sv_bl_scale bl_scale
        dyn_bit_clk dyn_transfer_time brightness panel_mode qsync_mode
connector supported_colorspaces ext_hdr_properties hdr_properties hdr_metadata
        security_level dcs
bw/clock core_ab core_ib dram_ab dram_ib llcc_ab llcc_ib rot_clk
        rot_prefill_bw true_inline_rot_rev wb_usage_type dsc_mode
        vm_request_state idle_pc_state cache_state autorefresh
```

Treat that as the map of what the composer will not find, not as a to-do list:
mainline has equivalents for some (core `pixel_blend_mode` for `blend_op`, core
`rotation`, core `zpos`/`alpha`, `IN_FENCE_FD` for `input_fence`) and no
equivalent for the rest, which are DPU block knobs. The known consequence of
absent post-processing properties is degraded image quality. The unknown, and
the reason this section exists next to the `map::at` finding above, is whether
one of them is a *precondition* - `DRMPlane::GetTypeInfo` looks properties up by
enum in a `std::map` with `.at()`, and every other `.at()` in that library is
keyed by DRM object id.

Regenerate with `python3 tools/drm_prop_gap.py`; the three input paths are
overridable through `MSM_DRM_KO`, `LIBSDEDRM` and `MSM_SRC`.

### Everything else

| subsystem | what it opens | state |
|-----------|---------------|-------|
| audio | `libagm.so::device_init` (@0x28ec8) opens `/sys/kernel/snd_card/card_state`, reads one byte and waits for `1`, retrying 99 times with `sleep(1)`; `/sys/kernel/aud_dev/state` is the same shape | **no mainline driver creates these**, so the audio HAL blocks about 99 seconds at boot and then gives way. Needs either a compat sysfs node or the vendor ASoC machine driver; `audio_prm_dlkm.ko`, `audio_pkt_dlkm.ko` and `audpkt_ion_dlkm.ko` are all on the stock module list. |
| DSP RPC | `libadsprpc.so` (@0x3a9d0) opens `/dev/fastrpc-adsp`, `-cdsp`, `-sdsp`, `-mdsp` and their `-secure` variants, plus `/dev/subsys_adsp`, `/dev/subsys_cdsp`, `/dev/subsys_slpi`, and reads `/sys/class/remoteproc/remoteproc*` | mainline *has* `fastrpc` and `qcom_q6v5_pas`; both are `=y` and patch 0003 adds the `qcom,sm4450-adsp-pas` / `-wpss-pas` matches, so nothing is missing on the driver side. What is missing is an image the kernel can be given: `/vendor/firmware` holds only CAMERA_ICP, the GPU zap+sqe, gmu, sipa, venus and vpu20 (63 entries, read off vendor.img) and **no adsp or wpss file anywhere under /vendor**. The one route that could serve them is the sysfs fallback - `CONFIG_FW_LOADER_USER_HELPER=y`, so ueventd answers firmware requests from `/etc/firmware/ /odm/firmware/ /vendor/firmware/ /firmware/image/` (system/etc/ueventd.rc) plus `/vendor/firmware_mnt/image/` (vendor/etc/ueventd.rc), where Qualcomm keeps the subsystem images - and a name like `qcom/sm4450/RG55G1/adsp.mbn` can never be found by a path-joining agent, so the board file now carries no `firmware-name` for ADSP at all (driver default `adsp.mbn`) and the vendor's own `adrastea/wpss.mdt` for WPSS, taken verbatim from flame's dtbo. |
| camera | `camera.qcom.so` opens `/dev/media%d`, reads `/sys/kernel/camera/` and `/sys/devices/soc0/soc_id` | no mainline ISP for SM4450 (`camera.ko`, `camcc-sm4450.ko` are vendor). Expect CamX to fail at media-node enumeration; the config-only part (CamX clock controller) is already `=y`. |
| GPU sysmon | `/sys/class/kgsl/kgsl-3d0/max_gpuclk` (libsdmextension.so @0x94954), `/sys/class/kgsl/kgsl-3d0/snapshot`, `/sys/kernel/debug/kgsl/proc/%d/mem` (libgsl) | kgsl-only. Super-resolution and the GPU frequency strategy lose their input; nothing fails to boot. |
| sensors | `sensors.qti` writes `/sys/kernel/boot_slpi/ssr`; config JSONs name `flame_*` parts | SLPI/session bus is vendor (`gh_*`, `qcom_glink`); sensor hub is a separate problem from the kernel's own IIO drivers. |

`/dev/dma_heap` names the ROM labels but that we deliberately do **not** alias:
`qcom,secure-pixel`, `qcom,secure-cdsp`, `qcom,secure-non-pixel`,
`qcom,qseecom{,-ta}`, `qcom,cp_sec_display`, `qcom,cp_camera`, `qcom,sp-hlos`,
`qcom,cma-secure-cdsp` (TrustZone memory), and `qcom,adsp`, `qcom,ubwcp`,
`qcom,ubwcp-movable`, `qcom,audio-ml` (DSP-shared). Handing those out normal
buddy pages would turn a clean "cannot open heap" into silent corruption of
whatever the DSP is reading, so Widevine L1 and secure capture are the accepted
losses.


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

## First stage: where `/init` actually comes from

The first thing the ROM needs from `boot.img` is not a driver, it is PID 1, and
its location is easy to guess wrong. Both halves of the boot image were read
out (`stock/boot.img`, `stock/vendor_boot.img`, `out/re/`):

| image | section | contents |
|-------|---------|----------|
| `boot.img` v4 | kernel | 36,456,960 bytes of `6.6.82-android15-8` Image |
| `boot.img` v4 | **generic ramdisk** | 2,600,656 bytes, 24 entries: `/init` (2,763,568, `-rwxr-x--- root root`), `system/bin/toolbox_ramdisk`, `system/bin/snapuserd_ramdisk`, `system/etc/ramdisk/build.prop`, and the empty mount points |
| `vendor_boot.img` | vendor ramdisk | 16,134,011 bytes LZ4, one cpio, 324 entries: `lib/modules/*.ko` (288) + `modules.load`/`.dep`/`.alias`/`.blocklist` and `system/bin/*` |

**`/init` is in boot.img, not in vendor_boot.** A boot image packed with
`ramdisk_size=0` - which is what CI ships, and ships on purpose - therefore hands
the kernel a rootfs with no init: the kernel unpacks the vendor ramdisk, finds
neither `/init` nor `/linuxrc`, and the device comes back in about ten seconds.
That was the whole of the observed "reboot loop", not a driver problem.

ABL concatenates generic + vendor ramdisks into one LZ4 legacy stream and points
`linux,initrd0` at the result, so `handover.c`'s `take_initrd()` picks up both
halves as one buffer as long as our image supplies the first; `tools/mklz4legacy.py`
exists because our own half has to be framed the same way when we build one.

Those bytes belong to the ROM, so they are not in this public repository. They
live in the private `i7m7r8/flame-stock` repo as `ramdisk.img` - nothing else,
no kernel and no firmware - and the last CI step downloads them with the
`STOCK_TOKEN` repository secret into `$RUNNER_TEMP` (never into `out/`, which
`upload-artifact` would publish), checks the hash against
`config/stock-ramdisk.sha256`, and attaches them:

```
python3 tools/attach_rom_ramdisk.py --boot flame-mainline-boot.img \
        --rom-ramdisk "$RUNNER_TEMP/ramdisk.img" -o flame-mainline-final.img
```

It re-packs the *same* kernel bytes with the ROM's ramdisk section, re-derives
`os_version` and the header cmdline from the ROM's header, and then reads its own
output back and refuses to finish unless both sections are byte-identical to the
inputs.

`--rom-boot` takes the phone's own `boot.img` instead, which is the route to use
if the ROM on the device is not the build `flame-stock` holds: dump the slot's
partition, run the same command against that. `RAMDISK.txt` in the artifact
records which hash went into the image you downloaded.

**Module loading does not block us, and no kernel-side lie is needed.** The
first stage ends with `LOG(FATAL) << "Failed to load kernel modules"`, which
looks like a dead end for a mainline kernel that cannot accept 288 vendor `.ko`.
It is not: `LoadKernelModules()` only returns that failure when
`modules_loaded = m.GetModuleCount()` is greater than zero, and `module_count_`
is incremented once per *successful* `finit_module()` in
`libmodprobe/libmodprobe_ext.cpp`. Every vendor module fails (`CONFIG_MODULES=n`,
so `finit_module` returns `-ENOSYS`; even with it on, the vermagic and CRCs are
for `6.6.82-android15-8`), the count stays zero, and the function returns true
after logging nothing. The one knob that would have relaxed the check further,
`androidboot.first_stage_console`, is compiled out of this ROM anyway:
`ALLOW_FIRST_STAGE_CONSOLE=0` for non-debuggable builds, and the string
`androidboot.first_stage_console` is absent from the shipped `/init` while
`Failed to load kernel modules` is present. `androidboot.load_modules_parallel`
is read from the *bootconfig*, which we never touch.

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

## How this kernel learns how much RAM the phone has

Every DTB the ROM ships - all six ravelin/parrot bases in `boot.img`, the same
six in `vendor_boot.img`, and all four `dtbo` overlays including flame's own -
carries

```
memory { device_type = "memory"; reg = <0x00 0x00 0x00 0x00>; };
```

a 16-byte placeholder with no unit address. XBL/UEFI writes the real bank list
into it before the kernel is started, which is why the vendor kernel sees all
4 GB from a tree that declares nothing. Our board file therefore cannot treat
its own `/memory` as documentation of the hardware; it is a value that either
gets overwritten or is used as-is.

The fallback literal is pinned to what can be *proved* from the shipped files,
and the proof comes from the carveouts: a `no-map` region can only exist inside
DRAM, so the highest end address across every vendor tree is memory we may
claim without guessing. That is `trusted_apps_region@e9480000`, ending at
`0xea680000`, i.e. `0x6a680000` - 1.66 GB - above the `0x80000000` base. The
vendor's own reserved-memory also has a reusable 16 MB region with
`alloc-ranges = <0x01 0x00 0xfffffffe 0xffffffff>`, which is only satisfiable
if there is DRAM above 4 GB, so a 4 GB part certainly has a second bank; its
base and size appear in no file we can read offline. That gap is precisely what
`handover_memory_fixup()` closes at boot by rewriting our `/memory` from the
copy of ABL's patched tree the shim parked, and `dmesg` says which of the two
happened: either `handover: memory from the bootloader: 0x...@0x...` or
`keeping the built-in literal`. A phone that reports roughly 1.6 GB total RAM
is the second case, and the first thing to look at is then the shim record, not
the memory driver.

Three slots are written rather than one, because the graft uses
`fdt_setprop_inplace()` and that call refuses to change a property's length;
spare slots repeat the first range, which memblock folds away silently, so the
graft can hold up to three banks without resizing anything.

Total reservation is 480.8 MB, of which 435.8 MB is the vendor's own carve-out
table copied address-for-address and size-for-size (verified by diffing our
`/reserved-memory` against the ravelin base DTB: 33 regions match exactly, and
nothing in the vendor tree is missing here). The two additions are ours:
`splash_region` at `0xb8000000` (44 MB, ABL's splash, needed until DPU takes
over) and `handover@bc000000` (2 MB, the shim's scratch). Cutting a vendor
carve-out would not give that RAM back to Linux in any usable way - ADSP, WPSS,
TrustUI VM and the secure regions hold firmware that is already running when
the kernel starts - so shrinking them is not on the table.

## Build recipe (CI)

```
KERNEL   = stable 7.1.2 (upstream tag, not a vendor fork)
PATCHSET = ROCKNIX/distribution@next projects/ROCKNIX/devices/SM4450/patches/linux/*.patch
LOCAL    = dts/sm4450-xiaomi-flame.dts, flame panel/touch drivers, handover hook,
           config fragment (CONFIG_MODULES=n: no .ko is loadable or deliverable)
OUTPUT   = out/flame-mainline-boot.img  (+ Image, dtb, payload.bin, SHA256SUMS)
FINAL    = tools/attach_rom_ramdisk.py adds the ROM's own generic ramdisk
           (/init), fetched in the job from the private i7m7r8/flame-stock repo
           with the STOCK_TOKEN secret and hash-checked first; the step fails
           the build rather than publish an image that cannot boot
```

## Getting the log out of a boot that does not finish

The first builds of this port reset in about ten seconds and left nothing
anywhere: no panic on the oops partition, no console, no USB. Three separate
reasons, each fixed, and the fixes are worth keeping in order because they are
three different kinds of silence.

**A watchdog bite is not a panic.** The vendor device tree declares
`qcom,wdt@17410000` with `qcom,bark-time = <11000>` and `qcom,pet-time =
<9360>`, and the ROM's own `qcom_soc_wdt.ko` maps that block as `wdt-base`: pet
at +0x04, enable at +0x08, status at +0x0c, bark and bite at +0x10 and +0x14,
with ticks of `ms * 32768 / 1000` from the always-on sleep clock. That layout is
mainline's `reg_offset_data_kpss` exactly, and the ROM's `qcom_wdt_core.ko` pets
the timer from a kernel thread that only exists once the vendor modules have
loaded from the vendor ramdisk. ABL arms the timer on the way into Linux, so a
kernel that neither pets nor stops it is reset at the bark - a hardware reset
that takes no path through the kernel and so can never reach pstore, a console
or anything else. `tools/shim.S` therefore writes the ROM's own stop (pet, then
0 to +0x08) as its first act, before the Image copy. Mainline's `qcom-wdt.c`
does not bind this node at all - its table knows `qcom,kpss-wdt` and friends,
not the vendor's `qcom,msm-watchdog` - and the board DTS deliberately has no
watchdog node, so nothing re-arms it later. The timer is volatile: the next
stock boot arms it from LK exactly as before.

**The panel is a console.** ABL leaves the display controller scanning a
framebuffer in RAM, and the board DTS declares that region both as
reserved-memory and as a `simple-framebuffer` child of `/chosen`;
`of_platform_populate` turns that child into a device, `CONFIG_FB_SIMPLE` binds
it, `console=tty0` puts the VT console on the printk list and `fbcon=map:0`
pins that console to this framebuffer. From about a second into
boot, every `printk` is drawn into the picture the panel is already showing:
nothing to solder, no recovery, no root, and it survives a device that then
resets or hangs. It is also a negative test worth as much as the log - a boot
where the splash never turns to text did not reach the framebuffer console,
which puts the fault in the shim or below rather than in a driver.

`console=tty0` is not decoration and `fbcon=map:0` alone does not work.
`register_console()` enables a console driver only if it matches an entry on the
preferred-console list (`try_enable_preferred_console`, and an unmatched driver
is dropped without ever being registered), and on arm64 nothing adds the VT
console to that list: only `of_console_check()` does the equivalent, for the
`/chosen/stdout-path` device, and it returns immediately once any `console=` is
on the command line. So the bootargs say `console=tty0` themselves, ahead of
`console=ttyMSM0` so the serial keeps `/dev/console`.

`CONFIG_FRAMEBUFFER_CONSOLE_DETECT_PRIMARY` would veto that, because it maps
only the *primary* device and `/chosen/stdout-path` points at the debug UART.
It cannot be turned off from a fragment: `DRM_MSM` selects `DRM_CLIENT_SETUP`,
which selects it. `fbcon=map:` is the documented override and is what the
bootargs use.

**pstore attaches before the disk exists.** With a bite no longer ending the
boot, a failure should reach pstore on the `oops` partition - except
`pstore_blk_init` is a `late_initcall`, the last initcall level, and the gendisk
whose partition table holds `oops` is not guaranteed to be registered by then:
the UFS host is a platform device that may still be deferred-probing, and
`sd_probe_async` registers the disk from async work that `init/main.c` only
drains after *all* initcalls. `patches/local/manifest` therefore inserts
`async_synchronize_full()` plus a bounded five-second retry of the name
resolution into `__best_effort_init()` - the retry loop is what
`wait_for_root()` does for `root=`, and draining async work is what
`prepare_namespace()` does through `wait_for_device_probe()`. Free when the disk
is already there, and without it a panicking kernel writes no record at all.

That still leaves one trap, which is what the first dumps looked like: the
stock ROM's own kernel writes to the same partition the moment it boots, so the
record has to be read *before* Android comes back - from LOS recovery's shell,
not from Termux after a stock boot.

Nothing here is readable over USB during first stage: there is no adb before
`/init` runs, and the vendor console is a UART header. Three things had to be
true at once for the block backend to record anything, and none of them were
the first time:

- `CONFIG_PSTORE_BLK_*_SIZE` is in **KiB**, and the `4096` in
  `verify_size(name, 4096, ...)` is a byte *alignment*, not a minimum: a value
  that is not a multiple of 4 falls into `ALIGN(name, 4096)`, which is applied to
  the KiB number and then re-read as bytes. `=2` meant a 4096 **byte** zone.
- only one pstore backend can be registered, and the DRAM ring is not readable
  back on this device (stock Android has no `/dev/mem`, and its `qcom,ramoops`
  driver re-initialises the ring at probe), so the block backend is the choice;
  the `ramoops` node stays in the DTS only to keep that DRAM out of the page
  allocator for the subsystem firmware.
- `kmsg_dump()` skips a dumper when `reason > max_reason`, and a first stage
  that gives up calls `reboot(2)`, whose `kernel_restart()` dumps with
  `KMSG_DUMP_SHUTDOWN` - 4 in 7.1.2's enum, above the default of 2. So the
  record was filtered out and the partition kept the previous boot's bytes.
  `CONFIG_PSTORE_BLK_MAX_REASON=4` and `pstore_blk.best_effort=1` are what make
  an init-triggered reset leave anything behind, and `pstore.kmsg_bytes=262144`
  raises the per-record cap from its 10 KiB default to the zone size.

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


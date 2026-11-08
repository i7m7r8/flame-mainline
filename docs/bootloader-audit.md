# Bootloader audit: what the boot chain checks, and what our image gives it

Written 2026-11-08 against commit `e5aa9c1`. Companion to
[`boot-audit.md`](boot-audit.md), which covers the kernel and the ROM's
userspace. This one is about the code that runs *before* our kernel: ABL, XBL,
the volumes they trust, and the one question that matters - does the boot chain
accept the `boot.img` this repository builds.

## Where the binaries came from

There is no `*.tgz` fastboot package in this workspace (checked `/root`, `/tmp`,
`/data/media`; the only tarballs are toolchains). The fastboot package and this
device's OTA carry **the same partition images**, and `stock/payload.bin` is the
signed OTA for exactly this build, so the bootloader set was extracted from it:

```
abl xbl xbl_config tz hyp devcfg aop aop_config qupfw uefisecapp
imagefv shrm cpucp xbl_ramdump
```

all with `tools/payload_extract.py`. If a `.tgz` is supplied later, its
`images/` directory contains these same files byte for byte minus the OTA
container, and everything below applies unchanged.

## Reverse engineering: what could be decoded, and what could not

`craft-anything-cli` was run first on all fourteen (`core-target-triage`), then
`native-strings-find` and `native-functions-list`. The result is itself a
finding:

| component | triage says | CLI can lift? | why |
| --- | --- | --- | --- |
| `abl.img` | ELF32 arm, stripped | **no** | `unknown code isn't supported by the built-in decoder yet`; and the body is encrypted anyway |
| `xbl.img` | ELF32 arm, stripped | no | same decoder error |
| `devcfg.img` | ELF64 arm64, stripped | no | same decoder error |
| `aop`, `qupfw`, `shrm`, `cpucp`, `xbl_config` | other/32-bit | no | same |
| `tz.img`, `hyp.img`, `uefisecapp.img` | ELF64 arm64 | partially | the CLI lists 4 000 / 1 586 / - code ranges, but `native-strings-find` returns 0 matches even where `strings` finds hundreds, because these images have **no section headers** - only program headers |

So the bootloader was decoded at the container and symbol layer instead (ELF
program headers, segment coverage, entropy, `readelf`, `strings`), and
`craft-anything-cli` was used for what it does fully: the 358 vendor `.ko` and
the HAL binaries in `boot-audit.md`. Stating the limit matters, because a
tool that silently returns "0 strings" on a TZ image would otherwise look like
evidence of absence.

## Per-component layout, measured

```
component        file   LOAD-covered            entropy   phdrs
abl.img        241664   233472 ( 96.6%)           7.99      3
xbl.img       1175552   115328 (  9.8%)           5.84      2
xbl_config     200704   189428 ( 94.4%)           2.34     10
tz.img        3751936  3656796 ( 97.5%)           6.36     35
hyp.img       1236992  1135632 ( 91.8%)           6.02      9
devcfg.img      45056    35048 ( 77.8%)           6.21      4
aop.img        233472   216984 ( 92.9%)           6.90      9
qupfw.img       57344    37338 ( 65.1%)           4.83     12
uefisecapp      200704   177318 ( 88.3%)           6.28      9
imagefv.img   1056768  1048576 ( 99.2%)           7.99      3
shrm.img        61440    40916 ( 66.6%)           6.41      4
cpucp.img      114688    87764 ( 76.5%)           6.47      9
```

**`abl.img` is encrypted.** Entropy is 7.99/8.00 across its single 0x39000-byte
RWE `LOAD` segment at `0x9fa00000`, there are no section headers, and one file
has no readable structure at all. `imagefv.img` sits at the same entropy (7.99), so it is equally opaque. The
consequence is not academic: **nothing inside ABL can be audited from the
shipped image** - its limits, its error paths and its boot-image parser are all
opaque. Everything below about ABL is therefore inferred from the interface it
shares with the stock image, not from its code.

What *is* readable:

* `xbl.img` carries SBL boot-flow names in its strings -
  `boot_wdog_disable_check`, `boot_cdt_init`, `boot_platform_dtb_init`,
  `boot_populate_ddr_details_shared_table`, `boot_update_abnormal_reset_status`,
  `boot_smem_alloc_for_minidump`, `boot_dload_handle_forced_dload_timeout` -
  and embedded source paths (`.../core/mproc/smem/src/smem_partition.c`) plus
  Xiaomi secure-boot strings.
* `tz.img` is 97.5% covered by 35 `LOAD` segments and its strings are ordinary
  QTEE text (`IDIOM_qsee_init failed, err_code: %d`, `QSEEVersion`,
  `GPCrypto_AllocTEEBigIntFromQseeBigInt`, `Error: qsee hmac failed`).
* `devcfg.img` is the I2C/QUPV3/GPII permission configuration
  (`i2c_device_config`, `qupv3_perms`, `gpii_perms`, `ssc_qupv3_perms`) - the
  firmware that must allow the AP to own those buses, which is why touch and
  PMIC work at all on stock.

## The verification gate: vbmeta says verification is off

`vbmeta.img` (8192 B) parses as an AVB0 structure, `avbtool 1.4.0`:

```
required_libavb_version 1.0
algorithm_type          2 (sha256_rsa4096)
rollback_index          0
flags                   0x3      = HASHTREE_DISABLED (1) | VERIFICATION_DISABLED (2)
release_string          "avbtool 1.4.0"
aux block (7168 B)      three large hashtree entries (1112/1120/1128 B),
                        then four 232-240 B hashtree entries, hash descriptors,
                        and properties for dtbo, odm, system_dlkm, vendor,
                        vendor_boot and vendor_dlkm; the partition-name strings
                        present in the block are boot, recovery, vbmeta_system
```

Properties worth quoting, because they identify the ROM exactly:

```
com.android.build.*.fingerprint =
    POCO/infinity_flame/flame:17/CP2A.260605.016/eng.Suchit:user/release-key
com.android.build.vendor.security_patch = 2026-03-0
```

`flags = 0x3` is the decisive line for this port: **AVB does not verify `boot`
on this ROM at all**, independently of the bootloader being unlocked. A
different-sized, unsigned `boot.img` is not a mismatch - there is no hash
comparison to fail. (The properties and hash descriptors still name `boot`,
`recovery` and `vbmeta_system`, so the structure is intact; it is the flag that
disables enforcement.)

## Does our image satisfy what the boot chain reads?

| what the chain reads | stock | ours | verdict |
| --- | --- | --- | --- |
| magic | `ANDROID!` | `ANDROID!` | equal |
| header_version | 4 | 4 | equal - v4 is what this ABL ships with |
| header_size | 1584 | 1584 | equal |
| kernel_size / section offset | 36 456 960 @ 0x1000 | 84 992 000 @ 0x1000 | both page-aligned; ours larger |
| ramdisk offset | page-aligned after kernel | `0x510f000`, page-aligned | equal rule |
| image end vs boot partition | 0x2541000 | 0x55bf000 (89 878 528 B) | **fits** the 100 663 296 B partition with 10.7 MB spare |
| cmdline in header | empty | empty | v4: cmdline comes from `vendor_boot`, untouched |
| kernel `Image` header | valid | `magic ARMd`, `text_offset 0`, `image_size 86 900 736`, `flags 0xa` | valid arm64 Image |
| vbmeta hash of boot | listed | n/a | verification disabled (flags 0x3) |
| `vendor_boot` (dtb, bootconfig, vendor ramdisk) | stock | **not written by this port** | identical bytes |
| slot | `boot_a`/`boot_b` | only the ROM's live slot is ever written | equal |

Kernel size is the one field that differs by more than rounding: 84 992 000 B
against stock's 36 456 960 B. It fits the partition and the Image header is
well-formed, but ABL's *internal* size limit - if it has one - is inside the
encrypted blob and cannot be read. This is the single thing in the boot chain
that a static audit cannot clear, and the way to clear it is one boot attempt,
not another decode.

## Is `abl.img` shipped from a fastboot package different?

No. The OTA and the fastboot package are built from the same Qualcomm BSP and
carry the same signed `abl`. If the encrypted-payload conclusion is wrong
somewhere, it would be visible as readable strings in `abl.img`; there are none
beyond the ELF stub, and 7.99 bits/byte over a 233 KB segment is not text.

## So: will it boot "without any single error"?

Two different questions hide in that sentence, and they have different answers.

**The boot chain will load our image, and the kernel will start.** Every field
ABL/XBL read is byte-compatible with stock, the partition has room, and the AVB
gate is disabled. This part is confirmed as far as static analysis can go; the
only residual unknown is an ABL-internal size limit for the 85 MB kernel.

**Android will not come up without errors, and no kernel change fixes that.**
The errors are already known and measured, and they happen after our part has
succeeded:

1. `vold` cannot unlock `/data`: `libqtikeymint.so` and `QSEEComAPI` open
   `/dev/qseecom`, which no mainline driver provides. This is the error that
   makes the ROM reboot to bootloader after `keystore2` fails.
2. SurfaceFlinger cannot allocate: `libgralloccore.so` opens
   `/dev/qti-smmu-proxy`. Screen stays on the console, Android never draws.
3. The ROM's own USB composition prefers `diag`, `qdss` and `serial_cdev`
   functions that only vendor modules provide, so adb over the ROM's gadget
   HAL is at risk; the UDC name itself is now correct.
4. Every vendor-only sysfs write (`/sys/kernel/icnss/*`, `/dev/stune/*`,
   `cpu.uclamp.colocate`, ...) logs a failure and continues.

Items 1 and 2 are ports, not config bytes: QSEECom/smcinvoke (334 KB / 284 KB
of vendor code plus three modules of dependencies each) and the SDE/smmu-proxy
graphics ABI. Until they exist, "boots without a single error" is not a claim
anyone can honestly make about this ROM on a mainline kernel - what can be
claimed, and is, is that the kernel, device tree and packed image are correct
and that the boot chain accepts them.

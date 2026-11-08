# Bootloader audit: what the boot chain checks, and what our image gives it

Written 2026-11-08 against commit `e5aa9c1`. Companion to
[`boot-audit.md`](boot-audit.md), which covers the kernel and the ROM's
userspace. This one is about the code that runs *before* our kernel: ABL, XBL,
the volumes they trust, and the one question that matters - does the boot chain
accept the `boot.img` this repository builds.

## Where the binaries came from

The fastboot ROM is now available and was used directly:

```
/storage/emulated/0/Download/ABDM/flame_in_global_images_OS3.0.303.0.WGUINXM_20261010.0000.00_16.0_in_a8222f7a67.tgz
6 559 454 050 B   gzip -dc | wc -c = 8 866 978 840 B, exit 0
```

There is a second copy of the same name under `.../ABDM/Compressed/`, same
size, and it is **corrupt**: `gzip` stops with `invalid compressed data--format
violated` after 469 008 384 bytes, so only the first 44 members (through
`userdata.img`) are recoverable from it. Before the intact path was known, that
prefix was still useful - it holds `uefi.elf`, `tools.fv`, the GPT binaries,
all `rawprogram*.xml`/`patch*.xml`, `devcfg.mbn` and the full
`vendor_boot.img` - and a tolerant tar walker was written to get them out. The
rule that matters: **check the archive before trusting a member count from it.**

From the intact file, extracted with `tar xzf ... --wildcards '*/images/abl.elf'`
and friends:

```
abl.elf 241464   aop.mbn 232416   cpucp.elf 113632   devcfg.mbn 44904
shrm.elf 60144   tz.mbn 3748032   xbl_config.elf 196744   imagefv.elf 1056568
featenabler.mbn 101344   multi_image.mbn 32768
boot.img 100663296   dtbo.img 24117248   vendor_boot.img 100663296
```

plus, from the recovered prefix, `uefi.elf` (4 661 048 B), `tools.fv`,
`qupv3fw.elf`, `apdp.mbn`, `rtice.mbn`, `rescue.img`, `gpt_main1.bin`,
`gpt_backup5.bin`, `gpt_both1.bin`, `rawprogram{0,1,2,3,5}.xml`,
`rawprogram4_WIPE_PARTITIONS.xml`, `patch{0,1,4}.xml`, `flash_all*.sh`,
`anti_version.txt`, `crclist.txt`, and a 178 MB `vmlinux-selfbuild`.

Note `xbl.elf`, `hyp.mbn`, `xbl_ramdump.elf` and `keymaster.mbn` are not present
under those names in this ROM - `tar` reported `Not found in archive` for them.

## The partition table, measured

`SECTOR_SIZE_IN_BYTES="4096"` throughout, and the sizes are exact:

| partition | sectors | size | where |
| --- | --- | --- | --- |
| `boot_a`, `boot_b` | 24576 | **96 MiB** | LUN4 |
| `vendor_boot_a`, `vendor_boot_b` | 24576 | 96 MiB | LUN4 |
| `logfs` | 2048 | 8 MiB | LUN4 |
| `oops` | 4096 | **16 MiB** | LUN0 |
| `metadata` | 4096 | 16 MiB | LUN0 |
| `super` | 1886208 | 7.72 GB | LUN0 |
| `userdata` | rest | grows | LUN0 |

`gpt_main1.bin` is the LUN1 table and holds `xbl_a`, `xbl_config_a`,
`multiimgqti_a`, `multiimgoem_a`, `apdp`; its header sits at a 4096-byte LBA, which
is how the sector size was confirmed twice.

## The stock kernel's own configuration

`vmlinux-selfbuild` is a real, symbol-bearing build:
`Linux version 6.6.118-android15-8-ge56cf6b09cca-ab15511674-4k (kleaf@build-host)`,
ELF64 arm64, 178 114 144 B, with an uncompressed-friendly **IKCONFIG block at
0x115fea8** (`IKCFG_ST` ... `IKCFG_ED` around a gzip stream). Decompressing it
gives 211 851 bytes of `.config`, 2 252 `CONFIG_` lines - the stock kernel's
configuration for this exact phone, which had only ever been modelled from
`stock/gki.config` before.

Diffed against the config this port builds (453 options stock has `=y` that we
do not, 282 of which exist as symbols in our tree, plus 67 stock `=m`):

* the one that blocks a boot is **`CONFIG_BOOT_CONFIG`** - see below;
* `ARM64_VA_BITS`: stock **39**, ours 52 (`ARM64_LPA2=y`, `PA_BITS 52`). Not a
  blocker: `arch/arm64/mm/proc.S:508-514` and the `ARM64_HAS_VA52` boot-CPU
  feature in `cpufeature.c:3072` make the kernel fall back to 48-bit VA on
  silicon without LPA2, it does not panic. Worth noting because it is a
  deviation from the device's own configuration;
* everything else in the gap is hardening (`FORTIFY_SOURCE`,
  `HARDENED_USERCOPY`, `CFI_CLANG`, `INIT_ON_ALLOC_DEFAULT_ON`), debug
  (`DEBUG_INFO_*`, `KASAN`), power (`CPU_IDLE_GOV_TEO`, `DAMON`), input
  (`HID_*`), or module plumbing (`MODVERSIONS`) that `CONFIG_MODULES=n`
  makes moot.

### The one real boot blocker this ROM exposed: bootconfig

```
stock:  CONFIG_BOOT_CONFIG=y   CONFIG_CMDLINE="... bootconfig ..."
ours:   nothing                CONFIG_CMDLINE=""
```

`init/main.c:437` runs `setup_boot_config()` only when `bootconfig_found` (the
literal `bootconfig` token on the command line, matched at
`init/main.c:404-411`) or `CONFIG_BOOT_CONFIG_FORCE` is set, and
`lib/Makefile:267` builds `bootconfig.o` only under `CONFIG_BOOT_CONFIG`.
With neither, the blob ABL appends is parsed by nobody and `/proc/cmdline`
loses every key that travels in it - `androidboot.slot_suffix` first among
them. The ROM's fstab rows are `slotselect`, so first stage then has no suffix
to resolve `vendor`/`system`/`product` with.

Both `vendor_boot` images on this phone carry that blob: 181 B on the Infinity
OTA (`androidboot.hardware`, `memcg`, `usbcontroller`,
`load_modules_parallel`, `vendor.qspa`), 153 B on HyperOS (the same list minus
`serialconsole`). Fixed in `05a410c`: `CONFIG_BOOT_CONFIG=y` and
`CONFIG_BOOT_CONFIG_FORCE=y`, the FORCE variant chosen so the no-blob case is
an info line rather than the error a bare token would print.

## Where each ROM keeps the generic ramdisk

Not the same, and it matters when mixing images:

| ROM | `boot.img` ramdisk | `vendor_boot` ramdisk table |
| --- | --- | --- |
| HyperOS OS3.0.303.0 (this fastboot ROM) | **0** | 2 entries: platform 13 056 316 B, `_boot` 2 478 170 B |
| Infinity X Android 17 (OTA, on the phone) | 2 600 656 B | 1 entry, 16 134 011 B |

So stock HyperOS keeps the generic ramdisk in `vendor_boot`, and Infinity X
puts it back into `boot.img`. Our image follows Infinity X, which is the ROM
that is installed - and since `vendor_boot` is never written by this port, the
pairing stays consistent.

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
| image end vs boot partition | 0x2541000 (Infinity), kernel-only 96 MiB image (HyperOS) | 0x55bf000 (89 878 528 B) | **fits** the 96 MiB partition the flash map declares (24576 x 4096), 10.7 MB spare |
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

## The packed payload, verified at the byte level

`boot.img`'s kernel section is not the Image - it is `payload.bin`, and the
check that matters is whether the bootloader's jump into it lands somewhere
valid. It does, and the whole chain was re-derived here:

```
payload.bin 0x00000000  4d 5a 40 fa  -> code0 = 0xfa405a4d = EFI_SIG_NOP ("MZ")
payload.bin 0x00000004  ff 33 44 15  -> code1 = 0x154433ff = b 0x510d000
                                       (imm26 0x14433ff; 4 + 4*imm = shim_off)
```

so the loader's jump to the Image start executes the PE "MZ" nop and falls
into the branch to the shim at `0x510d000`. Disassembling the 788 bytes there
confirms what it does before it can fail: `mov x4,#0x17410000; str w5,[x4,#4];
str wzr,[x4,#8]` stops the bootloader watchdog, it reads its header from
`shim_off-0x1000`, checks the `SWEETLNX` magic and version 2, bounds-checks
`image_off`/`dtb_off` against 0x20000000/0x1000000, copies the bootloader's
tree into the handover region, and jumps to `entry_off` with `x0 = dtb_load`.

Rebuilding the payload locally from the artifact's own `Image` and dtb with
`tools/mklinuxpayload.py` (and the shim assembled by `aarch64-linux-gnu-as`)
produces a **byte-identical** file:

```
af68b5aeeb6c623191e14327f17514408f08db93e8c77a3e72d42fcb18542ac9  payload.bin (artifact)
af68b5aeeb6c623191e14327f17514408f08db93e8c77a3e72d42fcb18542ac9  payload.bin (local rebuild)
```

which is the strongest statement available without the phone: the bytes the
boot chain will execute are exactly the bytes this repository's tools produce,
and their entry path is not a guess.

## Two ABLs, both encrypted

The question "is the fastboot ROM's loader any more readable than the OTA's?"
is answered by measurement, not by hope:

| file | size | LOAD segment | entropy over it | sha256 |
| --- | --- | --- | --- | --- |
| OTA `abl.img` | 241 664 | 0x39000 @ 0x9fa00000 | **7.99** | `20e453c3ae…` |
| fastboot `abl.elf` | 241 464 | 0x39000 @ 0x9fa00000 | **8.00** | `ef0673ba30…` |

Different builds, identical structure, and 8.00 bits per byte over the only
loadable segment: the payload is encrypted in both. Every other component in
the ROM is ordinary code - `aop.mbn` 5.36, `tz.mbn` 4.84, `devcfg.mbn` 6.25,
`imagefv.elf` 4.51, `xbl_config.elf` 3.25 - so it is ABL specifically, not the
packaging, that is sealed.

## The artifact is not bit-reproducible, and now it says why

Building the same commit twice does not produce the same `Image`. Two runs
were compared directly:

```
88b157c (run 42450676011)          50045bf (run 42454799295)
Image 84 916 736 B                 Image 84 916 736 B
                                   differing bytes: 9 396 898, first at 0x2afd8d
flame.config   identical
sm4450-xiaomi-flame.dtb  identical (3e140ca0...)
```

The cause is `ROCKNIX_REF`, which defaults to the moving branch `next`: each
run clones whatever the SM4450 patch series is that day, so the Image differs
even when nothing in this repository changed. The kernel tarball is pinned by
`KERNEL_VERSION` and cached, and the local patches live in the repository, so
the Rocknix series is the only moving input.

Since `fb57583` the workflow writes the resolved commit to `rocknix-ref.txt`
and puts it at the top of `patch-report.txt`, which ships inside the artifact -
so an Image can always be traced back to the patch state that produced it.
Pinning the default to a commit would make builds bit-reproducible; that is a
policy decision (it freezes upstream fixes), not something this audit should
change silently.

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

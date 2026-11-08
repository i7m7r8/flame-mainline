# Firmware formats on this phone, and what can read them

Written 2026-11-08 from the fastboot ROM
(`flame_in_global_images_OS3.0.303.0.WGUINXM`), the OTA `payload.bin`, and the
partition images extracted from it. Every row was classified by reading the
file's own magic, not by trusting its extension.

Two things are recorded per format: **what it is**, and **which reader
actually decoded it**. `craft-anything-cli` is the tool of record where it can
open the format; where it cannot, the row says so and names the parser that
did, because "the tool printed nothing" is not evidence of an empty file.

## The catalog

| format | magic | example on this phone | reader | result |
| --- | --- | --- | --- | --- |
| ELF program | `\x7fELF` | `abl.elf`, `xbl.mbn`, `tz.mbn`, `hyp.mbn`, `devcfg.mbn`, `aop.mbn`, `uefisecapp.mbn`, `imagefv.elf`, `cpucp.elf`, `shrm.elf`, `qupfw.img`, `qupv3fw.elf`, `rtice.mbn`, `vmlinux-selfbuild` | craft-anything-cli `core-target-triage` | arch/kind/stripped per file, 20 of them; code lifting only where the arch decoder exists |
| ELF-wrapped MBN | `\x7fELF`, no sections | `adsp.mdt` + `adsp.b00..b41`, `mfido.mdt`, `mkeybag.mdt`, `modem.mdt` | craft-anything-cli triage + `readelf -l` | 42 program headers: hash/header segment at 0, then code segments with TZ VAddr and DSP PAddr |
| Android boot image | `ANDROID!` | `boot.img` (stock HyperOS, stock Infinity, ours), `recovery.img` | header parse + parity diff | v4 everywhere, header_size 1584; stock ramdisk 0 (HyperOS) / 2 600 656 (Infinity) / 4 912 902 (ours) |
| Android vendor_boot | `VNDRBOOT` | `vendor_boot.img` | header parse | v4, page 4096, 1-2 ramdisk table entries, dtb 3 860 431 B, bootconfig 153-181 B |
| Android DTBO | `\xd7\xb7\xab\x1e` | `dtbo.img` | `tools/dtb_split.py` | 4 Flame QRD overlays, 328-341 KB each |
| Flattened device tree | `\xd0\x0d\xfe\xed` | vendor base DTBs in vendor_boot, our `sm4450-xiaomi-flame.dtb` | `dtc` | vendor tree 15 773 lines, 235 compatibles; ours 145 |
| AVB vbmeta | `AVB0` | `vbmeta.img`, `vbmeta_system.img` | descriptor walk (size/checksum order matters) | `flags 0x3` = hashtree+verification disabled, avbtool 1.4.0, fingerprint in properties |
| GPT | `EFI PART` at LBA1 | `gpt_main1.bin`, `gpt_backup5.bin`, `gpt_both1.bin` | structural parse | 4096-byte sectors; LUN1 holds xbl_a/xbl_config_a/multiimgqti_a/multiimgoem_a/apdp |
| FAT16 | `MSDOS5.0` + `FAT16` | `modem.img`, `BTFM.bin` | FAT walker (LFN aware) | `image/` with 339 entries: adsp/wps/TA firmware, `adspua.jsn`, `cladence/qdsp6m.qdb` |
| ext4 | `\x53\xef` at 0x438 | `vendor.img`, `system.img`, `odm.img`, `vendor_dlkm.img`, `dsp.img` | `debugfs` | 96 rc files, 258 modules, 52 DSP audio modules, HAL binaries |
| cpio newc | `070701` | `vendor_ramdisk.cpio` | `tools/`-local extractor (no `cpio` in this image) | 318 members, 288 `.ko` |
| LZ4 legacy | `\x02\x21\x4c\x18` | `ramdisk.img`, `vendor_ramdisk.bin` | block decoder (not needed after the cpio was found) | first-stage ramdisk / vendor ramdisk, compressed |
| Android sparse | `\x3a\xff\x26\xed` | `rescue.img`, several OTA images | header parse | 4096-byte blocks, 32768 blocks, 4 chunks |
| OTA payload | `CrAU` | `stock/payload.bin` | `tools/payload_extract.py` | v2 manifest, 161 258 B, 34 partitions |
| EFI firmware volume | zero vector + FS GUID + `_FVH` | `tools.fv`, `uefi.elf` body | FV walker | tools FV; `uefi.elf` FV header at 0x1000, payload encrypted above 0x50000 |
| IKCONFIG | `IKCFG_ST` | `vmlinux-selfbuild` | gzip of the embedded blob | 211 851 B of `.config`, 2 252 options, stock kernel 6.6.118 |
| Flash map XML | `<?xml` | `rawprogram{0..5}.xml`, `patch{0,1,4}.xml` | text parse | sector size 4096, boot_a/b 24576 sectors, oops 4096, logfs 2048 |
| JSON | `{` | `adspr.jsn`, `adsps.jsn`, `adspua.jsn` | text | ADSP sensor-PD configuration handed to the DSP |
| Raw text | - | `flash_all*.sh`, `build.prop`, `crclist.txt`, `anti_version.txt` | read | flash procedure, anti-rollback version 1, CRC list |

## What craft-anything-cli can and cannot open

Measured on these files, not assumed:

* **Opens and describes**: every ELF and PE it was given -
  `abl.elf`, `xbl.mbn`, `tz.mbn`, `hyp.mbn`, `devcfg.mbn`, `aop.mbn`,
  `uefisecapp.mbn`, `imagefv.elf`, `cpucp.elf`, `shrm.elf`, `qupfw.img`,
  `qupv3fw.elf`, `rtice.mbn`, `vmlinux-selfbuild`, the DSP `.mdt` segments.
  It reports arch, bitness, kind and whether names were stripped, with an
  evidence id per answer.
* **Opens but cannot lift**: the 32-bit ARM and "other" images. The decoder
  answers `unknown code isn't supported by the built-in decoder yet`, so no
  pseudocode exists for `abl.elf`, `xbl.mbn`, `aop.mbn`, `cpucp.elf`,
  `qupfw.img`, `shrm.elf` or the 32-bit DSP segments. For `tz.mbn`, `hyp.mbn`
  and `uefisecapp.mbn` it lists code ranges but its string search returns 0
  matches where `strings` finds hundreds, because those images carry program
  headers and no section headers.
* **Does not open at all**: `ANDROID!` boot images, `VNDRBOOT`, DTBO, FDT,
  `AVB0`, GPT, FAT16, ext4, cpio, LZ4, sparse, `CrAU`, EFI FVs, IKCONFIG.
  For these the parser column above names the reader that was used.

The practical rule this leaves: use `craft-anything-cli` for anything that is
an ELF/PE, `readelf`/`objdump` for the relocation and section layers inside
them, and a purpose-written decoder for the containers - a container is not
"empty" because a program-code tool cannot see inside it.

## Architecture of each image, and how far the lifter gets

Asked of every ELF image with `core-target-triage` and
`native-functions-list`; the errors are the tool's own words.

| image | what runs it | lifter result |
| --- | --- | --- |
| `tz.mbn` | Cortex-A (EL3 secure world) | 5 000+ code ranges (limit hit) |
| `hyp.mbn` | Cortex-A EL2 | 1 586 ranges |
| `xbl_ramdump.img` | Cortex-A | 2 316 ranges |
| `rtice.mbn` | Cortex-A64 | 635 ranges |
| `uefisecapp.img` | Cortex-A64 (UEFI app) | 423 ranges |
| `vmlinux-selfbuild` | Cortex-A64 | 5 000+ ranges (limit hit) |
| `uefi.elf` | Cortex-A64 UEFI | 11 ranges, body encrypted above 0x50000 |
| `abl.img`, `aop.img` | Cortex-A32 | 1 range each; body encrypted (entropy 8.00 / 6.90) |
| `cpucp.img`, `shrm.img` | **RISC-V 32** | `riscv32 code isn't supported by the built-in decoder yet` |
| `qupfw.img`, `qupv3fw.elf` | **Hexagon** | `hexagon code isn't supported by the built-in decoder yet` |
| `xbl.img`, `xbl_config.img` | MBN-wrapped | `unknown code isn't supported by the built-in decoder yet` |
| `devcfg.img`/`.mbn`, `imagefv.img`, `aop_config.img` | Cortex-A32 | 0 ranges (no section headers, decoder declines) |

Two of those are worth keeping: the QuP firmware really is Hexagon code and
CPUCP/SHRM really are RISC-V - the tool identifies both and stops, which is
more useful than a silent zero. And on `tz.img` the same tool reports 0 string
matches for `qsee` while `strings` finds 618, so its string search is only as
good as its container support: for section-less Qualcomm images it is not a
reader at all.

## The three formats that actually gate the boot

1. **Android boot v4** - the bootloader reads `ANDROID!`, `header_version`,
   `kernel_size`, `ramdisk_size`, `header_size` and an empty cmdline. Ours
   matches stock field for field and ends at 0x55bf000 of the 0x6000000
   partition.
2. **vendor_boot v4** - never written by this port. It carries the dtb, the
   ramdisk table and the bootconfig blob; the blob is why `CONFIG_BOOT_CONFIG`
   had to be turned on (it is the transport for `androidboot.slot_suffix`).
3. **The ELF kernel** - the bootloader jumps to its first instruction, and the
   first two words were checked byte for byte: `0xfa405a4d` (the EFI "MZ" nop)
   then `0x154433ff`, a `b` to `shim_off` = 0x510d000.

Everything else in the catalog is either verified for information (GPT,
vbmeta, rawprogram) or out of scope for booting this image (system/vendor
contents, DSP firmware payloads).

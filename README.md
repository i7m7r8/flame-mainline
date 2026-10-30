# flame-mainline

Mainline Linux for the Xiaomi Redmi 14C 5G / POCO M7 5G (`flame`, Qualcomm SM4450
"ravelin"), built on GitHub Actions.

Based on the proven bring-up by [orange200213/sky-linux](https://github.com/orange200213/sky-linux)
(Redmi 12 5G, same SoC) and the ROCKNIX SM4450 patch series.

- Base: **Linux 7.2.13** (newest stable)
- Patches: ROCKNIX `projects/ROCKNIX/devices/SM4450/patches/linux/*` + sky-linux patches
- Board DT: `dts/sm4450-xiaomi-flame.dts`
- Boot: sky-style shim in `boot_a` (UEFI boot chain picks the vendor DTB from
  `vendor_boot`, so a mainline DTB must be supplied by the shim)

## Status

- M0 console: build pipeline up; hardware values provisional
- M1 USB serial: initramfs sets up a CDC-ACM gadget and a root shell on `ttyGS0`
- M2+ (UFS, display `c3f` panel, focaltech touch, PM7250B charger, WCN3990): TODO

## Confirmed device facts

Read back from the running Android 17 ROM (`getprop`/`dumpsys`):

- SKU `c3fin`, hwversion `1.19.0`, 4 GB RAM, dtb_idx 5 (Android slots, `_a` active)
- SoC SM4450 ravelin, 4K pages
- UFS `soc/1d84000.ufshc`, USB `a600000.dwc3`
- Wi-Fi/BT combo: WCN3990-class (vendor DT `qcom,icnss@22800000` + WPSS
  firmware; BT firmware "cherokee"); not WCN6750
- Cameras: truly sc5kjns + sc820cs; side fingerprint: goodix
- GPU Adreno 613, 340-955 MHz
- Panel: 720x1640 "c3f"-class (vendor DT splash `0xb8000000 + 0x2b00000`)

## Flash (boot_a only, reversible)

The image never touches `boot_b`, `vbmeta`, `dtbo`, `vendor_boot` or any
bootloader partition, so it cannot brick the device. Before flashing, back up
the stock boot images (root shell in Android):

```sh
dd if=/dev/block/by-name/boot_a of=/sdcard/boot_a-stock.img
dd if=/dev/block/by-name/boot_b of=/sdcard/boot_b-stock.img
```

Try a no-write boot first (works on some bootloaders only):

```sh
fastboot boot out/flame-shim-boot-v4.img
```

Otherwise flash slot a only:

```sh
fastboot getvar current-slot        # must print a
fastboot flash boot_a out/flame-shim-boot-v4.img
fastboot reboot
```

Watch for a `ttyACM` device on the host, then `picocom -b 115200 /dev/ttyACM0`.

Restore Android at any time from fastboot (the bootloader is never touched):

```sh
fastboot flash boot_a boot_a-stock.img
```

While the shim is in `boot_a`, recovery is unreachable (recovery boots the
kernel from the active slot), so restore via fastboot, not via recovery.
Confirm `fastboot devices` works on your PC before flashing.

No auto-reboot on panic is configured (`panic` unset = hang), so a failed
boot will not loop. Keep slot b on the stock ROM: it is the automatic
fallback if slot a keeps failing.

## Provisional values (verify from the device)

- `/memory@80000000`: `0x80000000 + 0x68e00000` (assumed same as sky 4 GB)
- `simple-framebuffer`: 720x1640, stride 2880, region `0xb8000000 + 0x2b00000`
  (region confirmed from flame vendor DTB; stride provisional)

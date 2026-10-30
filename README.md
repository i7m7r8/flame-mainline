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
- M2 rootfs: Debian 12 arm64 on microSD (`LABEL=flame-root`, `sdhc_2`);
  initramfs finds it, moves the console mounts and `switch_root`s into it.
  Image ships on the artifact with weston + Waydroid preinstalled.
- M3+ (display `c3f` panel, focaltech touch, PM7250B charger, WCN3990, GPU): TODO

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

## Dual boot Android 17 + Debian (A/B slots)

The device is A/B: `boot_a` holds the shim (Linux), `boot_b` keeps Android.
The slot is switched from fastboot, nothing else is rewritten:

```sh
fastboot getvar current-slot     # shows which side is active
fastboot --set-active=a          # boot Linux (shim in boot_a)
fastboot --set-active=b          # boot Android (boot_b untouched)
```

Make sure `boot_b` still holds a matching Android 17 boot image before
relying on it (back it up or re-flash the ROM's `boot.img` to `boot_b`).
Some Qualcomm ABLs re-try the other slot after repeated failed boots; if that
happens, `fastboot --set-active=a` puts Linux back.

### Debian rootfs on microSD

1. Download `flame-debian-rootfs.img.xz` from the build artifact and write it
   to the card (balenaEtcher, Rufus, or `xz -dc ... | dd of=/dev/sdX bs=4M`).
2. Flash the shim: `fastboot flash boot_a flame-shim-boot-v4.img`.
3. Insert the card and boot. The initramfs prints `found flame-root on ...`
   then `switching to rootfs ...`; USB serial autologins as `root`
   (password `flame`, not needed on the serial console).

Without a card the shim still drops to the M1 shell, so a broken card cannot
brick the boot.

### Waydroid

The kernel is built with binder/binderfs, overlayfs, cgroup v2 controllers and
netfilter (`config/waydroid.config`); Waydroid's Debian packages are installed
in the rootfs. After the card boots:

```sh
waydroid init          # downloads the Android system image (needs network)
weston &               # or any other Wayland compositor
waydroid session start
```

Rendering is the real constraint: until the c3f panel and the Adreno 613 work
(M3/M4), Waydroid can only render through simpledrm with software GL, which is
slow. Calls/camera inside Waydroid (vendor HALs) are not supported by its
generic image.

## Provisional values (verify from the device)

- `/memory@80000000`: `0x80000000 + 0x68e00000` (assumed same as sky 4 GB)
- `simple-framebuffer`: 720x1640, stride 2880, region `0xb8000000 + 0x2b00000`
  (region confirmed from flame vendor DTB; stride provisional)

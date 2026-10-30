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
- M2+ (UFS, display `c3f` panel, focaltech touch, PM7250B charger, WCN6750): TODO

## Flash (boot_a only)

Recovery: flash your stock `boot.img` back to `boot_a`.

```sh
fastboot flash boot_a out/flame-shim-boot-v4.img
fastboot reboot
```

Watch for a `ttyACM` device on the host, then `picocom -b 115200 /dev/ttyACM0`.

## Provisional values (verify from the device)

- `/memory@80000000`: `0x80000000 + 0x68e00000` (assumed same as sky 4 GB)
- `simple-framebuffer`: 720x1640, stride 2880, region `0xb8000000 + 0x2b00000`
  (region confirmed from flame vendor DTB; stride provisional)

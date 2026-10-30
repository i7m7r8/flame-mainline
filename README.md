# flame-debian

Debian 13 (trixie) arm64 on the Xiaomi Redmi 14C 5G / POCO M7 5G (`flame`,
Qualcomm SM4450 "ravelin"), dual booted against Android 17.

The kernel is the **stock vendor GKI 6.6.82** from the Infinity X Android 17 ROM
(`6.6.82-android15-8-gd33b207314e6-ab13612522-4k`). It already drives every
piece of hardware (c3f panel, focaltech touch, Adreno 613, WCN3990, modem, SD),
so nothing has to be ported. A small initramfs replaces the Android ramdisk:
it loads the vendor modules from the ROM's own `vendor_boot`, finds the Debian
rootfs on microSD (`LABEL=flame-root`) and `switch_root`s into it.

## Layout and safety

| slot | contents |
|------|----------|
| `boot_a` / `vendor_boot_a` / `dtbo_a` | **untouched** — working Android 17 |
| `boot_b` | Debian: stock kernel + flame initramfs |
| `vendor_boot_b` / `dtbo_b` | stock copies from the same ROM |
| `recovery`, `vbmeta*`, bootloader partitions | **never touched** |

- Only `boot_b`, `vendor_boot_b` and `dtbo_b` are written.
- Android stays bootable in slot `a`; `fastboot --set-active=a` returns to it
  at any time, no reflash required.
- The stock `boot.img`, `vendor_boot.img`, `dtbo.img` and `recovery.img` from
  the ROM are kept next to the flash images, so slot `b` and recovery can be
  restored byte for byte.
- A failed Debian boot just hangs in the initramfs rescue shell (Power+VolDown
  gets back to fastboot); the bootloader is never modified.

## Images

- `flame-debian-boot_b.img` — boot v4, stock kernel, flame initramfs
  (`tools/repack_bootimg.py`)
- `flame-debian-rootfs.img.xz` — Debian 13 arm64 ext4, built in CI
  (`scripts/build-debian-rootfs.sh`). Mobian-style mobile stack from Debian's
  own packages: `phosh` + `phoc` (compositor), `squeekboard` on-screen
  keyboard, `osmo`, `gnome-calls` (dialer) + `callaudiod`, `gnome-contacts`,
  `feedbackd`, `phosh-mobile-settings`, pipewire,
  NetworkManager, nautilus/epiphany, plus `weston` and `waydroid`.
  root password `flame`
  (the dialer is `gnome-calls`; Debian trixie has no package named `calls`)
- stock `boot.img`, `vendor_boot.img`, `dtbo.img`, `recovery.img`

## Flash

Write the rootfs to a microSD card first:

```sh
xz -dc flame-debian-rootfs.img.xz | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
```

Then, while the phone boots Android (slot `a`, `fastboot devices` works):

```sh
fastboot getvar current-slot          # must print: a
fastboot flash boot_b        flame-debian-boot_b.img
fastboot flash vendor_boot_b vendor_boot.img
fastboot flash dtbo_b        dtbo.img
fastboot --set-active=b
fastboot reboot
```

The USB port becomes a CDC-ACM serial console (`ttyACM0` on Linux, a COM port
on Windows); log in as `root` / `flame`. This is the only console: the GKI
kernel has no VT and no fbcon.

Back to Android:

```sh
fastboot --set-active=a
fastboot reboot
```

Recovery (own 100 MB partition, never written):

```sh
fastboot reboot recovery              # or Power+VolUp
```

Restore slot `b` to stock at any time:

```sh
fastboot flash boot_b        boot.img
fastboot flash vendor_boot_b vendor_boot.img
fastboot flash dtbo_b        dtbo.img
```

## What the initramfs does

Because the stock GKI config has `CONFIG_DEVTMPFS=n` and `CONFIG_VT=n`, the
init (`initramfs-vendor/init`) runs its own `tmpfs /dev` + `mdev`, loads the
modules listed in `vendor_boot`'s `/lib/modules/modules.load`, `modprobe`s
`dwc3-msm` (USB), binds a configfs ACM gadget, scans `/dev/mmcblk*` and
`/dev/sd*` for the ext4 label `flame-root`, copies `/lib/modules` into the new
root (so Debian can later `modprobe` display/touch/wifi drivers), moves
`/dev /proc /sys /config /tmp` and `exec switch_root /sbin/init`.
Without a card it drops to a rescue shell on `ttyGS0` instead of hanging.

Stock ramdisks concatenate as LZ4 legacy frames, so the initramfs is packed
the same way (`tools/mklz4legacy.py`).

## Status

- Boot path: kernel + initramfs + switch_root — built, needs on-device run
- Debian 13 rootfs: CI build (debootstrap + qemu-user), validated read-only
  through `debugfs` (runners do not allow loop mounts)
- Mobile UI: `phosh.service` starts `phoc -S -E /usr/libexec/phosh` directly,
  because this kernel has no VT so gdm/gnome-session and the packaged
  `phosh.service` (both need `/dev/tty0` + a VT) cannot be used. It renders
  through `msm_drm` once the panel driver binds; software pixels
  (`WLR_RENDERER=pixman`, `GALLIUM_DRIVER=llvmpipe`) until the Adreno 613 works
- Display: `msm_drm.ko` and `focaltech_spi.ko` ship in the vendor ramdisk and
  are copied into the rootfs; `/etc/modules-load.d/flame.conf` loads panel,
  touch, vibrator and battery drivers at boot
- Waydroid: package installed, but the stock GKI lacks
  `CONFIG_PID_NS`/`CONFIG_USER_NS`, which LXC (and therefore Waydroid) needs.
  A custom GKI build of 6.6 with those namespaces enabled and the same KMI
  would unblock it; do not expect the stock kernel to run containers.
- Mainline 7.x work that preceded this route is kept in `linux/`, `dts/`,
  `config/` and the older commits; it is not part of the image anymore.

## Rebuilding the boot image

The stock kernel images are not committed (they are ~100 MB ROM extracts).
Place them in `stock/`:

```
stock/boot.img          # boot_a dump / payload boot.img
stock/vendor_boot.img   # payload vendor_boot.img
stock/payload.bin       # optional: extracts dtbo/recovery
```

```sh
python3 tools/mkinitramfs.py out/tree out/ramdisk.img --lz4legacy
python3 tools/repack_bootimg.py stock/boot.img out/ramdisk.img \
        out/flame-debian-boot_b.img --cmdline 'console=ttyGS0'
python3 tools/payload_extract.py stock/payload.bin dtbo out/dtbo.img
```

`out/tree` is `/init` (`initramfs-vendor/init`) plus a static aarch64
`busybox` from Debian's `busybox-static` package.

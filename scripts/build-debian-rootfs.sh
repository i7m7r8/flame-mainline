#!/bin/bash
# Build a Debian bookworm arm64 rootfs image (LABEL=flame-root) for the
# Xiaomi Redmi 14C 5G (flame), with Waydroid + Weston preinstalled.
#
# Runs on an x86_64 host: debootstrap --foreign plus qemu-user-static binfmt
# execute the arm64 chroot (second stage, apt, waydroid package).
#
#   sudo bash scripts/build-debian-rootfs.sh out/flame-debian-rootfs.img
#
# Output: the ext4 image, plus .xz when the environment sets XZ=1 (CI does).
set -euo pipefail

OUT=${1:-out/flame-debian-rootfs.img}
SUITE=${SUITE:-bookworm}
MIRROR=${MIRROR:-http://deb.debian.org/debian}
DIR=$(mktemp -d /var/tmp/flame-debroot.XXXXXX)
XZ=${XZ:-1}

log() { echo "== $*"; }

umount_all() {
	set +e
	for m in dev/pts dev sys proc; do
		mountpoint -q "$DIR/$m" && umount "$DIR/$m"
	done
}

cleanup() {
	umount_all
	rm -rf "$DIR"
}
trap cleanup EXIT

log "debootstrap $SUITE arm64 -> $DIR"
debootstrap --arch=arm64 --foreign --variant=minbase "$SUITE" "$DIR" "$MIRROR"

install -m 0755 /usr/bin/qemu-aarch64-static "$DIR/usr/bin/"

log "second stage under qemu-user"
chroot "$DIR" /debootstrap/debootstrap --second-stage

cp /etc/resolv.conf "$DIR/etc/resolv.conf" 2>/dev/null || true

cat > "$DIR/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main
deb $MIRROR $SUITE-updates main
deb http://deb.debian.org/debian-security $SUITE-security main
EOF

# keep maintainer scripts from starting services inside the chroot
printf '#!/bin/sh\nexit 101\n' > "$DIR/usr/sbin/policy-rc.d"
chmod 0755 "$DIR/usr/sbin/policy-rc.d"

for m in proc sys dev; do
	mount --bind "/$m" "$DIR/$m"
done

export DEBIAN_FRONTEND=noninteractive
log "base packages"
chroot "$DIR" /bin/bash -c '
	set -e
	apt-get update -qq
	apt-get install -y -qq --no-install-recommends \
		systemd-sysv udev dbus dbus-user-session \
		iproute2 iputils-ping ca-certificates curl wget gnupg \
		openssh-server sudo nano less htop \
		weston wayland-utils libgl1-mesa-dri libgles2 mesa-utils \
		lxc python3
'

log "waydroid package"
chroot "$DIR" /bin/bash -c '
	if curl -fsSL --max-time 60 https://repo.waydro.id/waydroid.gpg -o /tmp/waydroid.gpg; then
		gpg --dearmor < /tmp/waydroid.gpg > /usr/share/keyrings/waydroid.gpg
		echo "deb [signed-by=/usr/share/keyrings/waydroid.gpg] https://repo.waydro.id/ bookworm main" > /etc/apt/sources.list.d/waydroid.list
	else
		echo "WARN: waydroid key fetch failed, using trusted=yes" >&2
		echo "deb [trusted=yes] https://repo.waydro.id/ bookworm main" > /etc/apt/sources.list.d/waydroid.list
	fi
	apt-get update -qq || true
	if apt-get install -y -qq --no-install-recommends waydroid; then
		echo "waydroid: installed $(dpkg-query -W -f=\${Version} waydroid)" >> /root/setup-status.txt
	else
		echo "waydroid: package install failed" >> /root/setup-status.txt
	fi
'

log "flame configuration"
echo flame > "$DIR/etc/hostname"
grep -q "127.0.1.1" "$DIR/etc/hosts" || echo "127.0.1.1 flame" >> "$DIR/etc/hosts"

cat > "$DIR/etc/fstab" <<'EOF'
proc     /proc  proc     defaults              0 0
sysfs    /sys   sysfs    defaults              0 0
devtmpfs /dev   devtmpfs mode=0755,nosuid      0 0
tmpfs    /tmp   tmpfs    mode=1777,nosuid,nodev 0 0
EOF

# root shell over USB without a login prompt (the gadget is bound in initramfs)
mkdir -p "$DIR/etc/systemd/system/serial-getty@ttyGS0.service.d" \
         "$DIR/etc/systemd/system/getty@tty1.service.d"
for u in serial-getty@ttyGS0 getty@tty1; do
	cat > "$DIR/etc/systemd/system/$u.service.d/autologin.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM
EOF
done

mkdir -p "$DIR/etc/systemd/system/getty.target.wants" \
         "$DIR/etc/systemd/system/multi-user.target.wants"
ln -sf /usr/lib/systemd/system/serial-getty@.service \
	"$DIR/etc/systemd/system/getty.target.wants/serial-getty@ttyGS0.service"
ln -sf /usr/lib/systemd/system/getty@.service \
	"$DIR/etc/systemd/system/getty.target.wants/getty@tty1.service"
ln -sf /usr/lib/systemd/system/ssh.service \
	"$DIR/etc/systemd/system/multi-user.target.wants/ssh.service"
if [ -e "$DIR/usr/lib/systemd/system/waydroid-container.service" ]; then
	ln -sf /usr/lib/systemd/system/waydroid-container.service \
		"$DIR/etc/systemd/system/multi-user.target.wants/waydroid-container.service"
fi

echo 'root:flame' | chroot "$DIR" chpasswd

cat > "$DIR/etc/motd" <<'EOF'
flame mainline - Debian bookworm (arm64)
  root password: flame
  Waydroid: waydroid init   (needs network + a Wayland compositor, e.g. weston)
EOF
printf 'flame-mainline Debian rootfs for Redmi 14C 5G (flame)\n' > "$DIR/etc/flame-image"

# the shim kexecs/loads this image without needing modules on disk
rm -rf "$DIR/debootstrap"
rm -f "$DIR/usr/bin/qemu-aarch64-static"
rm -rf "$DIR"/var/lib/apt/lists/* "$DIR"/var/cache/apt/archives/*.deb

umount_all

USED=$(du -sm "$DIR" | cut -f1)
SIZE=$(( USED * 130 / 100 + 512 ))
[ "$SIZE" -lt 2048 ] && SIZE=2048

log "ext4 image ${SIZE}M (${USED}M used)"
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
truncate -s "${SIZE}M" "$OUT"
mke2fs -q -t ext4 -L flame-root -m 1 -d "$DIR" "$OUT"
e2fsck -fn "$OUT" >/dev/null

if [ "$XZ" = 1 ]; then
	log "compressing"
	xz -T0 -4 -f "$OUT"
	ls -lh "$OUT.xz"
else
	ls -lh "$OUT"
fi

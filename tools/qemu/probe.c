/*
 * First-stage probe, run as PID 1 under QEMU.
 *
 * It prints what Android's init would compute from the live kernel. AOSP's
 * DeviceHandler takes a block device's sysfs path, strips "/devices/platform/"
 * and matches what is left (up to the "block" component) against
 * androidboot.boot_devices; the same string is the directory init symlinks
 * under /dev/block/platform/. Printing it for our two dtb variants settles
 * whether the soc node's unit address can ever match.
 *
 * Static, and no shell: the ROM's ramdisk carries no sh (toolbox_ramdisk
 * answers "no such tool"), so this is the harness's rdinit.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/klog.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <unistd.h>

static void derive(const char *buf)
{
	const char *mark = "/devices/platform/";
	const char *p = strstr(buf, mark);
	const char *cut;
	size_t rest_len;

	if (!p) {
		printf("    derived:   not under /devices/platform/, init ignores it\n");
		return;
	}
	p += strlen(mark);
	cut = strstr(p, "/block/");
	rest_len = cut ? (size_t)(cut - p) : strlen(p);
	printf("    derived:   \"%.*s\"  <- what boot_devices must equal\n",
	       (int)rest_len, p);
}

static void show_realpath(const char *link)
{
	char buf[PATH_MAX];
	ssize_t n = readlink(link, buf, sizeof(buf) - 1);

	if (n <= 0)
		return;
	buf[n] = '\0';
	printf("    sysfs:     /sys%s\n", buf);
	derive(buf);
}

static void dump_uevent(const char *dev)
{
	char path[PATH_MAX];
	FILE *f;
	const char *want[] = { "PARTNAME", "DEVTYPE", "PARTNO", "MAJOR", "MINOR" };

	snprintf(path, sizeof(path), "/sys/class/block/%s/uevent", dev);
	f = fopen(path, "r");
	if (!f)
		return;
	printf("    uevent:    ");
	while (fgets(path, sizeof(path), f)) {
		size_t i;
		for (i = 0; i < sizeof(want) / sizeof(*want); i++) {
			size_t l = strlen(want[i]);
			if (!strncmp(path, want[i], l) && path[l] == '=')
				printf("%s", path);
		}
	}
	printf("\n");
	fclose(f);
}

static void cat_file(const char *path)
{
	FILE *f = fopen(path, "r");
	char line[512];

	if (!f) {
		printf("    (no %s: %s)\n", path, strerror(errno));
		return;
	}
	while (fgets(line, sizeof(line), f)) {
		line[strcspn(line, "\n")] = '\0';
		printf("    %s: %s\n", strrchr(path, '/') + 1, line);
	}
	fclose(f);
}

static void list_dir(const char *path, const char *title)
{
	DIR *d;
	struct dirent *e;

	printf("===== %s =====\n", title);
	d = opendir(path);
	if (!d) {
		printf("  (cannot open %s)\n", path);
		return;
	}
	while ((e = readdir(d)))
		if (e->d_name[0] != '.')
			printf("  %s\n", e->d_name);
	closedir(d);
}

static void list_soc_children(void)
{
	DIR *d = opendir("/sys/devices/platform");
	struct dirent *e;

	if (!d)
		return;
	while ((e = readdir(d))) {
		char path[PATH_MAX];

		if (strncmp(e->d_name, "soc", 3))
			continue;
		snprintf(path, sizeof(path), "/sys/devices/platform/%s", e->d_name);
		list_dir(path, "P2b children of the soc platform device");
	}
	closedir(d);
}

static void dump_kmsg(void)
{
	int len = klogctl(10, NULL, 0);	/* SYSLOG_ACTION_SIZE_BUFFER */
	char *buf;
	char *line;
	size_t off = 0;

	if (len <= 0)
		len = 1 << 20;
	buf = malloc(len + 1);
	if (!buf)
		return;
	len = klogctl(3, buf, len);	/* SYSLOG_ACTION_READ_ALL */
	if (len <= 0) {
		free(buf);
		return;
	}
	buf[len] = '\0';
	printf("  --- kernel log lines mentioning virtio/mmio/region ---\n");
	for (line = buf; off < (size_t)len; line += strcspn(line, "\n") + 1) {
		off = line - buf + strlen(line) + 1;
		if (strstr(line, "virtio") || strstr(line, "mmio") ||
		    strstr(line, "region") || strstr(line, "DMA"))
			printf("  | %s\n", line);
		if (off >= (size_t)len)
			break;
	}
	free(buf);
}

/* The driver's probe already ran and gave up (virtio_mmio returns -ENODEV with
 * no message when DEVICE_ID reads 0, which is what an empty slot looks like),
 * so /sys/bus/platform/drivers/virtio-mmio is bound to nothing. Binding by hand
 * makes the kernel say why.
 */
static void try_bind(void)
{
	static const char *drv = "/sys/bus/platform/drivers/virtio-mmio/bind";
	const char *devs[] = { "a000000.virtio_mmio", "a000200.virtio_mmio",
			       "soc/a000000.virtio_mmio" };
	size_t i;

	printf("===== P7 manual bind of the virtio-mmio slots =====\n");
	for (i = 0; i < sizeof(devs) / sizeof(*devs); i++) {
		int fd = open(drv, O_WRONLY);
		ssize_t n;
		int saved;

		if (fd < 0) {
			printf("  (no %s: %s)\n", drv, strerror(errno));
			return;
		}
		n = write(fd, devs[i], strlen(devs[i]));
		saved = errno;
		close(fd);
		printf("  bind %-26s -> %s\n", devs[i],
		       n > 0 ? "accepted" : strerror(saved));
	}
	dump_kmsg();
	list_dir("/sys/bus/platform/drivers/virtio-mmio", "P7b bound after the attempt");
}

int main(void)
{
	struct utsname u;
	DIR *d;
	struct dirent *e;
	FILE *f;

	mkdir("/proc", 0555);
	mount("proc", "/proc", "proc", 0, NULL);
	mkdir("/sys", 0755);
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	mkdir("/dev", 0755);
	if (mount("devtmpfs", "/dev", "devtmpfs", 0, NULL))
		printf("  (devtmpfs already mounted by the kernel)\n");

	printf("===== P1 kernel =====\n");
	uname(&u);
	printf("  %s %s (%s)\n", u.sysname, u.release, u.machine);

	/* The bootconfig test: a key that reached /proc/cmdline can only have
	 * come from the blob ABL appends, because -append never carries it. */
	printf("===== P1a cmdline =====\n");
	if (!(f = fopen("/proc/cmdline", "r"))) {
		printf("  (no /proc/cmdline: %s)\n", strerror(errno));
	} else {
		char line[1024];
		if (fgets(line, sizeof(line), f))
			printf("  %s", line);
		fclose(f);
	}
	printf("===== P1b /proc/bootconfig =====\n");
	if (!(f = fopen("/proc/bootconfig", "r"))) {
		printf("  (no /proc/bootconfig: %s)\n", strerror(errno));
	} else {
		char line[256];
		int n = 0;
		while (fgets(line, sizeof(line), f) && n++ < 8)
			printf("  %s", line);
		fclose(f);
	}

	printf("===== P2 /sys/devices/platform entries =====\n");
	printf("  (root=/sys/devices/platform)\n");
	d = opendir("/sys/devices/platform");
	if (d) {
		while ((e = readdir(d)))
			if (e->d_name[0] != '.')
				printf("  %s\n", e->d_name);
		closedir(d);
	} else {
		printf("  (cannot open)\n");
	}

	list_soc_children();
	list_dir("/sys/bus/platform/devices", "P2c /sys/bus/platform/devices");
	list_dir("/sys/bus/platform/drivers/virtio-mmio", "P2e virtio-mmio driver bound devices");

	printf("===== P2f virtio-mmio platform devices under any parent =====\n");
	{
		static const char *bases[] = { "/sys/devices/platform",
					     "/sys/devices/platform/soc",
					     "/sys/devices/platform/soc@0" };
		size_t b;

		for (b = 0; b < sizeof(bases) / sizeof(*bases); b++) {
			DIR *pd = opendir(bases[b]);
			struct dirent *pe;

			if (!pd)
				continue;
			while ((pe = readdir(pd))) {
				char path[PATH_MAX], sub[PATH_MAX];
				if (strncmp(pe->d_name, "a000", 4))
					continue;
				snprintf(path, sizeof(path), "%s/%s", bases[b], pe->d_name);
				printf("  %s\n", path);
				snprintf(sub, sizeof(sub), "%s/resource", path);
				cat_file(sub);
				snprintf(sub, sizeof(sub), "%s/modalias", path);
				cat_file(sub);
				snprintf(sub, sizeof(sub), "%s/driver_override", path);
				cat_file(sub);
			}
			closedir(pd);
		}
	}

	printf("===== P3 device-tree soc node =====\n");
	d = opendir("/proc/device-tree");
	if (d) {
		while ((e = readdir(d)))
			if (!strncmp(e->d_name, "soc", 3))
				printf("  /proc/device-tree/%s\n", e->d_name);
		closedir(d);
	}

	printf("===== P4 block devices, sysfs paths, PARTNAME =====\n");
	d = opendir("/sys/class/block");
	if (d) {
		while ((e = readdir(d))) {
			char link[PATH_MAX];
			if (e->d_name[0] == '.')
				continue;
			printf("  %s\n", e->d_name);
			snprintf(link, sizeof(link), "/sys/class/block/%s", e->d_name);
			show_realpath(link);
			dump_uevent(e->d_name);
		}
		closedir(d);
	}

	printf("===== P5 /dev/block =====\n");
	d = opendir("/dev/block");
	if (d) {
		while ((e = readdir(d)))
			printf("  %s\n", e->d_name);
		closedir(d);
	} else {
		printf("  (no /dev/block)\n");
	}

	printf("===== P6 cmdline =====\n");
	f = fopen("/proc/cmdline", "r");
	if (f) {
		char line[2048];
		while (fgets(line, sizeof(line), f))
			printf("  %s", line);
		fclose(f);
	}

	try_bind();
	printf("===== PROBE DONE =====\n");
	fflush(stdout);
	return 0;
}

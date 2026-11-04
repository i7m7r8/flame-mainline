// SPDX-License-Identifier: GPL-2.0-only
/*
 * flame: /sys/kernel/boot_adsp, which the ROM's init waits for.
 *
 * The stock ADSP loader is adsp_loader_dlkm.ko in the vendor ramdisk. It
 * registers a "boot_adsp" object under /sys/kernel carrying exactly two
 * write-only attributes, each mode 0220 (.data 0x110 and 0x130, both holding
 * 0x90): "boot" and "ssr". Userspace uses them as the ADSP on switch:
 * init.qti.kernel.target.rc:10 writes 1 to boot at early-boot, and
 * android.hardware.thermal-service.qti.rc:41 makes
 * "wait /sys/kernel/boot_adsp/boot" the first command of its on-boot action.
 * That wait is the reason this file exists. init's wait builtin blocks the
 * main loop with no timeout when the path is absent, and every action queued
 * behind it - including the class_start that launches the graphics and audio
 * HALs - then never runs. A kernel without this node does not boot a degraded
 * Android, it boots a hung one. init.target.rc:185 waits on the same path in
 * the charger-mode action.
 *
 * What the writes do is what the vendor driver does, out of the same two
 * exported remoteproc calls its disassembly shows: boot_store reaches
 * rproc_get_by_phandle() at +0x628 and rproc_boot() at +0x658, and
 * adsp_ssr_store reaches rproc_shutdown() at +0x908 followed by a queued
 * reload. A zero write is ignored by both, which is kept here because
 * init.qti.write.sh hands it the literal from the .rc file.
 *
 * Deliberately not implemented: the vendor's fuse and variant inputs
 * (qcom,adsp-state, adsp-fw-name, adsp-fw-names, adsp-fw-bit-values) and its
 * strcmp against "modem". Those select which image a PAS caller should be
 * handed, and under mainline the image name comes from the device tree of the
 * remoteproc itself, so there is nothing here for them to decide. Accepting
 * the properties would advertise a behaviour this driver does not have.
 *
 * The kobject is created before the remoteproc handle is looked up, and the
 * handle is in fact resolved per write rather than cached. A probe that
 * deferred until the ADSP driver had bound would keep the node away for
 * exactly as long as that driver kept deferring, and the stall this file
 * exists to prevent would reappear. A write against a subsystem that has not
 * come up yet returns -ENODEV, which init logs and continues past.
 */

#include <linux/kobject.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/remoteproc.h>
#include <linux/slab.h>

struct adsp_loader {
	struct kobject kobj;
	phandle rproc_phandle;
};

static struct adsp_loader *to_adsp_loader(struct kobject *kobj)
{
	return container_of(kobj, struct adsp_loader, kobj);
}

/*
 * Returns a remoteproc with a device reference held, per rproc_get_by_phandle.
 * The caller drops it; the boot reference taken by rproc_boot() is separate and
 * is meant to stay taken.
 */
static struct rproc *adsp_loader_get_rproc(struct adsp_loader *loader)
{
	return rproc_get_by_phandle(loader->rproc_phandle);
}

static ssize_t boot_store(struct kobject *kobj, struct kobj_attribute *attr,
			  const char *buf, size_t count)
{
	struct adsp_loader *loader = to_adsp_loader(kobj);
	struct rproc *rproc;
	int val, ret;

	if (kstrtoint(buf, 10, &val))
		return -EINVAL;
	if (!val)
		return count;

	rproc = adsp_loader_get_rproc(loader);
	if (!rproc)
		return -ENODEV;

	ret = rproc_boot(rproc);
	put_device(&rproc->dev);

	return ret < 0 ? ret : count;
}

static ssize_t ssr_store(struct kobject *kobj, struct kobj_attribute *attr,
			 const char *buf, size_t count)
{
	struct adsp_loader *loader = to_adsp_loader(kobj);
	struct rproc *rproc;
	int val, ret;

	if (kstrtoint(buf, 10, &val))
		return -EINVAL;
	if (!val)
		return count;

	rproc = adsp_loader_get_rproc(loader);
	if (!rproc)
		return -ENODEV;

	/*
	 * rproc_shutdown() returns -EINVAL when the subsystem is not running,
	 * which is the no-op case rather than a failure to report: the vendor
	 * driver reloads unconditionally from its work queue here, and so do we
	 * via the boot right below.
	 */
	rproc_shutdown(rproc);
	ret = rproc_boot(rproc);
	put_device(&rproc->dev);

	return ret < 0 ? ret : count;
}

/* __ATTR's brace order matches kobj_attribute as well as device_attribute;
 * drivers/acpi/acpi_fpdt.c:85 is the in-tree precedent. */
static struct kobj_attribute boot_attribute =
	__ATTR(boot, 0220, NULL, boot_store);
static struct kobj_attribute ssr_attribute =
	__ATTR(ssr, 0220, NULL, ssr_store);

static struct attribute *adsp_loader_attrs[] = {
	&boot_attribute.attr,
	&ssr_attribute.attr,
	NULL,
};

ATTRIBUTE_GROUPS(adsp_loader);

static void adsp_loader_release(struct kobject *kobj)
{
	kfree(to_adsp_loader(kobj));
}

static const struct kobj_type adsp_loader_ktype = {
	.release = adsp_loader_release,
	.default_groups = adsp_loader_groups,
};

static int adsp_loader_probe(struct platform_device *pdev)
{
	struct adsp_loader *loader;
	phandle phandle;
	int ret;

	/*
	 * The name is the vendor binding's own property, read the way the
	 * vendor driver reads it: of_find_property("qcom,rproc-handle") and
	 * the big-endian cell it points at, which is what the u32 accessor
	 * returns.
	 */
	ret = of_property_read_u32(pdev->dev.of_node, "qcom,rproc-handle",
				   &phandle);
	if (ret)
		return dev_err_probe(&pdev->dev, ret,
				     "missing qcom,rproc-handle\n");

	loader = kzalloc(sizeof(*loader), GFP_KERNEL);
	if (!loader)
		return -ENOMEM;

	loader->rproc_phandle = phandle;

	/*
	 * Freed by adsp_loader_release() rather than devm, because a kobject
	 * outlives kobject_put() while a sysfs file on it is still open.
	 */
	kobject_init(&loader->kobj, &adsp_loader_ktype);

	ret = kobject_add(&loader->kobj, kernel_kobj, "boot_adsp");
	if (ret) {
		kfree(loader);
		return dev_err_probe(&pdev->dev, ret,
				     "cannot create /sys/kernel/boot_adsp\n");
	}

	platform_set_drvdata(pdev, loader);

	return 0;
}

static void adsp_loader_remove(struct platform_device *pdev)
{
	struct adsp_loader *loader = platform_get_drvdata(pdev);

	/*
	 * The remoteproc may still hold nothing of ours: both handlers release
	 * the reference they take. The boot references rproc_boot() added stay
	 * with whoever asked for the subsystem, as they do on stock.
	 */
	kobject_put(&loader->kobj);
}

static const struct of_device_id adsp_loader_of_match[] = {
	{ .compatible = "qcom,adsp-loader" },
	{ }
};
MODULE_DEVICE_TABLE(of, adsp_loader_of_match);

static struct platform_driver adsp_loader_driver = {
	.probe = adsp_loader_probe,
	.remove = adsp_loader_remove,
	.driver = {
		.name = "adsp-loader-compat",
		.of_match_table = adsp_loader_of_match,
	},
};
module_platform_driver(adsp_loader_driver);

MODULE_DESCRIPTION("flame: /sys/kernel/boot_adsp for a ROM whose init waits for it");
MODULE_LICENSE("GPL");

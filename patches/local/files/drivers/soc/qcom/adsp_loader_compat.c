// SPDX-License-Identifier: GPL-2.0-only
/*
 * flame: /sys/kernel/boot_adsp, the ROM's ADSP on switch.
 *
 * The stock ADSP loader is adsp_loader_dlkm.ko in the vendor ramdisk (its
 * own symbols say so: adsp_loader_init_sysfs, adsp_boot_store,
 * adsp_ssr_store, and the kobject name boot_adsp). It registers a
 * "boot_adsp" object under /sys/kernel carrying exactly two write-only
 * attributes, each mode 0220 (.data 0x110 and 0x130, both holding 0x90):
 * "boot" and "ssr". Userspace uses them as the ADSP on switch:
 * init.qti.kernel.target.rc:10 writes 1 to boot at early-boot through
 * exec_background, init.target.rc:141 writes it again as a plain foreground
 * command in the ro.boot.mode=charger action, and
 * android.hardware.thermal-service.qti.rc:42 makes
 * "wait /sys/kernel/boot_adsp/boot" the first command of its on-boot action.
 * The live phone has the path: as uid 10412 with CapEff 0, /sys probes return
 * EACCES where a wrong sibling returns ENOENT.
 *
 * That wait is not why this file exists, and a kernel without this node does
 * not hang. The ROM's shipped /system/bin/init carries do_wait's own strings
 * ("wait_for_file() failed", "failed to parse timeout"), and AOSP's do_wait
 * takes kCommandRetryTimeout - 5 seconds - when the rc gives no timeout, then
 * logs and moves on; the class_start queued behind it still runs. This file
 * exists because the write is the only thing on this ROM that boots the ADSP
 * at all, and audio, sensors and FastRPC all sit behind that.
 *
 * What the writes do is what the vendor driver does, read off its own
 * relocations. adsp_boot_store (0x7ac, 14 instructions) scans with
 * sscanf(buf, "%du") - .rodata+0x221 holds "%du" - and on a failed scan prints
 * "adsp_boot_store: failed to read boot info from string" (.rodata+0x2c7 and
 * +0x33d) and returns -EINVAL; 1 queues the boot on system_wq at +0x114, 0
 * calls rproc_shutdown() inline at +0x834, and anything else returns the count
 * unchanged. The queued work is adsp_load_fw, whose relocations name
 * rproc_get_by_phandle() at +0x628 and rproc_boot() at +0x658.
 * adsp_ssr_store (0x888) scans with kstrtoint at +0x8d0, shuts down inline at
 * +0x908 and reloads from queue_work_on at +0x920. Nothing here calls
 * rproc_boot() from the store itself, so the sysfs write always returns
 * without waiting for a firmware load.
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
#include <linux/workqueue.h>

struct adsp_loader {
	struct kobject kobj;
	struct work_struct boot_work;
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

/*
 * The boot runs on a work queue because that is where the vendor puts it, and
 * the reason is measurable: rproc_boot() issues its firmware request
 * synchronously, and the ROM sets
 * /proc/sys/kernel/firmware_config/force_sysfs_fallback to 1
 * (init.qti.kernel.rc:67), which turns that request into the sysfs fallback's
 * full wait - drivers/base/firmware_loader/fallback_table.c:22 seeds
 * loading_timeout at 60 seconds. adsp_boot_store is 14 instructions long and
 * ends at queue_work_on(WORK_CPU_UNBOUND, system_wq, work) at +0x114; the work
 * is adsp_load_fw, whose relocations name rproc_get_by_phandle at +0x628 and
 * rproc_boot at +0x658.
 */
static void adsp_loader_boot_work(struct work_struct *work)
{
	struct adsp_loader *loader =
		container_of(work, struct adsp_loader, boot_work);
	struct rproc *rproc = adsp_loader_get_rproc(loader);
	int ret;

	if (!rproc)
		return;

	ret = rproc_boot(rproc);
	if (ret)
		pr_info("adsp-boot: subsystem %s did not come up (%d)\n",
			rproc->name, ret);
	put_device(&rproc->dev);
}

static ssize_t boot_store(struct kobject *kobj, struct kobj_attribute *attr,
			  const char *buf, size_t count)
{
	struct adsp_loader *loader = to_adsp_loader(kobj);
	struct rproc *rproc;
	unsigned int val;

	/* The vendor scans with sscanf(buf, "%du") - .rodata+0x221 holds
	 * "%du" - and prints "failed to read boot info from string" with
	 * -EINVAL before returning anything else unchanged. */
	if (kstrtouint(buf, 10, &val)) {
		pr_info("adsp_boot_store: failed to read boot info from string\n");
		return -EINVAL;
	}

	switch (val) {
	case 0:
		/* adsp_boot_store takes the subsystem down inline at +0x834. */
		rproc = adsp_loader_get_rproc(loader);
		if (rproc) {
			rproc_shutdown(rproc);
			put_device(&rproc->dev);
		}
		break;
	case 1:
		queue_work(system_wq, &loader->boot_work);
		break;
	}

	return count;
}

static ssize_t ssr_store(struct kobject *kobj, struct kobj_attribute *attr,
			 const char *buf, size_t count)
{
	struct adsp_loader *loader = to_adsp_loader(kobj);
	struct rproc *rproc;
	int val;

	/* kstrtoint here, not the unsigned scan boot_store mirrors: that is
	 * what the vendor's own call at +0x8d0 is. */
	if (kstrtoint(buf, 10, &val))
		return -EINVAL;
	if (!val)
		return count;

	rproc = adsp_loader_get_rproc(loader);
	if (!rproc)
		return -ENODEV;

	/*
	 * adsp_ssr_store shuts down inline (rproc_shutdown at +0x908) and
	 * reloads from the queue (queue_work_on at +0x920), so the write
	 * returns with the shutdown done and the boot pending.
	 * rproc_shutdown() returns -EINVAL when the subsystem is not running,
	 * which is the no-op case; the vendor ignores it and queues anyway,
	 * and so do we.
	 */
	rproc_shutdown(rproc);
	put_device(&rproc->dev);
	queue_work(system_wq, &loader->boot_work);

	return count;
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
	INIT_WORK(&loader->boot_work, adsp_loader_boot_work);

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
	 * Drain a queued boot before the memory its work item lives in goes
	 * away. kobject_put() below is what removes the file, so a write
	 * racing this could still re-queue; the window is theoretical because
	 * nothing unbinds this driver at runtime - it is built in and its
	 * device comes from the board DTS.
	 */
	cancel_work_sync(&loader->boot_work);

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

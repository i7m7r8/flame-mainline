// SPDX-License-Identifier: GPL-2.0-only
/*
 * flame: /dev/qseecom for a vendor userspace, over mainline's QSEECOM calls.
 *
 * This ROM's KeyMint HAL talks to QSEE through /dev/qseecom. qseecomd
 * (class core) and libqtikeymint.so both open that node, and with no node
 * /data cannot be unwrapped at all. Mainline already exports the transport -
 * qcom_scm_qseecom_app_get_id() and qcom_scm_qseecom_app_send(), both taking
 * TZ memory - so what is missing is only the userspace-facing chardev.
 *
 * The ABI was read out of the ROM's own libQSEEComAPI.so, which issues
 * _IOWR(0x97, nr, size) commands. Two of them are enough for a key request:
 *
 *   nr 1, 24 bytes  {u32, u32, char __user *name, u32 name_len}
 *      built at __QSEECom_get_handle+0x3a0..0x3d8, the name a plain userspace
 *      buffer that the library memsets to name_len first.
 *   nr 3, 32 bytes  {void __user *req; u32 req_len; void __user *rsp; u32 rsp_len}
 *      built at QSEECom_send_cmd+0xb690..0xb744, with rsp literally req+req_len
 *      and both lengths rounded up to 64 for TZ.
 *
 * Everything else returns -ENOTTY: a half-implemented listener or memory
 * ioctl is worse than a clean refusal, because the HAL treats an unknown
 * command as a programming error but a silent success as data.
 *
 * This driver is deliberately inert as shipped: the board DTS node is
 * status = "disabled" until the TZ side answers the app lookup. What is NOT
 * known is whether TZ on this device resolves the KeyMint app by name, or
 * whether the trustlet must be loaded from mfido.mdt through calls mainline
 * does not export. Enable the node only with a serial/fbcon log to watch.
 */

#include <linux/fs.h>
#include <linux/firmware/qcom/qcom_scm.h>
#include <linux/firmware/qcom/qcom_tzmem.h>
#include <linux/miscdevice.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#define FLAME_QSEE_NAME_MAX	64
#define FLAME_QSEE_BUF_MAX	(1024 * 1024)

struct flame_qsee_load {
	u32 arg0;
	u32 arg1;
	void __user *name;
	u32 name_len;
};

struct flame_qsee_send {
	void __user *req;
	u32 req_len;
	u32 pad;
	void __user *rsp;
	u32 rsp_len;
};

#define FLAME_QSEE_IOC_MAGIC	0x97
#define FLAME_QSEE_APP_LOAD	_IOWR(FLAME_QSEE_IOC_MAGIC, 1, struct flame_qsee_load)
#define FLAME_QSEE_SEND_CMD	_IOWR(FLAME_QSEE_IOC_MAGIC, 3, struct flame_qsee_send)

struct flame_qsee {
	struct device *dev;
	struct miscdevice mdev;
	struct qcom_tzmem_pool *pool;
	/* one app id and one command at a time: the TA is stateful */
	struct mutex lock;
	u32 app_id;
	bool loaded;
};

static long flame_qsee_app_load(struct flame_qsee *q, void __user *uarg)
{
	struct flame_qsee_load load;
	char *name;
	int ret;

	if (copy_from_user(&load, uarg, sizeof(load)))
		return -EFAULT;
	if (!load.name_len || load.name_len > FLAME_QSEE_NAME_MAX)
		return -EINVAL;

	name = kzalloc(load.name_len + 1, GFP_KERNEL);
	if (!name)
		return -ENOMEM;
	if (copy_from_user(name, load.name, load.name_len)) {
		kfree(name);
		return -EFAULT;
	}

	mutex_lock(&q->lock);
	ret = qcom_scm_qseecom_app_get_id(name, &q->app_id);
	if (!ret) {
		q->loaded = true;
		dev_info(q->dev, "app %s -> id %#x\n", name, q->app_id);
	} else {
		dev_warn(q->dev, "app %s lookup failed: %d\n", name, ret);
	}
	mutex_unlock(&q->lock);

	kfree(name);
	return ret;
}

static long flame_qsee_send_cmd(struct flame_qsee *q, void __user *uarg)
{
	struct flame_qsee_send send;
	void *req = NULL, *rsp = NULL;
	int ret;

	if (copy_from_user(&send, uarg, sizeof(send)))
		return -EFAULT;
	if (!send.req_len || send.req_len > FLAME_QSEE_BUF_MAX ||
	    !send.rsp_len || send.rsp_len > FLAME_QSEE_BUF_MAX)
		return -EINVAL;

	mutex_lock(&q->lock);
	if (!q->loaded) {
		mutex_unlock(&q->lock);
		return -ENODEV;
	}

	req = qcom_tzmem_alloc(q->pool, send.req_len, GFP_KERNEL);
	rsp = qcom_tzmem_alloc(q->pool, send.rsp_len, GFP_KERNEL);
	if (!req || !rsp) {
		ret = -ENOMEM;
		goto out;
	}
	if (copy_from_user(req, send.req, send.req_len)) {
		ret = -EFAULT;
		goto out;
	}
	memset(rsp, 0, send.rsp_len);

	ret = qcom_scm_qseecom_app_send(q->app_id, req, send.req_len,
					rsp, send.rsp_len);
	if (!ret && copy_to_user(send.rsp, rsp, send.rsp_len))
		ret = -EFAULT;
out:
	if (req)
		qcom_tzmem_free(req);
	if (rsp)
		qcom_tzmem_free(rsp);
	mutex_unlock(&q->lock);
	return ret;
}

static long flame_qsee_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	struct flame_qsee *q = container_of(f->private_data,
					    struct flame_qsee, mdev);

	switch (cmd) {
	case FLAME_QSEE_APP_LOAD:
		return flame_qsee_app_load(q, (void __user *)arg);
	case FLAME_QSEE_SEND_CMD:
		return flame_qsee_send_cmd(q, (void __user *)arg);
	default:
		/* The vendor driver prints "Invalid IOCTL: 0x%x" here. */
		dev_dbg(q->dev, "unsupported ioctl %#x\n", cmd);
		return -ENOTTY;
	}
}

static const struct file_operations flame_qsee_fops = {
	.owner		= THIS_MODULE,
	.unlocked_ioctl	= flame_qsee_ioctl,
	.compat_ioctl	= flame_qsee_ioctl,
};

static int flame_qsee_probe(struct platform_device *pdev)
{
	struct qcom_tzmem_pool_config cfg = {
		.initial_size = SZ_256K,
		.policy = QCOM_TZMEM_POLICY_ON_DEMAND,
		.max_size = SZ_4M,
	};
	struct flame_qsee *q;
	int ret;

	q = devm_kzalloc(&pdev->dev, sizeof(*q), GFP_KERNEL);
	if (!q)
		return -ENOMEM;

	q->dev = &pdev->dev;
	mutex_init(&q->lock);

	q->pool = devm_qcom_tzmem_pool_new(&pdev->dev, &cfg);
	if (IS_ERR(q->pool))
		return dev_err_probe(&pdev->dev, PTR_ERR(q->pool),
				     "cannot create the TZ memory pool\n");

	q->mdev.minor = MISC_DYNAMIC_MINOR;
	q->mdev.name = "qseecom";
	q->mdev.fops = &flame_qsee_fops;
	q->mdev.parent = &pdev->dev;

	ret = misc_register(&q->mdev);
	if (ret)
		return ret;

	platform_set_drvdata(pdev, q);
	dev_info(&pdev->dev, "/dev/qseecom on the mainline QSEECOM SCM calls\n");
	return 0;
}

static void flame_qsee_remove(struct platform_device *pdev)
{
	struct flame_qsee *q = platform_get_drvdata(pdev);

	misc_deregister(&q->mdev);
}

static const struct of_device_id flame_qsee_of_match[] = {
	{ .compatible = "flame,qseecom-shim" },
	{ }
};

static struct platform_driver flame_qsee_driver = {
	.probe = flame_qsee_probe,
	.remove = flame_qsee_remove,
	.driver = {
		.name = "flame-qseecom-shim",
		.of_match_table = flame_qsee_of_match,
	},
};
builtin_platform_driver(flame_qsee_driver);

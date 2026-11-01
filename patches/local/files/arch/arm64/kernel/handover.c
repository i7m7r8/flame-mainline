// SPDX-License-Identifier: GPL-2.0-only
/*
 * flame: finish what the bootloader started.
 *
 * The boot image we ship is a mainline Image whose first instruction has been
 * patched to branch into tools/shim.S. That shim relocates the Image and jumps
 * to primary_entry with *our* DTB in x0, because arm64 always takes the tree it
 * is handed and a vendor tree binds only vendor drivers.
 *
 * Dropping that tree, however, also drops everything ABL injected into it at
 * run time, and two of those things are not ours to invent:
 *
 *   /chosen/bootargs		androidboot.slot_suffix, androidboot.vbmeta.*,
 *				androidboot.bootdevice. Without them init cannot
 *				pick the active slot or the AVB chain, so the ROM
 *				cannot mount /vendor and the device does not get
 *				past first-stage init.
 *   /chosen/linux,initrd-*	where ABL left the ROM's first-stage init
 *				ramdisk. Our tree says nothing about it, so
 *				without this the kernel boots with no initrd at
 *				all and panics looking for an init.
 *
 * The shim copied the injected tree into a reserved region before it could be
 * overwritten and left a record saying where. This file reads it back and
 * grafts those two pieces onto our own boot.
 *
 * Timing. The hook runs from setup_arch() right after paging_init(), which is
 * the earliest point at which a tree of ~650 KiB can be read at all: the fixmap
 * and early_memremap slots available before then cannot map a region that big,
 * and the linear map itself only exists once paging_init has built it. It is
 * still early enough for both jobs:
 *
 *   - boot_command_line is not consumed until start_kernel() calls
 *     setup_command_line(), after setup_arch() has returned, so appending here
 *     is what /proc/cmdline shows and what init parses. parse_early_param() has
 *     already run, from a private copy, so nothing it read changes.
 *   - memblock is still the only allocator: the pages go to the buddy allocator
 *     in memblock_free_all(), which does not run until mm_init(). The arm64
 *     initrd conversion in arm64_memblock_init() has already been and gone,
 *     because our own tree carries no linux,initrd-* properties, so that
 *     conversion is repeated here and the range reserved before anything can
 *     allocate over the ramdisk.
 */

#define pr_fmt(fmt) "handover: " fmt

/* linux/types.h before any asm header: asm/setup.h declares
 * `extern u64 __cacheline_aligned boot_args[4]` without including the type
 * itself, and every other arm64 file happens to pull types.h in first. */
#include <linux/build_bug.h>
#include <linux/cache.h>
#include <linux/errno.h>
#include <linux/init.h>
#include <linux/initrd.h>
#include <linux/libfdt.h>
#include <linux/memblock.h>
#include <linux/of_fdt.h>
#include <linux/printk.h>
#include <linux/sizes.h>
#include <linux/stddef.h>
#include <linux/string.h>
#include <linux/types.h>

#include <asm/byteorder.h>
#include <asm/handover.h>
#include <asm/memory.h>
#include <asm/page.h>
#include <asm/sections.h>
#include <asm/setup.h>

/*
 * The shim's record, at the start of the region. Field order is shared with
 * .Lhv_record in tools/shim.S and pinned by the BUILD_BUG_ONs below, because a
 * silently misplaced field here means a silently wrong command line on the
 * phone.
 */
#define HANDOVER_MAGIC		0x56444e484d414c46ULL	/* "FLAMHNDV" */
#define HANDOVER_VERSION	1

struct handover_record {
	__le64 magic;
	__le32 version;
	__le32 flags;
	__le64 vendor_dtb;	/* the tree as it was passed to the shim */
	__le64 copy;		/* where the shim copied it, 0 if it could not */
	__le32 copy_size;
	__le32 rfu;
};

/* the label the board DTS puts the region under, /reserved-memory/<name> */
#define HANDOVER_NODE	"handover"

struct handover_region {
	phys_addr_t base;
	phys_addr_t size;
};

static u64 get_be(const void *val, int len)
{
	__be64 v = 0;

	/*
	 * Device tree properties are big endian and 1, 2 or (rarely) more cells
	 * wide. Anything narrower than a u64 is left aligned in the cells it
	 * uses, so it goes into the top of ours; anything wider than 8 bytes is
	 * a value we cannot represent and reads as nothing.
	 */
	if (len < 1 || len > 8)
		return 0;
	memcpy((char *)&v + 8 - len, val, len);
	return be64_to_cpu(v);
}

/*
 * Where is the region? Asked of the DT rather than hardcoded, so the board DTS
 * and the payload header cannot drift apart in silence: tools/mklinuxpayload.py
 * refuses to pack a payload whose --scratch-load is not the address this node
 * names, and a kernel built without that node does nothing here rather than
 * reading whatever is at a guessed address.
 */
static int __init find_region(struct handover_region *r)
{
	const void *dt = initial_boot_params;
	int mem, node;

	if (!dt)
		return -ENOENT;

	mem = fdt_path_offset(dt, "/reserved-memory");
	if (mem < 0)
		return mem;

	fdt_for_each_subnode(node, dt, mem) {
		const char *name = fdt_get_name(dt, node, NULL);
		const void *reg;
		int len;

		/*
		 * dtc stores the node name with its unit address, so the tree says
		 * "handover@bc000000". Compared as a prefix that would also match
		 * "handoverxyz", and mklinuxpayload.py checks the *basename* against
		 * the same string - the two sides have to agree exactly or the packer
		 * validates one node while the kernel reads another. strncmp keeps the
		 * read inside the eight bytes we are looking for; the byte after them
		 * is the terminator or the '@', and a name that matches is long enough
		 * to have one.
		 */
		if (!name || strncmp(name, HANDOVER_NODE, sizeof(HANDOVER_NODE) - 1))
			continue;
		if (name[sizeof(HANDOVER_NODE) - 1] &&
		    name[sizeof(HANDOVER_NODE) - 1] != '@')
			continue;
		/* two address cells and two size cells, as the board DTS writes it */
		reg = fdt_getprop(dt, node, "reg", &len);
		if (!reg || len != 16)
			continue;
		r->base = (phys_addr_t)get_be(reg, 8);
		r->size = (phys_addr_t)get_be((const char *)reg + 8, 8);
		if (!r->base || !r->size)
			continue;
		return 0;
	}

	return -ENOENT;
}

/*
 * Validate the record and the copy it points at. Everything here is data a
 * bootloader wrote into memory we are about to take over; a half-written record
 * from an older or damaged shim must not become a command line, let alone a
 * memblock reservation.
 */
static const void * __init vendor_tree(struct handover_region *r)
{
	const struct handover_record *rec;
	phys_addr_t vendor, copy;
	u32 copy_size;
	const void *fdt;

	if (find_region(r)) {
		pr_info("no /reserved-memory/" HANDOVER_NODE " node, nothing to do\n");
		return NULL;
	}

	/*
	 * Reserved in the DTS without no-map, which is the whole trick: the
	 * region is inside memblock.memory, so paging_init mapped it and __va()
	 * reaches it, while the reservation keeps the allocator off it.
	 */
	if (!memblock_is_memory(r->base) || !memblock_is_memory(r->base + r->size - 1)) {
		pr_warn("region %pa+%pa is not mapped memory\n", &r->base, &r->size);
		return NULL;
	}

	rec = __va(r->base);
	vendor = (phys_addr_t)le64_to_cpu(rec->vendor_dtb);
	if (le64_to_cpu(rec->magic) != HANDOVER_MAGIC) {
		pr_info("no record at %pa; entered without our shim\n", &r->base);
		return NULL;
	}
	if (le32_to_cpu(rec->version) != HANDOVER_VERSION) {
		pr_warn("record version %u, this kernel knows %u\n",
			le32_to_cpu(rec->version), HANDOVER_VERSION);
		return NULL;
	}

	copy = (phys_addr_t)le64_to_cpu(rec->copy);
	copy_size = le32_to_cpu(rec->copy_size);
	if (!copy || !copy_size) {
		/*
		 * The shim got as far as writing the record, then found nothing in
		 * x0 worth copying. Losing the injected tags is survivable enough
		 * to reach a shell; the ROM itself will not get past first-stage
		 * init, so this is worth saying loudly.
		 */
		pr_notice("shim copied nothing; bootloader tree was at %pa\n", &vendor);
		return NULL;
	}

	/* the copy has to lie inside the region the record lives in */
	if (copy < r->base + PAGE_SIZE || copy + copy_size > r->base + r->size) {
		pr_warn("copy %pa+%u is outside region %pa+%pa\n",
			&copy, copy_size, &r->base, &r->size);
		return NULL;
	}

	fdt = __va(copy);
	if (fdt_check_header(fdt)) {
		pr_warn("copy at %pa is not a flattened tree\n", &copy);
		return NULL;
	}
	/*
	 * The shim clamps the copy to whatever fits. A truncated tree is worse
	 * than none: the structure and strings block offsets in its header would
	 * point past the copied bytes into whatever follows, and libfdt would
	 * happily follow them. Refuse unless the whole thing came over.
	 */
	if (fdt_totalsize(fdt) > copy_size) {
		pr_warn("tree is %u bytes, only %u were copied\n",
			fdt_totalsize(fdt), copy_size);
		return NULL;
	}

	pr_info("bootloader tree: %u bytes copied to %pa from %pa\n",
		fdt_totalsize(fdt), &copy, &vendor);
	return fdt;
}

/*
 * ABL's bootargs are for Android, and the kernel must not swallow the rest of
 * them: a vendor line can carry mem=, no_console_suspend or a parameter whose
 * meaning mainline has changed. Only the androidboot.* tags are what userspace
 * needs - init turns each one into a ro.boot.* property - so only those are
 * appended, whole or not at all.
 */
static void __init append_androidboot(const void *fdt)
{
	static const char and[] = "androidboot.";
	const size_t andlen = sizeof(and) - 1;
	size_t used = strlen(boot_command_line);
	const char *args, *p, *end;
	int chosen, len, taken = 0, skipped = 0;

	chosen = fdt_path_offset(fdt, "/chosen");
	if (chosen < 0) {
		pr_notice("bootloader tree has no /chosen\n");
		return;
	}

	args = fdt_getprop(fdt, chosen, "bootargs", &len);
	if (!args || len < 2) {
		pr_notice("bootloader tree has no bootargs\n");
		return;
	}

	/* One line of it verbatim: on the first boot of a new ROM this is the
	 * whole question, and dmesg is the only place it can be asked. Bounded by
	 * the property length - a DT value is not guaranteed to be terminated,
	 * and this one was written by a bootloader we did not audit. */
	pr_info("bootloader bootargs: %.*s\n", (int)strnlen(args, len), args);

	end = args + len - 1;		/* the property carries a terminator */
	p = args;
	while (p < end) {
		const char *tok;
		size_t tlen;

		while (p < end && (*p == ' ' || *p == '\t'))
			p++;
		tok = p;
		while (p < end && *p != ' ' && *p != '\t')
			p++;
		tlen = p - tok;
		if (!tlen)
			continue;

		if (tlen <= andlen || memcmp(tok, and, andlen)) {
			skipped++;
			continue;
		}

		/* one space plus the token plus the terminator, or nothing */
		if (used + 1 + tlen + 1 > COMMAND_LINE_SIZE) {
			pr_warn("command line full at %zu bytes, dropped the rest\n",
				used);
			break;
		}
		boot_command_line[used++] = ' ';
		memcpy(boot_command_line + used, tok, tlen);
		used += tlen;
		boot_command_line[used] = '\0';
		taken++;
	}

	pr_info("appended %d bootloader tags, ignored %d others, line is now %zu/%u bytes\n",
		taken, skipped, used, COMMAND_LINE_SIZE);
}

/*
 * The ramdisk ABL built for the ROM. Our DTB cannot describe it - the address is
 * chosen when the boot image sections are laid out, at run time - so it has to
 * come from the injected tree, be turned into virtual addresses the way
 * arm64_memblock_init() would have done, and be reserved before the page
 * allocator takes it.
 */
static void __init take_initrd(const void *fdt)
{
	phys_addr_t kstart = __pa_symbol(_text), kend = __pa_symbol(_end);
	phys_addr_t start = 0, end = 0;
	phys_addr_t res, res_end;
	const void *val;
	int chosen, len;

	if (initrd_start) {
		pr_info("our own tree already has an initrd, keeping it\n");
		return;
	}

	chosen = fdt_path_offset(fdt, "/chosen");
	if (chosen < 0)
		return;

	val = fdt_getprop(fdt, chosen, "linux,initrd-start", &len);
	if (!val) {
		/*
		 * Android 13+ always injects this, because first stage init lives in
		 * the generic ramdisk, so its absence means the copy is not the tree
		 * ABL built. Say so instead of booting quietly initrd-less.
		 */
		pr_notice("bootloader tree has no linux,initrd-start\n");
		return;
	}
	start = get_be(val, len);

	val = fdt_getprop(fdt, chosen, "linux,initrd-end", &len);
	if (!val) {
		pr_notice("bootloader tree has no linux,initrd-end\n");
		return;
	}
	end = get_be(val, len);

	if (end <= start || end - start > SZ_256M) {
		pr_warn("initrd %pa-%pa is nonsense, ignoring\n", &start, &end);
		return;
	}
	if (!memblock_is_memory(start) || !memblock_is_memory(end - 1)) {
		pr_warn("initrd %pa-%pa is not in memory, ignoring\n", &start, &end);
		return;
	}

	/*
	 * The one way this fails that is our own fault: the shim copied the Image
	 * to image_load before we got here, so a ramdisk ABL parked under that
	 * range was overwritten on the way in. Nothing to recover, but it is a
	 * fixable mistake - move --image-load - and without this line it looks
	 * like a corrupt ROM.
	 */
	if (start < kend && kstart < end) {
		pr_emerg("initrd %pa-%pa overlaps the kernel image %pa-%pa: the shim's copy destroyed it. Move --image-load.\n",
			 &start, &end, &kstart, &kend);
		return;
	}

	/*
	 * Anything else already claimed: our own reserved-memory nodes, notably
	 * the handover region, and the page tables paging_init just built.
	 * memblock_reserve does not refuse an overlap, it only marks the range,
	 * so without this test the ramdisk would silently share pages with the
	 * splash framebuffer or the record we read five lines above, and the
	 * corruption would surface as a broken initramfs much later.
	 *
	 * The reservation is page rounded the way arm64_memblock_init() does it,
	 * while the addresses handed to the init code stay exact: populate_rootfs
	 * unpacks from initrd_start, which has to be the cpio's first byte.
	 */
	res = start & PAGE_MASK;
	res_end = PAGE_ALIGN(end);
	if (memblock_is_region_reserved(res, res_end - res)) {
		pr_warn("initrd %pa-%pa overlaps memory the kernel already owns, ignoring\n",
			&start, &end);
		return;
	}

	if (memblock_reserve(res, res_end - res))
		pr_warn("could not reserve initrd %pa-%pa\n", &res, &res_end);

	/* arm64_memblock_init() is behind us, so finish its job by hand */
	phys_initrd_start = start;
	phys_initrd_size = end - start;
	initrd_start = (unsigned long)__va(start);
	initrd_end = initrd_start + phys_initrd_size;

	/*
	 * Not a verdict, only a clue: a newc cpio means the image survived.
	 * Anything else is either a compressed ramdisk, which the kernel unpacks
	 * anyway, or memory that belongs to something else.
	 */
	if (memcmp((const void *)initrd_start, "070701", 6) == 0)
		pr_info("initrd %pa-%pa is an uncompressed cpio\n", &start, &end);
	else
		pr_notice("initrd %pa-%pa does not start with 070701\n", &start, &end);
}

void __init handover_cmdline_extend(void)
{
	struct handover_region r = { 0, 0 };
	const void *fdt;

	/* the asm writes these fields at these offsets; see .Lhv_record */
	BUILD_BUG_ON(offsetof(struct handover_record, magic) != 0);
	BUILD_BUG_ON(offsetof(struct handover_record, version) != 8);
	BUILD_BUG_ON(offsetof(struct handover_record, flags) != 12);
	BUILD_BUG_ON(offsetof(struct handover_record, vendor_dtb) != 16);
	BUILD_BUG_ON(offsetof(struct handover_record, copy) != 24);
	BUILD_BUG_ON(offsetof(struct handover_record, copy_size) != 32);
	BUILD_BUG_ON(sizeof(struct handover_record) != 40);

	/* Not fatal, but the ROM cannot boot without a first stage init, so the
	 * one line that explains why is worth having in the log. */
	if (!IS_ENABLED(CONFIG_BLK_DEV_INITRD))
		pr_warn("BLK_DEV_INITRD is off: the bootloader's ramdisk is unreachable\n");

	fdt = vendor_tree(&r);
	if (!fdt)
		return;

	append_androidboot(fdt);
	if (IS_ENABLED(CONFIG_BLK_DEV_INITRD))
		take_initrd(fdt);
}

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
 * run time, and three of those things are not ours to invent:
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
 *   /memory			how much DRAM the unit in the hand has. The
 *				board DTS can only carry a literal, and this
 *				model ships in more than one size: too small
 *				wastes the difference, too large hands the page
 *				allocator addresses that are not memory.
 *
 * The shim copied the injected tree into a reserved region before it could be
 * overwritten and left a record saying where. This file reads it back and
 * grafts those three pieces onto our own boot.
 *
 * Timing. The hook runs from setup_arch() right after paging_init(), which is
 * Timing. The command line and the ramdisk run from setup_arch() right after
 * paging_init(), which is the earliest point at which a tree of ~650 KiB can
 * be read at all: the fixmap and early_memremap slots available before then
 * cannot map a region that big, and the linear map itself only exists once
 * paging_init has built it. It is still early enough for both jobs:
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
 *
 * The memory graft cannot wait that long. memblock takes its memory ranges from
 * the flat-tree scan in setup_machine_fdt() and paging_init() builds the linear
 * map out of exactly those, so by the time the hook above runs the address of
 * every struct page is already fixed and a range discovered later is either
 * unreachable or worse. That one therefore runs from setup_machine_fdt(), just
 * before the scan, editing our own flat tree in place while it is still mapped
 * read-write. It reads the injected tree through a 256 KiB early_memremap
 * window instead of the linear map, which bounds what it can reach; see
 * handover_memory_fixup().
 */

#define pr_fmt(fmt) "handover: " fmt

/* linux/types.h before any asm header: asm/setup.h declares
 * `extern u64 __cacheline_aligned boot_args[4]` without including the type
 * itself, and every other arm64 file happens to pull types.h in first. */
#include <linux/align.h>
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
#include <linux/unaligned.h>

#include <asm/byteorder.h>
#include <asm/early_ioremap.h>
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
static int __init find_region(const void *dt, struct handover_region *r)
{
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
 * How much DRAM the unit in the hand has, and where it sits.
 *
 * ABL reads the DRAM map from the memory controller and writes it into the
 * /memory node of the tree it builds for the OS. Our tree has to carry a
 * literal instead, because the shim cannot grow a property into a blob it was
 * handed, and a literal is wrong somewhere: this model ships in more than one
 * memory size, and the second bank of a 4 GB part is not where a 6 GB part's
 * is. Too small only wastes RAM. Too large is not recoverable: memblock hands
 * the page allocator addresses that no DRAM answers at, and the first unlucky
 * allocation trips SError with nobody holding a page table to blame. So the
 * firmware's answer wins whenever it can be read, and the literal stays as the
 * fallback for the boots where it cannot.
 *
 * The window. This runs before paging_init(), so the only way in is the fixmap
 * area, and early_memremap() gives out NR_FIX_BTMAPS pages at a time - 256 KiB,
 * against an injected tree of roughly 650 KiB. Reaching /memory inside that is
 * not luck exactly, but it is not a guarantee either: dtc emits the struct
 * block in source order and every ravelin source tree seen here puts /memory
 * first among the root's children. Because a truncated map is what we have to
 * work with, every byte the walk below touches is checked against the mapped
 * length first, and a tree whose /memory falls outside the window is refused
 * rather than read past the end of it.
 *
 * One window is not enough, though, and the header says so: the property names
 * are offsets into a strings block that dtc writes *after* the struct block,
 * and in the merged ravelin blob measured here that block starts 580 KiB into
 * the 633 KiB tree - outside anything a 256 KiB map can reach. So the walk runs
 * over two maps, struct block in one and strings block in the other, which the
 * fixmap can hand out because arm64 gives early_ioremap seven slots precisely
 * so that a caller may hold more than one.
 */
#define HO_MAP_LEN		SZ_256K
#define HO_MEMBLK_MAX		4
#define HO_REG_BYTES		16	/* two #address-cells + two #size-cells */

struct ho_memblk {
	u64 base;
	u64 size;
};

/* Big-endian u32 at an absolute offset, or false when it is not mapped. */
static bool __init be32_at(const void *tree, size_t len, size_t off, u32 *val)
{
	if (off + 4 > len)
		return false;
	*val = get_unaligned_be32((const char *)tree + off);
	return true;
}

/*
 * String compare at an absolute offset, staying inside the mapped prefix.
 * libfdt's own accessors trust the sizes the blob declares for itself, and a
 * half-mapped tree is precisely the case where that is the wrong thing to do.
 */
static bool __init str_is_at(const void *tree, size_t len, size_t off,
			     const char *want)
{
	const char *s = tree;
	size_t i;

	for (i = 0; want[i]; i++) {
		if (off + i >= len || s[off + i] != want[i])
			return false;
	}
	return off + i < len && s[off + i] == '\0';
}

/* Length of a string including its terminator, 0 if not terminated in range. */
static size_t __init str_len_at(const void *tree, size_t len, size_t off)
{
	const char *s = tree;
	size_t i;

	for (i = 0; off + i < len; i++)
		if (s[off + i] == '\0')
			return i + 1;
	return 0;
}

/*
 * Does the node name at this offset read "memory" or "memory@80000000"? Node
 * names are stored with their unit address, so the basename has to be matched
 * as a prefix and followed by the terminator or the '@' - the same rule
 * find_region() applies to "handover@bc000000", for the same reason.
 */
static bool __init mem_node_at(const void *tree, size_t len, size_t off)
{
	const char *s = tree;
	int i;

	for (i = 0; i < 6; i++)
		if (off + i >= len || s[off + i] != "memory"[i])
			return false;
	return off + 6 < len && (!s[off + 6] || s[off + 6] == '@');
}

/*
 * Is the value at this offset the string "memory"? Six or seven bytes: a DT
 * value is not required to be NUL-terminated, and dtc happens to terminate
 * device_type anyway, so both spellings are the same property.
 */
static bool __init val_is_memory(const void *tree, size_t len,
				 size_t off, size_t vlen)
{
	const char *s = tree;

	if (vlen != 6 && vlen != 7)
		return false;
	if (off + vlen > len)
		return false;
	if (vlen == 7 && s[off + 6])
		return false;
	return !memcmp(s + off, "memory", 6);
}

/*
 * Collect the ranges of every root child with device_type = "memory". Returns
 * the count, 0 for none found, or -1 when the walk could not stay inside the
 * mapped prefix - which is the same "none found" as far as the outcome goes,
 * but worth telling apart in the log.
 */
static int __init scan_mem_ranges(const void *tree, size_t len,
				  const void *str, size_t slen,
				  struct ho_memblk *m, int max)
{
	size_t sbase, p, dt_at, dt_len, reg_at, reg_len;
	u32 token, plen, nameoff, cells_a = 0, cells_s = 0;
	int depth = 0, found = 0, i, n;

	sbase = fdt_off_dt_struct(tree);
	if (sbase + 4 > len || sbase < 40)
		return -1;

	for (p = sbase; found < max; ) {
		if (!be32_at(tree, len, p, &token))
			goto overrun;
		p += 4;

		switch (token) {
		case FDT_BEGIN_NODE: {
			size_t nlen = str_len_at(tree, len, p);

			if (!nlen)
				goto overrun;
			/*
			 * Names are in the struct block, so stopping needs no second
			 * map. Once one memory node has been read, stop at the first
			 * root child that is not another: pressing on would spend the
			 * whole window walking through /soc to prove the rest of the
			 * tree is not memory, and running off the end of the map is
			 * the one failure this code must not turn into a guess.
			 */
			if (depth == 1 && found && !mem_node_at(tree, len, p))
				goto done;
			p += ALIGN(nlen, 4);
			depth++;
			/*
			 * The root node counts as a level, so "/" is depth 1 and
			 * the root's children are depth 2: that is where
			 * device_type and reg live.
			 */
			if (depth == 2) {
				dt_at = 0;
				dt_len = 0;
				reg_at = 0;
				reg_len = 0;
			}
			break;
		}

		case FDT_END_NODE:
			if (depth == 2 && dt_at && reg_at && reg_len &&
			    !(reg_len % HO_REG_BYTES) &&
			    val_is_memory(tree, len, dt_at, dt_len)) {
				n = reg_len / HO_REG_BYTES;
				if (found + n > max)
					n = max - found;
				for (i = 0; i < n; i++) {
					const char *at = (const char *)tree +
							 reg_at + i * HO_REG_BYTES;

					m[found].base = get_unaligned_be64(at);
					m[found].size = get_unaligned_be64(at + 8);
					found++;
				}
			}
			depth--;
			break;

		case FDT_PROP:
			if (!be32_at(tree, len, p, &plen) ||
			    !be32_at(tree, len, p + 4, &nameoff))
				goto overrun;
			p += 8;
			if (p > len || plen > len - p)
				goto overrun;	/* the value is outside the window */

			/* nameoff counts from the start of the strings block, which
			 * is the other map, and str_is_at() keeps it inside that one
			 * too: a tree with a truncated copy simply stops matching. */
			if (depth == 1) {
				if (plen == 4 &&
				    str_is_at(str, slen, nameoff, "#address-cells")) {
					if (!be32_at(tree, len, p, &cells_a))
						goto overrun;
				} else if (plen == 4 &&
					   str_is_at(str, slen, nameoff, "#size-cells")) {
					if (!be32_at(tree, len, p, &cells_s))
						goto overrun;
				}
			} else if (depth == 2) {
				if (!dt_at && str_is_at(str, slen, nameoff, "device_type")) {
					dt_at = p;
					dt_len = plen;
				} else if (!reg_at && str_is_at(str, slen, nameoff, "reg")) {
					reg_at = p;
					reg_len = plen;
				}
			}
			p += ALIGN(plen, 4);
			break;

		case FDT_NOP:
			break;

		case FDT_END:
			goto done;

		default:
			return -1;	/* not a token: the walk has lost the plot */
		}
	}

overrun:
	/*
	 * Out of mapped data. What was collected before that is sound - each
	 * range was checked as a whole, inside the window, when its property was
	 * read - so a short map costs only whatever came after /memory in the
	 * tree, and losing a range means declaring too little, which is the safe
	 * direction. Nothing collected means nothing to graft.
	 */
	if (!found)
		return -1;
	goto done;

done:
	if (!found)
		return 0;
	/*
	 * Two cells each is what the ranges below were parsed as, and the only
	 * thing a 64-bit platform's /memory can honestly be written in. A tree
	 * that says otherwise is not one this code was written for.
	 */
	if (cells_a != 2 || cells_s != 2) {
		pr_warn("bootloader tree uses cells %u/%u, cannot read its /memory\n",
			cells_a, cells_s);
		return -1;
	}
	return found;
}

static bool __init memblk_in_range(const struct ho_memblk *m, int n, u64 from,
				   u64 to)
{
	int i;

	for (i = 0; i < n; i++)
		if (m[i].base <= from && to < m[i].base + m[i].size)
			return true;
	return false;
}

/*
 * Numbers straight out of a bootloader are not a reason to hand memblock an
 * address. Two of the checks below are also self-preservation: the region the
 * shim parked the tree in, and this tree itself, both have to survive inside
 * whatever map we are about to install, or the late hook and the flattened
 * tree lose their data.
 */
static bool __init memblk_sane(const struct ho_memblk *m, int n,
			       const struct handover_region *r,
			       phys_addr_t dt_phys, size_t dt_len)
{
	const u64 top = 1ULL << PHYS_MASK_SHIFT;
	int i;

	for (i = 0; i < n; i++) {
		if (m[i].size < SZ_4M) {
			pr_warn("range %llx+%llx is too small to be DRAM\n",
				m[i].base, m[i].size);
			return false;
		}
		if (m[i].base + m[i].size < m[i].base ||
		    m[i].base + m[i].size > top) {
			pr_warn("range %llx+%llx is off the end of the address space\n",
				m[i].base, m[i].size);
			return false;
		}
	}

	if (!memblk_in_range(m, n, (u64)r->base, (u64)r->base + r->size - 1)) {
		pr_warn("scratch region %pa+%pa is outside the reported memory\n",
			&r->base, &r->size);
		return false;
	}
	if (!memblk_in_range(m, n, dt_phys, dt_phys + dt_len - 1)) {
		pr_warn("our own tree at %pa is outside the reported memory\n",
			&dt_phys);
		return false;
	}
	return true;
}

/*
 * Overwrite our tree's /memory reg. In place and no longer than the property
 * already is: fdt_setprop_inplace() refuses anything else, because resizing a
 * property would move the struct block under a tree that has already been
 * mapped and memblock-reserved. Spare slots repeat the last real range, which
 * memblock folds back into the first without complaint - a zero-length one
 * would print "Ignoring memory block" on every boot instead.
 */
static void __init graft_mem_ranges(void *our, const struct ho_memblk *m, int n)
{
	char buf[HO_MEMBLK_MAX * HO_REG_BYTES];
	int room, node, matched = 0, len, err;
	const void *reg;
	size_t i;

	fdt_for_each_subnode(node, our, 0) {
		const char *type;
		int tlen;

		type = fdt_getprop(our, node, "device_type", &tlen);
		/* "memory" plus its terminator: a DT value is not NUL-terminated
		 * by contract, so the byte after the six is checked by hand. */
		if (!type || tlen < 7 || memcmp(type, "memory", 6) || type[6])
			continue;
		if (matched++) {
			pr_warn("our tree has more than one /memory, grafting the first only\n");
			break;
		}
		reg = fdt_getprop(our, node, "reg", &len);
		if (!reg || len < HO_REG_BYTES || len % HO_REG_BYTES ||
		    len > (int)sizeof(buf)) {
			pr_warn("our /memory reg is %d bytes, expected a multiple of %d up to %zu\n",
				len, HO_REG_BYTES, sizeof(buf));
			return;
		}
		room = len / HO_REG_BYTES;
		if (n > room) {
			pr_warn("firmware reports %d ranges, our tree has room for %d; the rest are dropped\n",
				n, room);
			n = room;
		}
		for (i = 0; i < (size_t)room; i++) {
			const struct ho_memblk *b = &m[i < (size_t)n ? i : (size_t)n - 1];

			put_unaligned_be64(b->base, buf + i * HO_REG_BYTES);
			put_unaligned_be64(b->size, buf + i * HO_REG_BYTES + 8);
		}
		err = fdt_setprop_inplace(our, node, "reg", buf, len);
		if (err) {
			pr_warn("cannot rewrite /memory reg: %s\n", fdt_strerror(err));
			return;
		}
		pr_info("memory from the bootloader:");
		for (i = 0; i < (size_t)n; i++)
			pr_cont(" 0x%llx@0x%llx", m[i].size, m[i].base);
		pr_cont("\n");
		return;
	}

	pr_notice("our tree has no /memory node to graft onto\n");
}

/*
 * Called from setup_machine_fdt(), before early_init_dt_scan() has let memblock
 * see our tree. Everything it touches is either our own blob, already mapped in
 * the fixmap FDT slot, or a window over the scratch region.
 */
void __init handover_memory_fixup(void *our_dtb, phys_addr_t dt_phys)
{
	struct handover_region r = { 0, 0 };
	struct handover_record rec;
	struct ho_memblk m[HO_MEMBLK_MAX];
	phys_addr_t copy;
	u32 copy_size, strtb, strsz;
	size_t map_len, str_len = 0;
	void *map, *str = NULL;
	int n;

	if (!our_dtb)
		return;		/* fixmap_remap_fdt failed; setup_machine_fdt says so next */

	if (find_region(our_dtb, &r))
		return;		/* no scratch region, so not one of our images */

	map = early_memremap(r.base, sizeof(rec));
	if (!map) {
		pr_warn("cannot map the record at %pa\n", &r.base);
		return;
	}
	rec = *(const struct handover_record *)map;
	early_memunmap(map, sizeof(rec));

	/*
	 * Quietly returning here is right more often than logging: this runs on
	 * every boot, including one where the shim was not involved at all, and
	 * the late hook already says the interesting part when the ROM cannot
	 * find its ramdisk.
	 */
	if (le64_to_cpu(rec.magic) != HANDOVER_MAGIC)
		return;
	if (le32_to_cpu(rec.version) != HANDOVER_VERSION) {
		pr_warn("record version %u, this kernel knows %u\n",
			le32_to_cpu(rec.version), HANDOVER_VERSION);
		return;
	}

	copy = (phys_addr_t)le64_to_cpu(rec.copy);
	copy_size = le32_to_cpu(rec.copy_size);
	if (!copy || !copy_size)
		return;
	if (copy < r.base + PAGE_SIZE || copy + copy_size > r.base + r.size) {
		pr_warn("copy %pa+%u is outside region %pa+%pa\n",
			&copy, copy_size, &r.base, &r.size);
		return;
	}

	map_len = min_t(size_t, copy_size, HO_MAP_LEN);
	map = early_memremap(copy, map_len);
	if (!map) {
		pr_warn("cannot map %zu bytes of the bootloader tree at %pa\n",
			map_len, &copy);
		return;
	}

	if (fdt_check_header(map)) {
		pr_warn("copy at %pa is not a flattened tree\n", &copy);
		n = -1;
	} else {
		strtb = fdt_off_dt_strings(map);
		strsz = fdt_size_dt_strings(map);
		/*
		 * The strings have to be inside the copy the shim actually made. If a
		 * tree overran the region the shim clamped its copy, the names are the
		 * part that fell off, and a walk that cannot name a property must not
		 * guess at one. strtb is compared before the subtraction because
		 * copy_size - strtb underflows and then admits anything.
		 */
		if (strtb < 40 || strtb > copy_size || strsz > copy_size - strtb) {
			pr_warn("strings at %#x+%u are not inside the %u byte copy\n",
				strtb, strsz, copy_size);
			n = -1;
		} else {
			/* The names we look for are all near the front of a ~50 KiB
			 * block; a longer one is read as far as the window goes and
			 * what is not there simply fails to match, which ends the
			 * graft rather than the boot. */
			str_len = min_t(size_t, strsz, HO_MAP_LEN);
			str = early_memremap(copy + strtb, str_len);
			if (!str) {
				pr_warn("cannot map %zu bytes of its strings at %pa\n",
					str_len, &copy);
				n = -1;
			} else {
				n = scan_mem_ranges(map, map_len, str, str_len,
						    m, HO_MEMBLK_MAX);
				if (n > 0 && !memblk_sane(m, n, &r, dt_phys,
							   fdt_totalsize(our_dtb)))
					n = -1;
			}
		}
	}
	if (str)
		early_memunmap(str, str_len);
	early_memunmap(map, map_len);

	if (n < 0) {
		pr_warn("no usable /memory in the first %zu bytes of the bootloader tree, keeping the built-in literal\n",
			(size_t)HO_MAP_LEN);
		return;
	}
	if (!n) {
		pr_notice("bootloader tree declares no memory, keeping the built-in literal\n");
		return;
	}

	graft_mem_ranges(our_dtb, m, n);
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

	if (find_region(initial_boot_params, r)) {
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

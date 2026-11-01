/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * flame (Xiaomi Redmi 14C 5G / SM4450) handover shim.
 *
 * The bootloader loads the kernel section of the boot image and jumps to its
 * first instruction with x0 pointing at the DTB it assembled from the *vendor*
 * device tree. That DTB only binds vendor drivers, so mainline cannot use it:
 * arm64 always prefers a valid DTB in x0 over anything built into the Image.
 * This shim replaces it. It is entered under the same conditions the stock
 * kernel is (EL1, MMU and D-cache off) and therefore
 *
 *   - is position independent: everything is derived from `adr`, there are no
 *     relocations and no absolute addresses baked into the code,
 *   - copies the bootloader's DTB into the handover region named in the header
 *     before anything can overwrite it, because that DTB's /chosen/bootargs is
 *     where the bootloader injected androidboot.slot_suffix and the AVB tags,
 *     and Android's init cannot find the ROM without them,
 *   - copies the mainline DTB and the Image out of the payload to fixed
 *     2 MiB-aligned addresses, overlap-safe in case the loader happened to
 *     place the payload on top of a destination,
 *   - cleans every copy to the Point of Coherency and invalidates the
 *     instruction cache over the Image, as Documentation/arch/arm64/booting.rst
 *     requires of a bootloader,
 *   - jumps to the Image with x0 = our DTB, x1..x3 = 0, x30 = 0.
 *
 * Payload layout (written by tools/mklinuxpayload.py, offsets are payload
 * relative and the header sits one page below this code):
 *
 *   image_off               Image, first instruction patched to `b shim_off`
 *                           so the loader's jump lands here
 *   dtb_off                 mainline .dtb
 *   shim_off - 0x1000       header block (see HDR in tools/mklinuxpayload.py)
 *   shim_off                this code
 *
 * Handover region (reserved in the board DTS *without* no-map, so the kernel
 * reads it through the linear map as soon as paging_init has run):
 *
 *   +0x0000  record: magic u64 "FLAMHNDV", version u32 1, flags u32,
 *            vendor dtb phys u64, copy phys u64, copy size u32, rfu u32
 *   +0x1000  verbatim copy of the bootloader's DTB, at most 2 MiB - 4 KiB
 *
 * Read by arch/arm64/kernel/handover.c, called from setup_arch under
 * CONFIG_ARM64_HANDOVER_CMDLINE. If x0 did not hold a flattened tree, or the
 * tree overlaps the region, the record says "nothing copied" and the kernel
 * boots with the command line from our own DTB. If the region itself overlaps
 * the payload still being read, or is missing, nothing is written there at
 * all: the record would land on bytes the Image copy has not read yet.
 *
 * If a destination would cover this code or its header page - or, for the DTB,
 * the Image's not-yet-copied source bytes - copying would overwrite
 * instructions still being executed or the kernel we are about to boot, so we
 * spin instead. The device is still recoverable by flashing the ROM's own boot
 * image back.
 */

	.text
	.globl	_start

_start:
	/* Self-referential `adr` keeps this position independent and needs no
	 * relocation: 1b is the offset of this very instruction. */
1:	adr	x19, 1b
	mov	x17, x0			/* the bootloader's dtb, before we replace it */
	sub	x20, x19, #0x1000		/* header page */

	ldr	w21, [x20, #16]		/* image_off */
	ldr	w22, [x20, #20]		/* image_size */
	ldr	w23, [x20, #24]		/* dtb_off */
	ldr	w24, [x20, #28]		/* dtb_size */
	ldr	x25, [x20, #32]		/* image_load */
	ldr	x26, [x20, #40]		/* dtb_load */
	ldr	w27, [x20, #48]		/* entry_off */
	ldr	w28, [x20, #52]		/* shim_off */
	ldr	x11, [x20, #64]		/* handover region */

	/* Trust nothing in the header page before using it as a block size and a
	 * destination. A damaged page must not become a 4 GiB memmove into an
	 * arbitrary physical address. */
	ldr	x8, [x20]			/* magic, "SWEETLNX" little endian */
	movz	x9, #0x5753
	movk	x9, #0x4545, lsl 16
	movk	x9, #0x4c54, lsl 32
	movk	x9, #0x584e, lsl 48
	cmp	x8, x9
	b.ne	halt
	ldr	w8, [x20, #8]		/* abi version */
	cmp	w8, #2
	b.ne	halt
	mov	x9, #0x20000000		/* 512 MiB: far past any Image we build */
	cmp	x22, x9
	b.hs	halt
	mov	x9, #0x1000000		/* 16 MiB: ABL's merged tree is about 1 MiB */
	cmp	x24, x9
	b.hs	halt

	sub	x19, x19, x28		/* x19 = payload base */

	/* Two overlaps are fatal, and the ordering of the copies decides which:
	 *
	 *   a destination covering the header page or this code, because the copy
	 *   would rewrite instructions that are still executing;
	 *   the DTB destination covering the Image's source bytes, because the
	 *   DTB moves first and the Image copy reads that source afterwards.
	 *
	 * A destination covering its own source is not an error (memmove picks a
	 * direction), and the Image destination covering the DTB's source is not
	 * either, because that source has already been read. The payload sitting
	 * on top of image_load is the normal case, not an error.
	 *
	 * The handover region against both destinations is not checked here: it
	 * is checked at pack time (tools/mklinuxpayload.py refuses a payload whose
	 * destinations touch it), and the worst case is a lost command line
	 * rather than a corrupt boot, so degrading quietly beats halting. */
	add	x9, x19, x28
	sub	x9, x9, #0x1000		/* our span starts: header page */
	add	x10, x19, x28
	add	x10, x10, #0x1000	/* our span ends: after this code */
	cmp	x25, x10		/* Image dst vs our span */
	b.hs	.Ldest_dtb
	add	x16, x25, x22
	cmp	x9, x16
	b.lo	halt
.Ldest_dtb:
	cmp	x26, x10		/* DTB dst vs our span */
	b.hs	.Ldest_src
	add	x16, x26, x24
	cmp	x9, x16
	b.lo	halt
.Ldest_src:
	add	x5, x19, x21
	add	x6, x5, x22		/* Image source, still to be read */
	cmp	x26, x6			/* DTB dst vs that source */
	b.hs	.Ldest_ok
	add	x16, x26, x24
	cmp	x5, x16
	b.lo	halt
.Ldest_ok:
	/* Handover before anything moves: the Image copy may well land on the
	 * tree the bootloader parked in low memory. */
	bl	handover

	/* DTB first: the Image copy is allowed to trash the payload behind it. */
	add	x1, x19, x23
	mov	x0, x26
	mov	x2, x24
	bl	memmove

	add	x1, x19, x21
	mov	x0, x25
	mov	x2, x22
	bl	memmove

	mov	x0, x26
	mov	x1, x26
	add	x1, x1, x24
	bl	dcache_clean
	mov	x0, x25
	mov	x1, x25
	add	x1, x1, x22
	bl	dcache_clean
	dsb	sy
	mov	x0, x25
	mov	x1, x25
	add	x1, x1, x22
	bl	icache_inval
	dsb	sy
	isb

	mov	x0, x26			/* x0 = our dtb */
	mov	x1, xzr
	mov	x2, xzr
	mov	x3, xzr
	mov	x30, xzr
	/* primary_entry sets its own stack after two leaf calls, but booting.rst
	 * wants a valid sp on entry. Use the tail of the section we were loaded
	 * into: RAM the loader gave us, above the Image source that has already
	 * been copied out, and no longer holding live code. Not the handover
	 * region: with a vendor DTB near 2 MiB its top is inside the copy. */
	add	x8, x19, x28
	add	x8, x8, #0x1000		/* end of our loaded section */
	bic	x8, x8, #15		/* 16 byte aligned */
	mov	sp, x8
	add	x8, x25, x27		/* primary_entry = image_load + entry_off */
	br	x8

halt:
	b	halt

/*
 * handover: record what was in x0 and keep a copy of it.
 * x11 = region, x17 = bootloader dtb as passed (possibly junk),
 * x19 = payload base, x28 = shim_off (so the payload ends near x19+x28+0x1000).
 * Uses x4..x10 only; x19..x28 are the caller's state.
 */
handover:
	mov	x10, x30			/* every bl below overwrites the link
					 * register, and a `ret` sitting at the
					 * address its own last bl returns to
					 * would jump to itself forever. Keep the
					 * caller's address out of reach in x10. */
	mov	x8, xzr			/* copy size, 0 until proven otherwise */
	mov	x9, xzr			/* copy address */

	/* First, and before anything is written down: is the region somewhere we
	 * are still standing on? The record goes into the region's first page
	 * whether or not the copy succeeds, so a region that overlaps the payload
	 * would have its not-yet-copied Image bytes - or this very code - replaced
	 * by "FLAMHNDV". Refuse the whole handover in that case and return
	 * without touching a byte; the kernel reads a stale region, finds no
	 * magic, and boots with our own command line. Losing the androidboot tags
	 * is recoverable by the user flashing the ROM's image back; overwriting
	 * the kernel is not.
	 *
	 * The region also has to be real: zero (an older payload) or a base that
	 * is not 2 MiB aligned (the record stores 8 byte values into it, and an
	 * unaligned store with the MMU off is an alignment fault with no handler).
	 */
	cmp	x11, xzr
	b.eq	.Lhv_ret
	tst	x11, #0x1fffff
	b.ne	.Lhv_ret
	add	x4, x19, x28
	add	x4, x4, #0x1000		/* payload end, with slack for this code */
	add	x5, x11, #0x200000	/* region end */
	cmp	x11, x4
	b.hs	.Lhv_tree		/* region starts at/after payload end */
	cmp	x19, x5
	b.hs	.Lhv_tree		/* payload starts at/after region end */
.Lhv_ret:
	mov	x30, x10
	ret

.Lhv_tree:
	/* Reading the header at all is a risk: with the MMU off an unaligned
	 * 32-bit load takes an alignment fault, and there is no handler yet. A
	 * real FDT is at least 4 byte aligned, so anything else is not a tree. */
	tst	x17, #3
	b.ne	.Lhv_record
	/* Nor may a wild pointer be dereferenced: without a handler it faults the
	 * boot. This is a coarse plausibility bound, not a memory map - SM4450
	 * DRAM starts at 0x80000000 and no part sold with this SoC puts a tree
	 * above 8 GiB, while the floor still admits the qemu virt RAM the test
	 * boots in. A pointer outside it is ABL handing us junk, and a record
	 * saying "nothing copied" is the right answer for junk. */
	mov	x4, #0x40000000
	cmp	x17, x4
	b.lo	.Lhv_record
	mov	x4, #0x200000000
	cmp	x17, x4
	b.hs	.Lhv_record

	/* flattened tree magic, big endian at +0 */
	ldr	w5, [x17]
	rev	w5, w5
	movz	w4, #0xfeed
	movk	w4, #0xd00d, lsl 16
	cmp	w5, w4
	b.ne	.Lhv_record

	ldr	w5, [x17, #4]		/* totalsize, big endian */
	rev	w5, w5
	mov	x8, x5
	mov	x4, #40
	cmp	x8, x4
	b.lo	.Lhv_none		/* smaller than a header: not a tree */
	mov	x4, #0x1ff000		/* region is 2 MiB, one page of record */
	cmp	x8, x4
	b.ls	.Lhv_span
	mov	x8, x4			/* copy what fits; the kernel checks the
					 * header against the real totalsize */
.Lhv_span:
	/* Would the copy land on the tree being read? The region being clear of
	 * the payload was settled at entry, and a destination that lands on the
	 * Image or DTB is harmless in the other direction: those copies have
	 * already happened by then and the packer forbids it anyway. So this is
	 * the one case where copying would read garbage, and the answer is to
	 * record "nothing copied" rather than to try. */
	add	x5, x17, x8		/* source end */
	add	x7, x11, #0x200000	/* region end */
	cmp	x11, x5
	b.hs	.Lhv_copy		/* region starts at/after src end */
	cmp	x17, x7
	b.hs	.Lhv_copy		/* src starts at/after region end */
	b	.Lhv_none
.Lhv_copy:
	add	x0, x11, #0x1000	/* dst = region + record page */
	mov	x1, x17
	mov	x2, x8
	bl	memmove
	mov	x9, #0x1000
	add	x9, x9, x11		/* where it went */
	b	.Lhv_record

.Lhv_none:
	mov	x8, xzr
	mov	x9, xzr

.Lhv_record:
	/* "FLAMHNDV". Built in registers, not loaded from a literal: with the
	 * MMU off an 8-byte load from an address that is only 4-byte aligned
	 * takes an alignment fault, and that is exactly what a .quad stashed in
	 * the middle of this code turns out to be. */
	movz	x4, #0x4c46
	movk	x4, #0x4d41, lsl 16
	movk	x4, #0x4e48, lsl 32
	movk	x4, #0x5644, lsl 48
	str	x4, [x11]		/* magic */
	mov	x4, #1
	str	w4, [x11, #8]		/* version */
	str	wzr, [x11, #12]		/* flags */
	str	x17, [x11, #16]		/* the tree as it was passed to us */
	str	x9, [x11, #24]		/* the copy, 0 if there is none */
	str	w8, [x11, #32]		/* its length, 0 if there is none */
	str	wzr, [x11, #36]

	/* Clean the copy, then the record page. Both go through bl, and the
	 * return below re-points x30 at the caller first. */
	cbz	x8,	.Lhv_clean_hdr
	mov	x0, x9
	add	x1, x9, x8
	bl	dcache_clean
.Lhv_clean_hdr:
	mov	x0, x11
	mov	x1, x11
	add	x1, x1, #0x1000
	bl	dcache_clean
	mov	x30, x10
	ret

/*
 * memmove: x0 = dst, x1 = src, x2 = len. Uses x3..x9, so the state held in
 * x19..x28 survives.
 *
 * One byte at a time on purpose: with the MMU off an unaligned pair access
 * takes an alignment fault, and neither the payload base (chosen by the
 * bootloader) nor the lengths are guaranteed to be congruent. Copying the
 * ~40 MB Image this way costs well under 100 ms on the A55/A78 cluster.
 */
memmove:
	cbz	x2, .Lret
	cmp	x0, x1
	b.ls	.Lfwd
.Lb_byte:					/* dst > src: walk backwards, */
	subs	x2, x2, #1			/* indexing down from the base */
	b.mi	.Lret
	ldrb	w3, [x1, x2]
	strb	w3, [x0, x2]
	b	.Lb_byte
.Lfwd:
	mov	x9, xzr				/* dst <= src: walk forwards */
.Lf_byte:
	ldrb	w3, [x1, x9]
	strb	w3, [x0, x9]
	add	x9, x9, #1
	cmp	x9, x2
	b.lt	.Lf_byte
.Lret:
	ret

/* dcache_clean: x0 = start, x1 = end (exclusive). Clean+invalidate to PoC. */
dcache_clean:
	bic	x0, x0, #63
.Ldc:
	dc	civac, x0
	add	x0, x0, #64
	cmp	x0, x1
	b.lo	.Ldc
	ret

/* icache_inval: x0 = start, x1 = end (exclusive). Invalidate to PoU.
 * IC IVAC is deprecated (and rejected by current binutils); with the MMU off
 * VA == PA, so IVAU addresses the same lines. */
icache_inval:
	bic	x0, x0, #63
.Lic:
	ic	ivau, x0
	add	x0, x0, #64
	cmp	x0, x1
	b.lo	.Lic
	ret

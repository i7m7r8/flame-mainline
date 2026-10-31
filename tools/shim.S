/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * flame (Xiaomi Redmi 14C 5G / SM4450) handover shim.
 *
 * The bootloader loads the kernel section of boot_b and jumps to its first
 * instruction with x0 pointing at the DTB it assembled from the *vendor*
 * device tree. That DTB only binds vendor drivers, so mainline cannot use it:
 * arm64 always prefers a valid DTB in x0 over anything built into the Image.
 * This shim replaces it. It is entered under the same conditions the stock
 * kernel is (EL1, MMU and D-cache off) and therefore
 *
 *   - is position independent: everything is derived from `adr`, there are no
 *     relocations and no absolute addresses baked into the code,
 *   - copies the mainline DTB and the Image out of the payload to fixed
 *     2 MiB-aligned addresses, overlap-safe in case the loader happened to
 *     place the payload on top of a destination,
 *   - cleans both copies to the Point of Coherency and invalidates the
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
 *   shim_off - 0x1000       header block (see HDR_* in tools/mklinuxpayload.py)
 *   shim_off                this code
 *
 * If the Image destination would cover this code or its header page, copying
 * would overwrite instructions still being executed, so we spin instead of
 * corrupting memory. The device is still recoverable with
 * `fastboot --set-active=a`.
 */

	.text
	.globl	_start

_start:
	/* Self-referential `adr` keeps this position independent and needs no
	 * relocation: 1b is the offset of this very instruction. */
1:	adr	x19, 1b
	sub	x20, x19, #0x1000		/* header page */

	ldr	w21, [x20, #16]		/* image_off */
	ldr	w22, [x20, #20]		/* image_size */
	ldr	w23, [x20, #24]		/* dtb_off */
	ldr	w24, [x20, #28]		/* dtb_size */
	ldr	x25, [x20, #32]		/* image_load */
	ldr	x26, [x20, #40]		/* dtb_load */
	ldr	w27, [x20, #48]		/* entry_off */
	ldr	w28, [x20, #52]		/* shim_off */

	ldr	w8, [x20, #8]		/* abi version */
	cmp	w8, #1
	b.ne	halt

	sub	x19, x19, x28		/* x19 = payload base */

	/* Is this code inside the Image destination range? */
	add	x9, x19, #0x1000
	add	x9, x9, #0x100		/* code + header page end */
	add	x10, x25, x22		/* image dst end */
	cmp	x25, x9
	b.hs	.Ldest_ok
	cmp	x10, x19
	b.hi	halt
.Ldest_ok:
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
	mov	sp, x25			/* inside the Image; the kernel sets its own */
	add	x8, x25, x27
	br	x8

halt:
	b	halt

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

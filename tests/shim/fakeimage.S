/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Stand-in for arch/arm64/boot/Image, used by tests/shim/run.sh.
 *
 * It has the same first-64-bytes shape as a real kernel image so that
 * tools/mklinuxpayload.py treats it identically (the efi_signature_nop that
 * CONFIG_EFI arm64 Images open with, the branch to the entry next, u64
 * text_offset at 0x8, u64 image_size at 0x10, magic u32 at 0x38), and it prints
 * what the shim must have gotten right by the time the kernel is entered:
 *
 *   "SHIM-ENTRY"    the patched first instruction reached the shim, and the
 *                   shim jumped back into the relocated copy
 *   "FDT-OK"        x0 points at the DTB the shim copied (FDT magic checked)
 *   "COPY-OK"       the 64-bit marker inside the image survived the memmove
 *   "HAND-OK"       the handover record is there, names a copy of the tree the
 *                   loader passed in x0, and that copy starts with an FDT
 *
 * SCRATCH has to match --scratch-load in run.sh.
 */

	.text
	.set SCRATCH, 0x43000000

	/* ccmp x18, #0, #0xd, pl: what arch/arm64/kernel/efi-header.S calls
	 * efi_signature_nop. It only touches the flags, and its opcode spells
	 * "MZ" for UEFI loaders. The packer keeps it and patches the branch
	 * after it, so the test image has to carry it too. */
	.long	0xfa405a4d
	b	.Lentry

	.org	0x8
	.quad	0			/* text_offset: the file starts at the
					 * 2 MiB aligned base the shim copies to,
					 * and mklinuxpayload insists on that */
	.org	0x10
	.quad	0			/* image_size: 0, so qemu loads the whole
					 * section instead of trusting a size */
	.org	0x38
	.long	0x644d5241		/* ARM64_IMAGE_MAGIC "ARM\x64" */

	.org	0x100
.Lmarker:
	.quad	0xdeadbeefcafebabe
	.org	0x108
.Lhand_magic:
	.quad	0x56444e484d414c46	/* "FLAMHNDV", the shim's record magic */
	.org	0x110
.Lmark:
	.ascii	"SM4450ABL"		/* must match vendor.dts */

	.org	0x200
.Lmsg_entry:
	.asciz	"SHIM-ENTRY "
.Lmsg_fdt_ok:
	.asciz	"FDT-OK "
.Lmsg_fdt_bad:
	.asciz	"FDT-BAD "
.Lmsg_copy_ok:
	.asciz	"COPY-OK "
.Lmsg_copy_bad:
	.asciz	"COPY-BAD "
.Lmsg_hand_ok:
	.asciz	"HAND-OK\n"
.Lmsg_hand_bad:
	.asciz	"HAND-BAD\n"

	.org	0x400
.Lentry:
	mov	x20, x0			/* what the shim passed as the dtb */
	mov	x21, #SCRATCH
	adr	x0, .Lmsg_entry
	bl	.Lputs

	/* the FDT magic is big endian: d0 0d fe ed */
	ldrb	w8, [x20]
	cmp	w8, #0xd0
	b.ne	.Lfdt_bad
	ldrb	w8, [x20, #1]
	cmp	w8, #0x0d
	b.ne	.Lfdt_bad
	ldrb	w8, [x20, #2]
	cmp	w8, #0xfe
	b.ne	.Lfdt_bad
	ldrb	w8, [x20, #3]
	cmp	w8, #0xed
	b.ne	.Lfdt_bad
	adr	x0, .Lmsg_fdt_ok
	b	.Lfdt_print
.Lfdt_bad:
	adr	x0, .Lmsg_fdt_bad
.Lfdt_print:
	bl	.Lputs

	adr	x10, .Lmarker
	ldr	x11, [x10]
	mov	x12, #0xbabe
	movk	x12, #0xcafe, lsl 16
	movk	x12, #0xbeef, lsl 32
	movk	x12, #0xdead, lsl 48
	cmp	x11, x12
	b.ne	.Lcopy_bad
	adr	x0, .Lmsg_copy_ok
	b	.Lcopy_print
.Lcopy_bad:
	adr	x0, .Lmsg_copy_bad
.Lcopy_print:
	bl	.Lputs

	/* record: magic u64 @0, version u32 @8, vendor dtb u64 @16,
	 * copy u64 @24, copy size u32 @32 */
	ldr	x8, [x21]
	adr	x9, .Lhand_magic
	ldr	x9, [x9]
	cmp	x8, x9
	b.ne	.Lhand_bad
	ldr	w8, [x21, #8]
	cmp	w8, #1
	b.ne	.Lhand_bad
	ldr	x8, [x21, #16]		/* the tree we were entered with */
	cmp	x8, x20
	b.eq	.Lhand_bad		/* must not be the one we replaced */
	cbz	x8, .Lhand_bad
	ldr	x13, [x21, #24]		/* the copy; kept in x13 because the
					 * size check below would otherwise
					 * clobber the pointer it then follows */
	mov	x9, #SCRATCH
	add	x9, x9, #0x1000
	cmp	x13, x9
	b.ne	.Lhand_bad
	ldr	w8, [x21, #32]
	cmp	w8, #40
	b.lo	.Lhand_bad
	cmp	w8, #0x1ff000		/* never more than the region can hold */
	b.hi	.Lhand_bad
	ldrb	w9, [x13]		/* the copy really is an FDT */
	cmp	w9, #0xd0
	b.ne	.Lhand_bad
	ldrb	w9, [x13, #1]
	cmp	w9, #0x0d
	b.ne	.Lhand_bad
	ldrb	w9, [x13, #2]
	cmp	w9, #0xfe
	b.ne	.Lhand_bad
	ldrb	w9, [x13, #3]
	cmp	w9, #0xed
	b.ne	.Lhand_bad

	/* The tree has to be the loader's own, not merely some FDT: scan the
	 * copy for the marker vendor.dts hides in /chosen/bootargs. */
	mov	x14, x13
	add	x15, x13, x8		/* end of the copy */
.Lscan:
	add	x9, x14, #9
	cmp	x9, x15
	b.hi	.Lhand_bad		/* marker cannot fit: it is not here */
	mov	x16, xzr
.Lscan_inner:
	ldrb	w10, [x14, x16]
	adr	x11, .Lmark
	add	x11, x11, x16
	ldrb	w12, [x11]
	cmp	w10, w12
	b.ne	.Lscan_next
	add	x16, x16, #1
	cmp	x16, #9
	b.lt	.Lscan_inner
	b	.Lscan_found		/* all nine bytes matched */
.Lscan_next:
	add	x14, x14, #1
	b	.Lscan
.Lscan_found:
	adr	x0, .Lmsg_hand_ok
	b	.Lhand_print
.Lhand_bad:
	adr	x0, .Lmsg_hand_bad
.Lhand_print:
	bl	.Lputs

1:	b	1b			/* the runner times us out */

/* .Lputs: x0 = asciiz. QEMU virt PL011 is at 0x9000000, the smoke test target. */
.Lputs:
	mov	x1, #0x9000000
	stp	x29, x30, [sp, #-16]!
	mov	x29, sp
2:	ldrb	w3, [x0], #1
	cbnz	w3, 3f
	ldp	x29, x30, [sp], #16
	ret
3:	strb	w3, [x1]
	b	2b

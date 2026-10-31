/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Stand-in for arch/arm64/boot/Image, used by tests/shim/run.sh.
 *
 * It has the same first-64-bytes shape as a real kernel image so that
 * tools/mklinuxpayload.py treats it identically (branch at 0, text_offset,
 * image_size, magic at 0x18), and it prints what the shim must have gotten
 * right by the time the kernel is entered:
 *
 *   "SHIM-ENTRY"    the patched first instruction reached the shim, and the
 *                   shim jumped back into the relocated copy
 *   "FDT-OK"        x0 points at the DTB the shim copied (FDT magic checked)
 *   "COPY-OK"       the 64-bit marker inside the image survived the memmove
 */

	.text
	b	.Lentry

	.org	0x8
	.long	0			/* text_offset: 0 = "no EFI stub", which
					 * is also what makes qemu and ABL load
					 * the whole section instead of only
					 * the Image proper */
	.org	0x10
	.quad	0			/* image_size */
	.org	0x18
	.long	0x644d5241		/* ARM64_IMAGE_MAGIC "ARM\x64" */
	.org	0x100
.Lmarker:
	.quad	0xdeadbeefcafebabe
	.org	0x200
.Lmsg_entry:
	.asciz	"SHIM-ENTRY "
.Lmsg_fdt_ok:
	.asciz	"FDT-OK "
.Lmsg_fdt_bad:
	.asciz	"FDT-BAD "
.Lmsg_copy_ok:
	.asciz	"COPY-OK\n"
.Lmsg_copy_bad:
	.asciz	"COPY-BAD\n"
.Lnl:
	.asciz	"\n"

	.org	0x400
.Lentry:
	mov	x20, x0			/* what the shim passed as the dtb */
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

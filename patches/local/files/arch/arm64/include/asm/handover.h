/* SPDX-License-Identifier: GPL-2.0 */
/*
 * flame: what the EL1 handover shim left behind for the kernel to pick up.
 * See arch/arm64/kernel/handover.c and tools/shim.S.
 */

#ifndef __ASM_ARM64_HANDOVER_H
#define __ASM_ARM64_HANDOVER_H

#include <linux/init.h>
#include <linux/types.h>

#ifdef CONFIG_ARM64_HANDOVER_CMDLINE
void __init handover_cmdline_extend(void);
void __init handover_memory_fixup(void *our_dtb, phys_addr_t dt_phys);
#else
static inline void __init handover_cmdline_extend(void) { }
static inline void __init handover_memory_fixup(void *our_dtb,
						phys_addr_t dt_phys) { }
#endif

#endif /* __ASM_ARM64_HANDOVER_H */

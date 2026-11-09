/*
 * ps4-kexec - a kexec() implementation for Orbis OS / FreeBSD
 * Copyright (C) 2015-2016 shuffle2 <godisgovernment@gmail.com>
 * Copyright (C) 2015-2016 Hector Martin "marcan" <marcan@marcan.st>
 * Copyright (C) 2026 Meerzulee and Linux psX contributors.
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Physical descriptors are fully validated before arming. Source/control pages
 * consumed here never intersect an immediate copy destination. Later kernel
 * decompressor workspace may overlap staging: all records finish before entry.
 * Copying starts only after the existing terminal CPU/device quiesce.
 */
.intel_syntax noprefix
.text
#ifdef DIAGNOSTIX_HOST_COPY
.globl diag_scatter_copy_host
diag_scatter_copy_host:
    push rbx
    push r12
    push r13
    push r14
    push r15
    mov r12, rdi
    mov r10, rsi
#else
.globl jmp_to_linux
jmp_to_linux:
    # head=rdi, bootparams=rsi, new_cr3=rdx, gdt_ptr=rcx, records=r8
    cli
    lgdt [rcx]
    mov eax, 0x18
    mov ds, eax
    mov ss, eax
    mov es, eax
    mov fs, eax
    mov gs, eax
    mov r12, rdi
    mov r13, rsi
    mov r10, r8
    mov cr3, rdx
    lea rsp, [rip + jmp_to_linux_end + 0x200]
    and rsp, -0x10
#endif
    cld
    test r10, r10
    jz .Lrefuse
    cmp r10, 40000
    ja .Lrefuse
.Lnode:
    test r12, r12
    jz .Ldone
    mov r15, [r12]
    mov rbx, [r12 + 8]
    test rbx, rbx
    jz .Lrefuse
    cmp rbx, 682
    ja .Lrefuse
    lea r14, [r12 + 16]
.Lrecord:
    test r10, r10
    jz .Lrefuse
    dec r10
    mov rsi, [r14]
    mov rdi, [r14 + 8]
    mov rcx, [r14 + 16]
    test rcx, rcx
    jz .Lrefuse
    cmp rcx, 0x4000
    ja .Lrefuse
    test rsi, rsi
    jz .Lzero
    rep movsb
    jmp .Lnext
.Lzero:
    xor eax, eax
    rep stosb
.Lnext:
    add r14, 24
    dec rbx
    jnz .Lrecord
    mov r12, r15
    jmp .Lnode
.Ldone:
    test r10, r10
    jnz .Lrefuse
#ifdef DIAGNOSTIX_HOST_COPY
    xor eax, eax
    jmp .Lreturn
.Lrefuse:
    mov eax, 1
.Lreturn:
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    ret
#else
    push 0x10
    push 0x06000200
    mov rsi, r13
    lretq
.Lrefuse:
    hlt
    jmp .Lrefuse
jmp_to_linux_end:
.data
.globl jmp_to_linux_size
jmp_to_linux_size: .quad jmp_to_linux_end - jmp_to_linux
#endif
.section .note.GNU-stack,"",@progbits
.att_syntax prefix

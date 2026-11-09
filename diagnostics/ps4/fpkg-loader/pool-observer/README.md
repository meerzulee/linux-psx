# Native memory pool report

A separate manual ELF/BIN report for exact FW12.02 and Baikal B1. This is not a
Linux loader. It does not request a reboot, shutdown, allocation, mapping, hook,
syscall installation, permission change or device access. No boot files are
read. No queue links or page-array pointers are followed. Loader06 is unchanged.

The operator handoff must call this a **MEMORY REPORT that leaves Orbis running**.
Results use private UART. No on-screen notification is promised: the upstream
notification helper loads a module, so it is deliberately not linked. Entry and
completion text is also written to fd1, whose visibility in PayloadGuest is not
established. There are no filesystem report writes.

The pinned public library's `kexec` wrapper directly invokes existing syscall11;
it does not run the loader's `kernel_main`. The zero-argument callback ignores
GoldHEN thread/argument registers and stores only in owned static payload memory.
The payload contains no syscall153 installer or kernel bootstrap patches. Kernel
code uses no red zone, SSE/general floating-point registers, libc or TLS. Native
`printf` is used for bounded UART output after two code fingerprints match.
The already-installed GoldHEN syscall11 facility and trusted exact native kernel
context are prerequisites; host testing cannot qualify that runtime mechanism.

User entry first checks raw firmware1202 and the read-only
`hw.sce_subsys_subid` value0x30201. Its fixed name-to-OID query uses syscall202
with `{0,3}`, then a value read with no replacement data. This follows FreeBSD9.3
`sysctlnametomib`; it avoids dynamic module loading. Unknown query behavior
refuses before invoking the kernel callback.

Two fixed32-byte FNV-1a64 windows identify compatibility with the reviewed native
profile. These are compatibility checks, not security attestation. All subsequent
reads are a fixed allowlist: segment count, up to31 forty-byte normal segments,
active-free-list byte, active normal head pointers, one special segment, profile
and GameDMEM scalars, and u32 count fields at offset16 in fixed24-byte queue
records. Normal and special results are labeled separately. At maximum there
are276 bounded reads; reports use one13-count row per selector/pool. Pointers
are printed as metadata, never dereferenced. Native reads are not fault-contained.

The counters are an approximate unlocked snapshot and can change during the
report. Segment membership, nonzero counters and the highest populated queue
order do not prove allocation eligibility or the largest contiguous extent:
orders beyond12 can combine adjacent64MiB blocks. No runtime availability,
ownership, draining or allocator-success claim follows from this report.

`build.py --loader-tree <pinned-clean-source> --output <new-directory> --yasm
<existing-yasm>` compiles only the observer, upstream firmware reader, CRT and
syscall wrappers, then emits `pool-observer.elf` and `pool-observer.bin`. The BIN
includes its complete static/BSS extent. Native per-function stack use is
recorded. The full pinned public source and observer source are retained.

Host fixtures compile the actual collector and entry. Synthetic fingerprint
expectations exist only behind `PSX_POOL_HOST_TEST`, never in the native build.
They verify exact read locations, both queue classes, every276 read-failure
boundary, wrong firmware/base/hash refusal before metadata, inactive-list and
31-record bounds, endian decoding and zeroed failure output. Entry fixtures
never invoke syscall11, RDMSR, native memory reads or host sysctl. No console
operation or deployment is part of this build.

Source basis: pinned ps4-linux-loader commit
`9acef9fbf79097a2bb39d6c9c17228198bc445cc`, original notices retained; FreeBSD's
[sysctlnametomib interface](https://github.com/freebsd/freebsd-src/blob/releng/9.3/lib/libc/gen/sysctlnametomib.c).
Only symbol offsets, scalar widths and hashes derived from the owner's private
native review are included. No proprietary firmware bytes are embedded.

# PS4 dynamic RAM and VRAM allocation

Recorded from the project owner's idea on 2026-10-19. Status: proposed research;
no memory-layout change is included in the completed UVD token test.

## The idea

Use the PS4's shared physical memory as a flexible pool for Linux applications
and GPU workloads, similar in purpose to unified memory on Apple silicon.
Keep only the fixed reservations required by the hardware, preserve enough
memory for Linux to remain responsive, and allocate the remaining memory to
CPU or GPU workloads as needed instead of fixing a large RAM/VRAM split at boot.

The Linux-oriented implementation would let the kernel manage most usable
pages. AMDGPU would allocate, map and retain GPU buffers from that pool, then
make their backing pages reusable after GPU access has safely finished.
Linux headroom is a memory-budget policy; it need not be another permanently
partitioned pool. The same physical page must never be independently allocated
to unrelated CPU and GPU owners.

## DDR3 and the actual PS4 memory pools

The tested PS4 Slim with Baikal B1 has **8 GiB of GDDR5 as one unified pool**;
it does not have a separate DDR3 pool. The base PS4 and Slim use the same
GDDR5 for CPU and GPU. The PS4 Pro adds a separate **1 GiB DDR3** pool for
background/system tasks alongside its 8 GiB GDDR5; that DDR3 is not a general
game-memory pool and its Linux exposure has not been tested here.

`vram.txt = 1024` is therefore not a second physical memory type. It is a
**boot-time carve-out from the single GDDR5 pool**: the loader excludes it
from Linux's E820 map and gives it to the GPU framebuffer/VRAM path. Linux
sees roughly 6.8 GiB of the remaining pool. The carve-out is why changing only
`amdgpu.vramlimit` cannot return memory to Linux.

## Practical dynamic-memory strategy

There are three different goals that are easy to conflate:

1. **Return unused fixed VRAM to Linux.** This needs the loader's E820
   reservation, GPU framebuffer window, MTRRs and driver assumptions to agree
   on a smaller reservation. It is a boot-time change, not a runtime resize.
2. **Let GPU buffers come from system pages on demand.** AMDGPU already maps
   GTT/GPUVM buffers backed by system memory and TTM can evict buffers under
   pressure. This can make GPU allocations more flexible without moving the
   physical VRAM boundary.
3. **True runtime resizing of the physical pool.** There is no production
   mechanism for this today. It would require a custom loader/driver contract
   and careful handling of display scanout, page tables, rings, firmware and
   pinned buffers. HMM/SVM or a custom carveout driver may be research paths,
   not a configuration switch.

The lowest-risk progression is:

1. Measure the current 1 GiB carve-out, AMDGPU VRAM/GTT heaps, display
   framebuffer, UVD and page-table residency.
2. Try a smaller fixed carve-out (512 MiB first, then 256 MiB only if needed)
   as one boot variable, with the 1 GiB set as rollback.
3. Keep only display/scanout and required resident structures in VRAM; use
   GTT/GPUVM for ordinary GPU buffers. Verify display, OpenGL, Vulkan, UVD and
   memory pressure before increasing the shared pool.
4. Only then consider a custom dynamic carveout or HMM/SVM design. Keep Linux
   headroom as a policy/budget, not a second permanent partition.

This is about Linux applications and Linux games. Native Orbis games are not
Linux binaries and do not become loadable through a RAM/VRAM policy change;
any PS4-game path would be a separate compatibility/emulation project.

## What the current code provides

- The current test boot set uses `vram.txt = 1024`, a 1 GiB fixed reservation.
  The loader excludes that region from Linux's E820 RAM map and programs the
  GPU framebuffer window, reported memory size and CPU cache attributes.
- AMDGPU already allocates system-memory backing pages through TTM and maps
  them for GPU access through GART or GPUVM. This GTT path is the starting point
  for dynamic sharing. Its existence does not establish every PS4 engine's
  correctness, allocation limits or performance.
- The PS4 driver's 512 MiB GART window is not the total amount of system memory
  that GPUVM can map. Aperture size, allocation budget and physical capacity
  are different quantities.
- Changing `amdgpu.vramlimit` alone does not return the loader's reserved VRAM
  to Linux. Any smaller fixed reservation must agree across the loader's RAM
  map, GPU registers, cache attributes and driver assumptions.
- The current configuration disables AMDGPU USERPTR and HSA/KFD. Ordinary
  GEM/GTT buffers remain a separate path; arbitrary shared application pointers
  and a working ROCm stack must not be assumed.

## Development direction

1. Keep the existing 1 GiB reservation and measure the memory heaps and budgets
   exposed by the actual PS4 graphics stack.
2. Demonstrate CPU writes, a small GPU compute operation, completion and CPU
   readback on GTT-backed buffers. Check data correctness and synchronization,
   then allocation behavior beyond the fixed VRAM capacity without exhausting
   Linux memory.
3. After that baseline works, test a smaller loader reservation as one separate
   variable. Verify Linux's reclaimed memory, display operation, GPU compute,
   repeated allocation/release and behavior under memory pressure.
4. Establish a justified fixed reservation and CPU headroom policy from those
   measurements. GPU page tables and other required resident allocations still
   need backing; do not assume zero reserved VRAM will work.

One bounded UART experiment at a time, with the existing boot set as rollback.
This research remains separate from UVD command-transport development.

## Intended benefits and limits

CPU-heavy workloads could use memory that would otherwise sit in a large
fixed VRAM reservation. GPU-heavy workloads could obtain more backing memory
from the same physical pool, subject to Linux's own needs.

LLM inference is a possible consumer through a working GPU compute backend,
such as Vulkan. UVD accelerates video decoding and does not provide LLM
compute. Shared physical memory alone does not promise shared virtual
addresses, automatic cache coherence, zero-copy behavior in every API, or
useful inference speed. Model weights, context state, GPU buffers and Linux
still compete for the machine's finite memory.

## Source references

- Loader checkout: `references/ps4-linux-loader`, commit
  `9acef9fbf79097a2bb39d6c9c17228198bc445cc`; `linux/main-aio.c` reads
  `vram.txt`; `linux/ps4-kexec-common/linux_boot.c` defines the E820 reservation,
  framebuffer configuration, MTRRs and power-of-two reservation assumption.
- Linux source: `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`, with the
  [PS4 Liverpool bridge patch](../platforms/ps4/patches/7.2-ps4-baikal/0300-gpu-liverpool/0001-amdgpu-add-ps4-liverpool-bridge.patch).
  Relevant functions include `amdgpu_ttm_tt_populate`, `amdgpu_ttm_backend_bind`,
  `amdgpu_vm_bo_update`, and the GMC v7 memory/GART setup.
- [Current PS4 configuration](../platforms/ps4/config/7.2-psx-baikal.config).
- AMDGPU memory domains and buffer management:
  https://docs.kernel.org/gpu/amdgpu/driver-core.html
- Sony's PS4 unified-memory specification:
  https://www.sony.com/SonyInfo/IR/news/20130221E.pdf

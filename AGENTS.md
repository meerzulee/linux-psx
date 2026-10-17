# Linux psX contributor instructions

## Current scope

- This repository owns console kernel patches, configs, build/CI tooling,
  artifact identities, provenance and hardware evidence. The active target is
  `6.18-ps4-baikal`; PS5 integration remains planned. `7.2-ps4-baikal` and
  `7.2-ps4` are compile-tested development targets (`docs/PORT-7.2.md`) with
  no hardware acceptance.
- Keep runtime/build inputs distribution-independent. Omarchy setup and boot
  profiles belong to `omarchy-psx`. Do not add a desktop, package manager,
  init-system or network-manager requirement to the kernel or generic artifact
  contract.
- Ethernet needs no userspace adapter: `psx_mts_net` autoloads through its PCI
  alias and its first open publishes (`0700-network-mts/0002`). Preserve one
  first-publication attempt per boot and retained DMA; add no retries, unload
  or reset, and keep the `autoarm=0` gate and the blacklist escape working.
- Read `docs/PS4-PATCHES.md`, `CREDITS.md`, `provenance/ps4-import.json` and
  `docs/UNIVERSAL-KERNEL.md` before changing a patch or making support claims.
- Read `docs/BUILDING.md`, `docs/ARTIFACTS.md`, `docs/ETHERNET.md` and
  `provenance/6.18-ps4-baikal.json` for the active target.
- Read `platforms/ps4/docs/README.md` and `CONTRIBUTING.md` for the migrated
  research and source workflow. `provenance/knowledge-migration.json` records
  historical input dispositions and curated output hashes. Preserve negative
  findings and corrections; old hardware evidence does not qualify a new build.
- Root `targets/*.env` paths resolve from the repository root. Inherited
  `platforms/ps4/targets/` paths still resolve from `platforms/ps4/`; these are
  frozen historical metadata, not active targets. Preserve the original import.
- The active target must not embed or bundle device firmware. Do not select
  the historical firmware-embedding config or silently enable UFW/ultrawide.
- Use the explicit ordered `series` files. Disabled patches and `.candidate`
  files are retained for reference and must stay inactive without new evidence.
- Preserve patch bytes, author headers, copyrights and licenses during imports.
  Record adaptations separately; do not attribute aggregate patches solely to
  their packaging author or silently rewrite a recorded patch hash.
- Pin source commits and record current working locations when old links fail.
  Distinguish direct imports, adaptations and comparison-only references.

## Verification and hardware

- Run `make check` for frozen import checksums, active series/target integrity
  and host contracts. Record adaptations separately whenever inputs change.
- Portable MTS tests compile the original GPL fixture against the header
  extracted from the active patch; keep that linkage rather than testing an
  unrelated copied header. A native C compiler is required for host checks.
- `src/`, `output/` and `dist/` are generated. Keep edits in versioned patches
  and source tools, never in the disposable upstream build tree. Package the
  complete corresponding Linux source, resolved config, notices and committed
  build inputs with public binary artifacts.
- Source inspection, patch application and successful builds do not establish
  hardware support. Existing PS4 evidence has its recorded hardware scope;
  experimental ultrawide and PS5 are not accepted by this import.
- Console operation, payload delivery, storage writes, public releases and
  history rewrites require explicit task authority. Keep raw private logs,
  credentials, firmware binaries and generated kernel artifacts out of Git.
- For consequential hardware work, follow `docs/HARDWARE-TESTING.md`: continuous
  UART, one bounded coherent session, one diagnostic variable, explicit rollback
  and completed continuity for acceptance. Do not require an Omarchy checkout.
- MTS2 has one retained DMA/code generation per boot. Do not unload, reset,
  unbind or rearm a published driver. No safe DMA drain or suspend is claimed.
  Prior EXP016 is DEGRADED. The active patch set passed two native Ethernet
  boots as a local build (EXP-20261017-019/020); CI builds have no hardware
  acceptance.

## Hosts and publication

- `master` is this repository's default development and publication branch.
  Keep CI push/PR filters, release guards and download links on `master`.
- Use native tools on Linux x86-64. On macOS ARM64 use the OrbStack helper;
  never run the kernel build directly on Darwin. Preserve both build paths.
- Use OrbStack exclusively for local containers and Linux VMs. Verify it is
  running and the Docker context is `orbstack` before Docker work.
- Preserve existing local work and inspect live remote refs before handoff.
- Use a GitHub noreply author email for public commits when email privacy is
  enabled. Publishing requires task authority; never disable that protection
  merely to push a commit.
- Provide full raw URLs in operational handoffs, with absolute paths for local
  artifacts and pasteable macOS `open` commands when immediately useful.

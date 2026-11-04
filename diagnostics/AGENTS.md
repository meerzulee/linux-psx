# Optional console diagnostics

Linux psX owns the diagnostic appliance source, frontends, image/media builders,
optional test providers and their tests. Production Omarchy distribution and
desktop policy remain in `omarchy-psx`. The owner explicitly assigned diagnostic
source **and image builds** to this repository on 2026-11-04.

Read the root `AGENTS.md`, `docs/DIAGNOSTICS.md` and
`provenance/diagnostics-import.json`. Keep this optional appliance independent
of an Omarchy source checkout. Kernel and generic artifact builds must not
acquire a dependency on its Arch packages, systemd, NetworkManager, browser,
desktop, Rust or Node tooling. `make check-diagnostics` is a separate entry point.

Preserve the MIT notice in `diagnostics/LICENSE`, per-file SPDX notices,
third-party licenses and exact upstream/theme provenance. Do not substitute
the kernel's GPLv2 root license when packaging diagnostic source notices.
Record local adaptations without overwriting original imported byte hashes.
Retain pending source at its former location until primary review approves
any retirement; a successful copy does not authorize deleting it.

Keep generated roots, binaries, packages, firmware, keys, media and private
logs outside versioned source (`output/`, `cache/`, `dist/` or explicit external
paths). Source files under `diagnostics/ps4/tui/src/` are versioned source;
only its Cargo `target/` is disposable. Fetch public inputs through pinned
manifests rather than depending on a private archive or another checkout.

Preserve the 1 GiB core boot-set cap. Optional provider images have explicit
separate budgets and launch only on operator request. Never silently move an
optional image into the core cap, start a compositor at boot or label a host
dependency check as hardware acceptance. The current frontend bundle is a
fixed allowlist with hash, size, mode, root-image and kernel guards; expand it
only with matching init/refresh/builder tests.

Native builds, data-only artifact inspection and synthetic tests require no
console session. Hardware and storage operations require explicit task
authority and the root hardware-testing rules. Keep one bounded experiment,
preserve stop guards and the Ethernet retained-DMA restrictions, and do not
retry a failed hardware action without a changed variable or new evidence.

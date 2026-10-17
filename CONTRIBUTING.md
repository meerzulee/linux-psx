# Contributing to Linux psX

This repository owns console kernel targets and their distribution-independent
artifact contract. Read [CREDITS](CREDITS.md),
[CONTRIBUTIONS](docs/CONTRIBUTIONS.md) and
[PS4-PATCHES](docs/PS4-PATCHES.md) before adapting an imported change.
Omarchy installation, package policy, initramfs and desktop integration belong
to the companion `omarchy-psx` project. PS5 has a research record, not an
accepted target yet.

## Source changes

The active PS4 target is `targets/6.18-ps4-baikal.env`; it selects a pinned
upstream commit, resolved configuration and ordered series under
`platforms/ps4/patches/`. Inherited `platforms/ps4/targets/` files describe
the frozen historical import and are not the root build interface.

Work in a separate development checkout when editing upstream Linux. Save
the resulting reviewed changes as a versioned patch before a build. Generated
`src/` trees are disposable; the builder may reset and clean its own marked
worktree. Never rely on unsaved edits there, change another developer's tree
or regenerate imported patch bytes just to rename the project.

Keep the exact baseline ordering. Give each new patch its original author,
license and immutable source reference, explain adaptations, and update the
active provenance/config identities deliberately. Retain disabled experiments
as reference until a new diagnostic variable justifies them. Do not silently
enable firmware embedding, netfilter, ultrawide or unrelated platform fixes.

```sh
make check
./build.sh --target 6.18-ps4-baikal --patches-only
./build.sh --target 6.18-ps4-baikal
```

The build commands above are for native Linux x86-64. On macOS ARM64 use
the OrbStack wrapper described in [BUILDING](docs/BUILDING.md); verify OrbStack
is running and Docker context is `orbstack` first. `make check` works on
either host with Python 3 and a native C compiler. It verifies immutable
import hashes and active target contracts, including portable MTS C tests.
Compilation does not establish a hardware pass.

## Hardware evidence and reports

A useful report includes console/southbridge revision, exact source commit,
kernel/config/module/boot-set identities, loader/initramfs/firmware context,
expected behavior, observed result and a reviewed relevant log excerpt.
Redact MAC/IP addresses, serial identifiers, machine paths and credentials.
Do not upload raw firmware, PUP files, private mappings, EAP keys or unreviewed
UART/packet captures.

Use [HARDWARE-TESTING](docs/HARDWARE-TESTING.md) for a bounded coherent
experiment with continuous capture, a changed variable, timeout, stop
conditions and verified rollback. Device enumeration, a successful source
test and missing error messages are distinct from functional acceptance.
Historical [PS4 results](platforms/ps4/docs/README.md) keep their exact artifact
scope; a newly rebuilt target requires new evidence and declared repetition
counts before promotion.

MTS2 retains its code, mappings, references and published DMA until power-off.
Do not unload, reset, unbind, rearm or force cleanup after publication.
Healthy administrative cycles retain the same generation; a terminal fault
ends the attempt. Read [ETHERNET](docs/ETHERNET.md) before changing that path.

## A new console or Linux version

Create a separate explicit root target with a pinned base commit, console
configuration, reviewed patch order and provenance rather than replacing the
existing target. Record what is imported, refreshed, comparison-only and
still untested. Include platform/loader detection and isolate conflicting
shared-code quirks before proposing a universal binary. The
[PS4/PS5 research](docs/UNIVERSAL-KERNEL.md) explains why copying both patch
sets together does not establish one working kernel.

For a pull request, describe the concrete failure and resulting behavior,
the source of each change, checks actually completed and remaining hardware
limits. Preserve notices and include an appropriate Signed-off-by when you
can certify the contribution. An upstream submission also needs review
against that upstream tree and its current contribution rules; this document
does not certify that a downstream patch is ready for mainline.

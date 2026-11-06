# Diagnostix — PS4 diagnostic appliance

Linux psX owns the diagnostic source **and image builders**. A standalone clone
contains the controller, Ratatui interface, browser lab, initramfs/root overlays,
optional providers, media generator and host tests. No Omarchy source checkout
is needed. Production Omarchy installation and desktop policy remain in the
separate Omarchy psX project.

This is an experimental appliance for the recorded PS4 Baikal build, not a
general installer or a hardware-support declaration. The core boot set is
capped at 1,073,741,824 bytes. Its read-only ext4 loop image stays on USB;
writable directories and the bounded frontend overlay use RAM. SSH uses an
optional seeded owner key or explicit [local key enrollment](PS4-DIAGNOSTIC-SSH-ACCESS.md).
Unseeded images begin with root locked and password access disabled.
Graphics/browser tests start only when selected.

[Console screenshots](images/README.md) ·
[SATA/USB storage comparison](PS4-STORAGE-COMPARISON.md)

The [import receipt](../provenance/diagnostics-import.json) records the source
commit, pending-byte hashes, licenses and repository-local adaptations. The
original source was retained during review. [Detailed image notes](PS4-DIAGNOSTIC-IMAGE.md)
preserve historical test scope; [Chromium notes](PS4-DIAGNOSTIC-CHROMIUM.md)
describe the separate opt-in provider.

The separate [UVD alpha](PS4-DIAGNOSTIC-ALPHA.md) adds a matched experimental
kernel, exact AVC reference comparison and the full **Test plan** checklist.
The checklist covers 45 scoped gates: 6 automated, 8 assisted, 22 manual,
6 unimplemented and 3 deferred. It lists the unfinished work explicitly and
does not start tests or award stability. Ordinary frontend refreshes reject
UVD alpha images so native-dev3 helper guards cannot overwrite alpha guards.
The explicit `--alpha-ui-only` mode accepts the frozen alpha3 boot set and
changes only the TUI binary and its receipts; all other initramfs entries,
firmware, kernel, root image and UVD gates remain unchanged.

The separate [browser UVD prototype](PS4-DIAGNOSTIC-BROWSER-UVD.md) prepares
offline four- or 30-second AVC clips and runs only on an explicit hardware
trial. Its first browser attempt fell back to software; longer media and
source corrections do not establish hardware decoding or stable support.

## Source checks

Use a native Linux x86-64 host for diagnostic image assembly. The builder uses
Python 3, GNU tar with zstd, binutils, pacman, e2fsprogs and normal Linux tools;
`--help` and synthetic tests do not install packages or contact the console.
Browser JS tests require Node. Rust/Cargo is needed only to build/test the TUI;
the kernel's normal `make check` has no diagnostic-tool dependencies.

```sh
git clone https://github.com/meerzulee/linux-psx.git
cd linux-psx
make check-diagnostics
python3 scripts/build-ps4-diagnostics --help
python3 scripts/refresh-ps4-diagnostics-frontend --help
CARGO_TARGET_DIR=/var/tmp/psx-diagnostics-cargo cargo build --release --locked --manifest-path diagnostics/ps4/tui/Cargo.toml
```

`scripts/check-ps4-diagnostics --source-only` verifies imported source identities
without running tests. Add `--rust` to run the locked offline Rust tests, using
an external Cargo target directory. Populate Cargo's ordinary public crate
cache first if it is empty.

After deliberate Linux-owned source changes, record each exact path and the
reason before running checks:

```sh
python3 scripts/check-ps4-diagnostics --record-adaptation diagnostics/ps4/controller.py --reason 'Describe the intentional behavior change' --source-only
```

Repeat `--record-adaptation` for additional changed/new source files. The tool
appends current destination hashes and reasons, preserving original imported
hashes. New Linux files enter the generated-source list without claiming an
Omarchy origin. This updates source provenance only; it neither validates a
hardware behavior nor changes any frozen output artifact.

## Obtain pinned build inputs

All generated inputs remain outside Git. The frozen console profile names one
exact kernel/config/modules/Mesa set; a fresh build has new identities and
requires a new profile and hardware qualification. Do not edit a frozen
profile merely to make unrelated artifacts pass its hashes.

* Build the kernel and **whole matching module archive** using this repository's
  [native builder](BUILDING.md), for example
  `./build.sh --target 7.2-ps4-baikal-native-dev`. Upstream Linux acquisition,
  corresponding source and provenance are handled by that builder.
* Build the process-local Mesa candidate using
  `python3 userspace/mesa/build-candidate.py --help` and the pinned source/patch
  manifest in `userspace/mesa/`. No Omarchy Mesa source path is used.
* Fetch the public historical BusyBox/firmware seed using
  `python3 scripts/fetch-ps4-initramfs-seed`. Its immutable URL, size and SHA256
  are in `manifests/ps4-initramfs-seed.json`; the reduced firmware-member pins
  are in `manifests/ps4-diagnostic-seed-firmware.json`. The old seed is input
  data, never the installed root or a claimed accepted boot.
* Fetch externally licensed MT7668 firmware using
  `python3 scripts/fetch-ps4-firmware`. `manifests/ps4-firmware.json` pins each
  public upstream URL, size and SHA256. Firmware binaries are excluded from
  this source tree.
* Obtain package archives through
  `manifests/ps4-diagnostic-packages.json`. It records official Arch Linux
  Archive URLs, byte sizes, hashes, DEPENDS/PROVIDES and both profile closures.
  The console closure has 392 archives (465,175,889 compressed bytes); the
  legacy TUI closure has 184. Libalpm validates versions/dependencies when
  installing the root. Keep notices and package licensing intact.

An existing snapshot is an explicit external binary input to `--snapshot`.
The frozen profile pins SHA256SUMS to
`6b98da612b59e63b8548393cf0df8c7d9a572a4cad1f851aec930eb594e9cd57`.
The historical database filename `repo/omarchy-ps4-snapshot.db.tar.gz` is a data
format retained for compatibility; it does not identify a required checkout.

For a cold package build, download the selected closure from the public lock,
verify every size/hash, and use Arch's `repo-add` to assemble
`repo/omarchy-ps4-snapshot.db.tar.gz`. Generate a sorted SHA256SUMS containing
that database and each `repo/*.pkg.tar.zst`. Database generation is not claimed
to reproduce the historical bytes. Copy the diagnostic profile to a new file,
give it a new profile identity, and set `snapshot_sums_sha256` to the measured
SHA256SUMS hash. The exact runtime-library archive/member/file pins stay fixed;
changing the snapshot receipt cannot bypass them. Never relabel a reconstructed
snapshot as the original frozen one. This procedure uses public inputs and
does not require copying an Omarchy output directory.

Inspect the exact CLI before assembly:

```sh
python3 scripts/build-ps4-diagnostics --profile manifests/ps4-diagnostics-console.json --help
python3 scripts/prepare-ps4-diagnostics-media --help
python3 scripts/prepare-ps4-chromium-provider --help
python3 scripts/build-ps4-wayland-suite --help
```

Supply explicit paths for the kernel artifact directory, snapshot, seed,
private Mesa archive, firmware, TUI binary, graphics probes, browser fixture and
owner public key. `--plan` validates and reports inputs without assembling an
image. Build OpenGL/Vulkan probes from the pinned source in `userspace/mesa/`
and generate the tiny browser fixture with
`diagnostics/ps4/lab/generate-video.py`; these are host operations.
`--stage-only` prepares a root for inspection; ordinary assembly packs ext4
offline with `mkfs.ext4 -d`, not a host mount or a container. No CLI examples
here authorize writing a console or a physical USB device.

## Frontend refresh and optional suites

`scripts/refresh-ps4-diagnostics-frontend` accepts an existing checksum-verified
boot set as **binary data** and writes a new ordinary output directory. It
retains the exact kernel/root/modules and regenerates only the small fixed
frontend initramfs plus receipts. `--runtime-library-dir` must include the
exact provider files and their pinned receipt; alternatively `--snapshot`
extracts them from the verified public archives. Fresh builds install these
providers normally. A refresh never permits arbitrary replacement libraries.

Chromium is a separately capped 384 MiB provider, mounted read-only only on an
explicit Chromium action. Its source preparation and verified SSH stager are
local tools; it is outside the 1 GiB core payload and is never an auto-started
desktop service. The native DRM/GLES Wayland suite is also opt-in, with pinned
public providers and an offline ELF closure check. Building either suite is
not GPU execution or acceptance. Use the recorded stop guards and hardware
experiment rules before any real run.

Diagnostic tooling and generated synthetic-media notices use
[`diagnostics/LICENSE`](../diagnostics/LICENSE), the preserved MIT text.
Upstream packages, fonts, firmware and themes retain their own notices. The
kernel root GPLv2 license and artifact publication requirements are unchanged.

## Hardware overview and optional gaming

Open **View hardware details** from System, or a component's details action.
The Ratatui view groups the cached inventory into readable component summaries.
Use arrows to choose a component, Enter for its detail page and Esc to go back;
press A explicitly for that component's raw data. Long pages scroll and retain
their position through resize/back navigation. Opening or navigating the view
does not run a test, issue another controller request or modify saved reports.
Missing metadata stays unavailable, and a UVD boot policy is labelled separately
from decoding evidence.

The optional **ProtonDB** action opens its public website in a fresh private
Firefox profile. It is manual browsing without a diagnostic batch or automatic
test timeout; community Linux ratings are not PS4 game results. Steam and a
local game-compatibility catalogue remain
[proposed optional additions](PS4-DIAGNOSTIC-GAMING.md) outside the 1 GiB core
budget.

Settings groups **Theme**, **Font** and **About** into expandable parents.
Choose a child to apply a theme, change font size or view the creator link;
SSH setup remains a separate action. The TUI and browser lab display
**made by meerzulee** in their footers. These interface changes do not add
hardware acceptance or change saved test results.

## Source-handoff verification

On 2026-11-04 the standalone source passed 279 Python tests, 11 Node browser-lab
tests, 28 locked offline Rust tests and all 14 source CLI help checks. An actual
offline refresh consumed an existing boot set and exact runtime providers as
external binary data. The result held 28 fixed frontend assets and measured
1,065,928,109 bytes, preserving the kernel/root and every other immutable file.
No Omarchy source was read by that build. The import receipt records these
checks and their artifact hashes. No full root reassembly, new hardware test
or publication was performed as part of the source migration.

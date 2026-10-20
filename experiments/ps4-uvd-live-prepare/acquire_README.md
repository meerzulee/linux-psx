# Read-only preparation acquisition producer

`acquire.py` is a standalone Python standard-library producer for a future
preparation boot. It collects explicitly scoped inputs and emits one JSON
object to stdout. It writes no files, runs no subprocesses, opens no network
connection and provides no GPU, register, UVD-arm, mount or permission control.

This tool has only been tested with fake readers, identities, clocks and file
descriptor operations. It has not been run against the console. The current
IB experiment is unaffected.

The only positive result is:

```
CAPTURED_CACHED_PREPARATION_INPUTS
complete: true
hardware_acceptance: false
```

That means the bounded input collection completed with consistent canonical
identity observations. It does not mean the status is PREPARED or that an
experiment passed. Even an empty or malformed status payload is retained as a
successful acquisition if it was read completely. The separate, frozen
`observation.py` evaluates the captured bytes.

## Future operator use

The root workflow must review this script and authorize its use under the
future operator plan first. The expected release, PCI BDF and exact debugfs
directory are supplied by that workflow. The producer never assumes directory
0, derives a PCI address from the directory number, or searches for a device.
The selected directory's sibling `name` must identify driver `amdgpu` and the
explicit PCI address from the plan/UART binding (planned `0000:00:01.0`).

The boot configuration must allow debugfs with `debugfs=on`. Debugfs must
already be mounted, and the caller must already be able to read the root-only
node. Missing nodes, a missing mount, denied access or a removal race produce
an incomplete acquisition; this program attempts no repair or retry.

The allowed status path is exactly:

```
/sys/kernel/debug/dri/NUMBER/amdgpu_ps4_live_prepare
```

`NUMBER` is one canonical nonnegative decimal directory name, at most ten
digits, with no leading zero except `0`. No suffix, alternate node, symlink
leaf, traversal or relative path is accepted.

Once the script and inputs have been reviewed, a caller can run the following
on the target host, replacing `NUMBER` with the explicitly selected directory:

```sh
python3 -B -I acquire.py \
  --expected-release 7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1 \
  --expected-pci-bdf 0000:00:01.0 \
  --status-path /sys/kernel/debug/dri/NUMBER/amdgpu_ps4_live_prepare
```

It also works when the reviewed source is supplied on stdin:

```sh
python3 -B -I - \
  --expected-release 7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1 \
  --expected-pci-bdf 0000:00:01.0 \
  --status-path /sys/kernel/debug/dri/NUMBER/amdgpu_ps4_live_prepare < acquire.py
```

The root workflow may arrange that stdin transport through already authorized
SSH access. This producer contains no SSH client, privilege escalation or
remote execution facility. Saving or transporting stdout belongs to the
caller, and the producer does not create a local or remote report file.

`--help` returns JSON without reading inputs. Argument errors also return JSON
on stdout; arguments cannot be duplicated or abbreviated. Exit codes are `0`
for a complete acquisition or help, `1` for a refused/incomplete collection,
and `2` for an invalid request or CLI/API error. Every report keeps
`hardware_acceptance=false`.

## Scope and ordering

The only runtime input operations are:

- `uname(2).release`, using `os.uname()`; the raw release encoding has no added
  newline.
- `/proc/sys/kernel/random/boot_id` (64-byte bound).
- `/proc/cmdline` (65,536-byte bound).
- `/proc/uptime` (128-byte bound).
- The one caller-selected cached status node (65,536-byte bound).
- Its sibling `/sys/kernel/debug/dri/NUMBER/name` (4,096-byte bound), derived
  from the supplied status path without directory discovery.
- Monotonic and wall-clock nanosecond lookups for acquisition brackets.

Collection order is fixed:

1. Record the acquisition-start monotonic/wall-clock pair.
2. Read release and boot ID. Require canonical identities and the exact
   expected release before proceeding.
3. Read cmdline and the first uptime; read release and boot ID again. Require
   identity consistency and the expected release before touching the cached
   node. Any prerequisite read, bound, identity or timing error refuses the
   status read.
4. Read the sibling DRM `name` and require driver `amdgpu` and the explicit
   expected PCI BDF. Then record the before-status clock pair, read the cached
   node once through one descriptor, and record the after-status pair.
5. Read the same sibling `name` again and require the same driver/BDF and
   byte-identical name text. Read uptime, release and boot ID again, even if
   the status or second name read failed. Require identity consistency,
   expected release and nondecreasing uptime.
6. Record the acquisition-end clock pair. Clock failures or monotonic
   regression make the collection incomplete. A wall-clock adjustment is
   preserved in `wall_clock_nondecreasing`; it does not erase otherwise
   complete inputs.

The extra identity pair before the node provides a consistency prerequisite
as well as a postcondition. These separate reads remain non-atomic. The
monotonic and wall-clock values in a pair are called in that order, not at the
same instant. An unchanged boot ID or release cannot rule out a change between
observations, authenticate the host or establish current GPU state.

Identity parsing is deliberately canonical: the expected and observed
release are ASCII release tokens of at most 128 bytes; boot ID is a nonzero
lowercase UUID followed by one LF; uptime is two nonnegative decimal numbers
with two fractional digits, one separating space and one final LF. An uptime
integer part is limited to 19 digits. Idle time may exceed uptime because it
can cover multiple CPUs. The producer does not interpret cmdline gates or
the status schema, so those exact bytes remain available for the evaluator.

The required PCI BDF has canonical lowercase `dddd:bb:ss.f` form: four domain
digits, two bus digits, two slot digits in `00..1f`, and function `0..7`. The
DRM name parser follows the preserved native `drm_name_info` literal:

```
driver dev=DEVICE[ master=TEXT][ unique=TEXT]\n
```

The native formatter makes `dev` optional, but this acquisition requires it
to bind the selected directory to the explicitly named PCI device. Only the
driver token and its immediate `dev=` field establish identity. Because the
kernel does not escape optional strings, the suffix beginning with ` master=`
or ` unique=` is retained as an opaque printable ASCII string. It is never
searched for a replacement device field or parsed into possibly ambiguous
master/unique values. Optional text can be absent, empty, contain spaces or
include another `dev=` spelling without changing the identity interpretation.
Unknown suffix introducers, control bytes and multiple lines are refused.
Both raw name observations and their hashes remain in the report. Changes to
optional text also make the acquisition incomplete, even when the BDF stayed
the same; no assumption that a DRM master stays unchanged is hidden.

## Bytes and failure evidence

Each observation records its source, bound, exact observed bytes as base64,
SHA256, byte count and completeness. Canonical identity/uptime values are also
parsed. On overflow it preserves at most `bound + 1` bytes as an observed
prefix with `source_complete=false`; it never hashes that prefix as if it
were the entire input. A mid-read error preserves the prefix obtained so far.
An open failure has `raw=null`, which differs from a successful zero-byte
read. Error class, errno where available and a bounded error message remain
in the report. Collected inputs are retained after an identity mismatch.

Schema 2 adds required `expected_pci_bdf`, fixed `expected_driver=amdgpu`,
derived `drm_name_path`, `drm_identity_consistent`, the `drm_name_before` and
`drm_name_after` observations, and the native name-formatter reference.
`identity_consistent` still describes the boot ID/release pairs; it does not
replace the separate DRM check. The core API now requires `expected_pci_bdf`.

For a complete acquisition, the separate evaluator can consume decoded raw
inputs without modifying them:

```python
status_bytes = base64.b64decode(report["observations"]["cached_status"]["raw"]["base64"], validate=True)
uname_bytes = base64.b64decode(report["observations"]["uname_before"]["raw"]["base64"], validate=True)
cmdline_bytes = base64.b64decode(report["observations"]["cmdline"]["raw"]["base64"], validate=True)
```

The integrator must check `complete`, retain the acquisition report and its
identity/timing evidence, and independently evaluate the saved bytes. A
successful evaluation still does not make hardware acceptance true.

## Read adapter and limits

The native adapter opens only the allowed literal paths, with `O_RDONLY`,
`O_NOFOLLOW`, `O_CLOEXEC` and `O_NONBLOCK`. It calls `fstat` before reading and
requires a regular-file mode, which includes the reviewed seq_file node.
`st_size=0` is valid; size metadata is never used to determine the byte count.
Reads continue to EOF or a `bound + 1` sentinel, and the descriptor is closed.
No seek, write or ioctl is used. Required open flags must exist; their absence
does not relax the policy.

These checks concern the leaf descriptor only. They do not authenticate
parent directories, a filesystem mount, namespaces, device identity or a
hostile host. Nonblocking open helps avoid blocking on an accidental FIFO
before the regular-file check; it is not a deadline for a kernel regular-file
or debugfs read. The caller owns transport/runtime timeouts and must preserve
an interrupted capture as incomplete. A read overlapping removal may fail
through the debugfs proxy.

The reviewed node copies cached scalar state and prints it without GPU/UVD
register access or work submission. Its compiled-review reference is:

```
lab/uvd-2026-10-20/live-prepare/compiled-audit/compiled-review.json
13e4837544a6ea7faade1bf932d7da2f739c1909df1fb3cdb3f88adf649c09b4
```

That is a static provenance reference embedded in this producer; the producer
does not open the receipt or attest that a running kernel matches it. Clock
lookups and the operating system's read implementation are outside the claim
of no GPU/UVD register access. PREPARED and its proof timestamps/generations
remain historical cached observations. No current page-table, normal-boot,
GPU-coexistence, UART-continuity, lifecycle or decode evidence is manufactured
by this acquisition.

The sibling name parser is anchored to the primary's saved current native
formatter in `lab/uvd-2026-10-20/live-prepare/review/DRM-NAME-FORMATTER.json`.
The native `drivers/gpu/drm/drm_debugfs.c` file SHA256 is
`d0b17791b2536af59e628fcbcf0c06a72b2a494cf22624c79dcb87c6308a2d38`.
Its formatter emits the driver, optional ` dev=`, optional ` master=`, optional
` unique=`, then LF while holding the DRM master mutex. The historical local
whole-file hash differs; no full-file parity is claimed. The injected tests
check the saved native formatter's literal sequence. This source reference is
not a running-host or mount attestation.

## Injectable API and offline tests

The core has no default live dependencies:

```python
report = acquire(
    expected_release=expected_release,
    expected_pci_bdf=expected_pci_bdf,
    status_path=explicit_status_path,
    reader=fake_reader,          # reader(path, limit) -> bytes or ReadFailure
    uname=fake_uname,            # () -> release bytes, without an added LF
    monotonic_ns=fake_monotonic, # () -> nonnegative int nanoseconds
    wall_time_ns=fake_wall,      # () -> nonnegative int nanoseconds
)
```

All four dependencies must be supplied. The reader returns complete bytes or
at most `limit + 1` bytes to signal an oversized input. `ReadFailure` may carry
an observed byte prefix. The native `read_bounded` adapter separately accepts
injected open/fstat/read/close functions, so its file contracts can be tested
without touching `/proc`, `/sys` or a device. Importing the module performs no
acquisition.

Run the offline suite with a new receipt filename:

```sh
python3 -B linux-psx/experiments/ps4-uvd-live-prepare/acquire_test.py \
  --report-file lab/uvd-2026-10-20/live-prepare/acquisition-tool/attempt-02/macos-01.json
```

The suite writes only its caller-selected evidence under that directory. It
uses no subprocess, SSH, live identity, live clock, device, status, mount or
privilege operation. Its sample report is explicitly synthetic.

The first producer, tests and README bytes remain preserved under
`acquisition-tool/attempt-01/snapshot/` with their original hashes and the
40-case first-run receipt. Attempt 02 adds the explicit DRM device binding.

# PS4 UVD source composition

The experimental normal AMDGPU UVD backend is now developed in [`runtime/`](runtime/README.md).
It connects the cold-boot sequence used for the verified first frame to normal
application submission. EXP-20261020-006 passed one real
FFmpeg/VAAPI run: all 120 reference frames were byte-exact, with normal desktop/SSH
and completed UART continuity. [Runtime results](runtime/RESULTS.md) retain the
limited clip/boot scope; presentation, repetition and broader coverage remain. The rest of this
file describes the earlier, preserved composition tool.

`compose.py` combines exactly two frozen source deltas over the preparation
attempt-06 baseline: fault-history revision 02, followed by readiness-core
attempt 01. It checks the receipt and patch hashes embedded in the tool,
compares all 16 readiness baseline files with preparation, and compares the
shared GMC baseline byte for byte. The two patches change disjoint sets of
three files.

The output contains one 18-file source overlay: preparation's 16 files plus
the IRQ observer source and header. The original patch bytes, author headers,
source notices, receipt copies and replay order are retained. The tool runs
`git apply --check` and `git apply` for each patch, then compares every output
byte against the corresponding frozen candidates. It rechecks input hashes
before writing its final manifest.

From the workspace root:

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-driver/compose.py \
  --out lab/uvd-2026-10-20/driver-integration/attempt-01
```

The output must be a new immediate child of
`lab/uvd-2026-10-20/driver-integration/`. Existing outputs are never reused.
Failures after output creation leave `FAILURE.json` and available command
logs. `MANIFEST.json` is written only after exact ordered replay and final
input checks succeed. It inventories every output except itself; its own hash
is printed. `INVOCATION.json`, `COMMANDS.json` and per-command output files
record what actually ran. A copy of this tool and README is retained; pass
`--workspace` explicitly if invoking that copied tool from another location.

This result establishes source correspondence only. Live entry, control and
lifecycle integration remain unresolved. The unfinished live-control
component is excluded. There is no combined compilation, installed or runnable
driver, hardware acceptance, firmware inclusion or console operation. The
changed embedded `amdgpu_device` layout requires a full matching kernel and
modules rebuild before adoption. Prior component tests and isolated object
checks do not qualify the combined overlay.

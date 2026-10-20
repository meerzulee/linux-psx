# PS4 UVD preparation-only development candidate

This separate experiment prepares the full private mapping at the existing
pre-client SDMA seam and returns to normal initialization. Its status interface
is cached and read-only. It has no live UVD arm or decoder operation.

Read [DESIGN.md](DESIGN.md) for gates, ownership, historical-versus-current
mapping semantics, teardown scope and outstanding validation. This source is
not selected by any active series or target and must not replace the installed
IB-DATA hardware test.

Generate a new selected-file candidate and patch from the pinned baseline:

```sh
python3 -B linux-psx/experiments/ps4-uvd-live-prepare/build_candidate.py \
  --baseline lab/uvd-2026-10-20/live-prepare/source/baseline \
  --output lab/uvd-2026-10-20/live-prepare/source/review-candidate
```

Run from the workspace root. The output directory must not already exist.
The tool performs no SSH, build, stage, privilege or hardware operation. It
checks all 15 selected baseline file hashes and emits 16 files, the patch and
a source receipt. Use the patch over the exact full frozen IB-DATA tree for a
later separately reviewed build; the selected-file output is not a full tree.

The source templates are `live_prepare.h`, `live_prepare.c.inc` and
`debugfs.c.inc`. The generator preserves upstream notices, checks exact edit
anchors and rejects drift. Existing imported patches and frozen experimental
inputs remain unchanged. No firmware is added or fetched.

Root owns `check.py`, `fixture.c` and `lab/.../live-prepare/host-tests/` for
actual-source host validation. Source generation, host models, compilation,
and console evidence have distinct scopes; no test result is implied by this
README.

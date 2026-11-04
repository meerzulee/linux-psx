# Core checks before advanced diagnostics

The intended diagnostic order is boot/artifact identity, CPU known-answer
computation, owned-RAM patterns, GPU reference pixels, browser/video behavior,
then selected peripheral checks. Read-only inventory, networking, SSH setup,
reports and recovery remain independently available. This ordering is a
diagnostic dependency policy, not a claim of overall hardware stability.

The controller enforces the CPU/RAM boundary for GPU catalogue probes,
OpenGL/Vulkan reference renders, graphical browser/test launches, 44.1 kHz
audio playback, and the interface-bound HTTPS diagnostic. Network connection
setup/status and read-only audio/storage inventory remain independent. Selecting
one of these actions first saves its normal job, then runs missing core checks
in order: a two-second CPU computation followed by 32 MiB of owned-RAM patterns.
It never reaches a GPU command after a failed or incomplete prerequisite. A
directly selected RAM check likewise requires CPU PASS first, then runs only
the RAM size the operator selected. It does not recursively run another RAM
check or prevent an explicitly justified RAM retry after CPU PASS. A
browser HTTP start can only reuse completed core receipts; it cannot start core
work or bypass the gate. The existing graphical diagnostics/recovery UI is not
treated as an advanced test. Wayland has no controller action yet; its future
controller tests use the same advanced classification. The separate root-only
Wayland CLI remains an explicit engineering entry point outside this UI policy.

Completed core PASS reports are reused only for this boot and matching boot
manifest, runner source and catalogue hashes. The test version, parameters,
successful bounded command receipt and functional oracle must also agree.
The CPU oracle is the runner's fixed SHA-256 answer and a positive iteration
count. RAM must report the selected owned allocation and all four patterns
`0, 85, 170, 255`. Existing valid parameter sizes are preserved when reused;
the automatic defaults do not relabel earlier test coverage.

A previous failed, blocked, interrupted or unfinished core check is never
retried automatically. The descendant receives a durable `BLOCKED` report
with the prerequisite/run ID and a human explanation; it is not recorded as a
failed GPU or browser test. Review the original evidence, then explicitly run
the core check with the existing changed-evidence retry reason when permitted.
Running/unknown GPU faults and existing stop guards remain enforced. A stale
CPU/RAM running marker blocks advanced work but does not itself disable network
setup or the owner's working SSH access. Full core history is inspected so the
UI's bounded report list cannot evict a failure or cause an automatic rerun.

Core PASS establishes only these finite functional checks. It does not qualify
all CPU cores, cooling, every memory address, GPU stability, decoder support,
performance, or another console model. No performance threshold is invented.
The controller does not automatically render with an API, start Wayland, play
video/audio, test storage, or perform a power transition beyond the action the
operator selected. GPU-specific qualifications and experimental Vulkan/UVD
stop conditions still require their separate evidence and approval scope.

Host validation:

```sh
python3 -m unittest discover -s tests -p 'test_ps4_diagnostics_prerequisites.py'
```

Tests use synthetic workers and files. They check exact execution order, cached
identity reuse, failure/interruption blocking, explicit retry, independent
network access, browser-start refusal and every current advanced window mode.
No console, network interface, GPU or storage device is operated by this suite.

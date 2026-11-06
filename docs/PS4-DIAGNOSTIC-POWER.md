# Diagnostix power controls

The Power tab offers **Shutdown** and **Reboot to Orbis**, with Cancel selected
until the operator explicitly confirms. The main native kernel now registers
an atomic Baikal ICC restart callback; the original power-off contribution
from suds-mcduff remains intact. Restart returns to Orbis, not directly to Linux.

On runtime-dev5/alpha6, completed UART recorded normal restart after 120 exact
UVD reference frames and software power-off with UVD enabled. Correcting tty2's
interactive rescue shell to `KillSignal=SIGHUP` removed its 90-second SIGTERM
wait. The corrected-image restart reached ICC **1.049 seconds** after command
acceptance (EXP-20261106-019); Shutdown reached ICC in **1.093 seconds**
(EXP-20261106-020), and the owner confirmed the light and fans stopped.
These are scoped observations on one Baikal Slim, not repeated-cycle stability.

Runtime-dev6/alpha7 adds **immediate Power**. A confirmed action preempts owned
jobs, sends SIGKILL to their process groups and the fixed graphics/browser
cgroups, and calls ordinary Linux `reboot()` with POWER_OFF or RESTART.
It skips USB report saves, fsync, sync, remounts and systemd service-stop waits;
**unsaved reports and other pending writes may be lost**. The TUI uses a
separate IPC lane so a slow state or SSH setup request cannot queue Power.
The controller remains outside the killed cgroups. Cancellation prevents
late worker callbacks and subsequent diagnostic child commands.

The exact boot/root/gate/ownership contract is checked once when the controller
starts. The terminal path uses that cached verified identity and a RAM-only
request/outcome. A duplicate in-flight or already-issued call is not sent
again. An explicitly failed or expired **not-submitted** request can be
confirmed again; an uncertain issued call remains fenced. No permanent USB
"already recorded" marker blocks this new path. Older profiles preserve their
historical dispatch behavior.

The dev6 UVD callback admits a previously started runtime whose complete boot
initialization succeeded, even if cancelling the test already closed or faulted
its queue. It retains allocations, quarantines entered leases and preserves
latched errors through terminal restart. No allocation is freed, DMA mapping
removed or GPU restarted in place. Failed startup, forced restart, kexec,
suspend, removal and reset remain outside this exception. The default-off
immutable power opt-in remains required.

The new preemption and post-fault restart paths have source/host evidence;
they still need a separate observed hardware test. See the
[terminal restart implementation](../experiments/ps4-uvd-driver/runtime/TERMINAL-RESTART.md)
and [session ledger](../diagnostics/SESSIONS.md).

The following records describe the earlier frozen profiles and their distinct
restart mechanism.

The **Power** menu offers **Shutdown** and **Reboot to Orbis** on the exact
frozen alpha4/dev3 and alpha5/dev4 profiles. Each opens a confirmation with
**Cancel** selected. An older controller or another build shows the controls
as unavailable. Demo mode never sends a power request.

Shutdown saves reports and requests the reviewed software power-off path.
The owner observed physical power-off after UVD on dev3 (EXP016) and dev4
(EXP021). The dev4 sequence took about 91 seconds and retained busy loop/USB
and DMA-cleanup limitations. Allow time for it to finish; the menu does not
claim that command acceptance proves the console is off.

Reboot to Orbis is an **experimental forced ICC restart**, after saved reports,
sync and a verified read-only USB remount. It skips Linux reboot teardown.
EXP018 observed this sequence once on dev3; dev4 preserves that ICC source
path but has no separate observed restart acceptance. Ordinary Linux reboot,
kexec, suspend and DMA drain remain unqualified. This control does not invoke
`systemctl reboot`, unload a driver or reset/retry UVD.

The root-only `power.execute` request accepts only `shutdown` or `reboot-orbis`
and the literal confirmation `true`. It checks the exact frozen profile,
verified root/initramfs contract, matching USB ownership receipt, and all four
effective UVD/power-off gates. Active tests, unfinished reports and saved
faults refuse dispatch. A completed, matching UVD PASS permits only this narrow
power operation; its immutable attempt/stop marker stays intact. Presence of
the experimental `psx_sbram` module also refuses both controls, regardless of
its arm value; use that experiment's recovery procedure.

Before dispatch the controller saves and fsyncs the request, reserves a
per-boot power intent, flushes report files and durably finishes its job.
Failure at any step sends no power command. Dispatch rechecks identity and
stop conditions, flushes reports again and holds the runner's nonblocking
exclusive lock through storage sync, remount and the request. A read-only
lock descriptor permits the USB read-only remount. It records a private RAM outcome at
`/run/psx-diagnostics/power-dispatch.json`. A reserved intent blocks further
active diagnostics and repeated power requests, including after controller
restart. The report remains **INCONCLUSIVE** until physical/UART observation;
it never records a hardware PASS from a returned command or a lost connection.
An identity-matched dispatch failure appears in Power and Reports with its
reason and a no-retry instruction. The controller also updates the USB report
when it remains writable, and never remounts it writable to save an outcome.

State polling also carries the matching current-boot dispatch outcome to the
TUI after its preparation job has completed. Preparing, failure and sent states
replace that request's initial pending snapshot; unchanged polls preserve other
notices. This is a source fix for the visibility race identified during
INC-20261106-002. It changes no power command or retry policy, does not explain
the observed ordinary reboot sequence, and has not been deployed with the
separate DDR3 capacity-loader experiment.

The controller reserves its active job under the job-state mutex, releases the
mutex for directory creation and durable initial/final saves, then publishes
completion after the final save succeeds. A slow USB save therefore does not
hold the mutex needed by status queries. The active reservation still prevents
a second job, and a failed save never dispatches power. Synthetic blocked-save
tests cover initial directory creation, initial/final saves and the durable
callback ordering. This repairs a source-level UI blocking defect; physical
shutdown/restart and storage latency still need captured observation.

Power progress also names the current preparation/dispatch phase before and
after report flushes, storage sync, mount checks, the read-only remount and the
selected power call. A fixed `PSX_POWER_V1` marker carries boot/run identity,
phase and started/returned/failed state to the kernel log; the matching RAM
record feeds the menu. Host fixtures verify that state and job queries can
respond while either report flush is blocked. These markers identify a stuck
call; they do not cancel it, retry it or prove that a physical hang is fixed.
Report `fsync` and the ICC ioctl still lack a cancellation guarantee. Do not
infer the cause of the earlier 91-second shutdown from a matching service
timeout alone.

The `power-status2` frontend was staged and read back on the existing diagnostic
USB under EXP-20261106-014. That session ended with verified staging and no
instrumented Linux boot or Power-menu transition. The operator reported a tty2
reboot during handoff; UART recorded Orbis booting, while physical-button
involvement remained unconfirmed. This does not qualify the new menu or power
path. The next candidate also includes the job-state mutex fix and Overview.

EXP-20261106-015 booted that candidate and returned responsive IPC throughout
one restart attempt. The USB report flushes took about 0.65 and 0.89 seconds;
sync and the read-only remount returned. Opening the ICC node failed with
EACCES because its old `/run` location was on a `nodev` mount, before the ioctl.
The error was also hidden by an API mismatch: the frozen root's runner accepts
`read_json(path)`, while the controller supplied a second limit argument.

The `power-device4` candidate uses a fixed root-owned 0600 ICC node inside a
0700 directory on `/dev` devtmpfs. It checks the mount, directory and device
identity before opening, verifies the opened descriptor, and keeps the same
single ioctl and payload. A controller-local strict 64 KiB reader supplies
Power receipts without depending on the newer runner API. Identity and
single-request guards remain intact. Focused compatibility/device fixtures
and the complete host diagnostics checks pass; a fresh physical restart test
is still required.

For a power experiment, keep continuous UART capture through the planned
sequence and save reports on the host first. If dispatch fails or the outcome
is uncertain, inspect the saved evidence and follow the planned physical
power-off/Orbis recovery; the appliance does not retry. The qualification
catalogue's power checks remain blocked and the smoke suite never powers off
or restarts the console automatically.

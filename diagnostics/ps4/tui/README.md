# psX diagnostic console

A small native Ratatui interface for the PS4 diagnostic controller. It runs on
the Linux virtual terminal or through an SSH terminal. The UI opens with network
setup on every invocation, including when Ethernet is already connected.

The layout is centered, adapts from 80×24 upward, and uses an olive palette.
Linux VT uses the six palette slots set by the image's console helper;
SSH terminals advertising `COLORTERM=truecolor` use the same olive RGB colors.
Other terminals use an olive 256-color fallback. No browser, HTTP client, Tokio runtime or external assets
are required by this client. Graphical test applications are separate selected
jobs on TTY3; the controller owns their launch and return to TTY1.

## Build and run

Rust 1.88 or newer is required. Direct dependencies are pinned exactly and the
full dependency checksums are recorded in `Cargo.lock`.

```sh
cargo build --locked --release
cargo test --locked
./target/release/psx-diagnostics-tui --socket /run/psx-diagnostics/control.sock
```

The release profile optimizes for size, uses LTO and strips symbols. The client
is installed as `/usr/bin/psx-diagnostics-tui` by the image assembler. Generated
target files stay outside Git.

For a host preview with sample data and no controller connection:

```sh
./target/release/psx-diagnostics-tui --demo
./target/release/psx-diagnostics-tui --render-demo 110x30
```

Demo screens are marked clearly and never report a hardware PASS. They only
simulate the interface state. Ordinary launch always starts on the network
screen; Continue opens a mode chooser with **Manual diagnostics** selected.
Choose a mode and press Enter explicitly. Refresh, boot and returning to this
menu never start a diagnostic run.

**Automatic diagnostics** submits one controller job: hardware inventory,
two seconds of CPU computation, a 32 MiB memory pattern check, Ethernet status,
audio inventory and storage inventory, in that order. It stops on failure and
shows step progress, a scoped summary and the issue report created by the
controller. Graphics, audio playback, UVD and power actions remain manual.
Selecting Automatic again requires returning to the chooser and activating it;
held Enter and refresh never resubmit a run. Existing job and retry guards apply.
The controller retains one automatic sequence per boot; use Manual for follow-up
checks after reviewing a stopped run.

**Manual diagnostics** opens the read-only Overview. It lists hardware and
recent test status for this boot. F7 switches between Overview and Advanced;
Advanced contains the existing tabbed controls. Enter on an Overview row opens
its Advanced area without starting a test. Switching views preserves active
jobs, saved reports and Settings selections. Press M on Overview, or choose
**Choose diagnostic mode** in System, to return to the mode chooser later.

**Create issue report** in Reports or the automatic summary creates a local
ZIP even when no tests have run or the kernel log is unavailable. It runs as
one asynchronous controller job. The completed view shows its exact local path,
download path, filename, size, report ID and SHA256. Retrieve the ZIP using that
path or the download path at the local browser lab / existing SSH forward,
then attach it to a GitHub issue. No issue is opened and nothing is uploaded
automatically.
**View issue report** reopens the latest export in this client. `READY` means
the archive exists; it is not a hardware PASS. Demo mode simulates results and
never creates an archive or qualifies hardware.

Passed applies only to saved functional receipts: native pixel checks for
OpenGL/Vulkan, exact AVC reference decoding for UVD, and operator-confirmed
44.1 kHz for audio. Detection, addresses and API enumeration cannot pass a
component. Not tested means no matching result in the current cache; earlier
boots do not turn rows green. Blocked, unsupported, deferred and inconclusive
remain distinct. The optional `overview_results` cache retains latest per-test
summaries beyond the ordinary recent-report list. No check starts automatically.
Nerd Font icons appear in the known graphical terminal; VT/SSH uses plain
markers with the same readable status labels.

## DiagnostiX run screen

The `diagnostix` binary is the full-screen view of the DiagnostiX app's RAM image
(0.16 at this revision). It runs the tests in order and shows one page of results.
To see it on a development computer with sample data:

```sh
cargo build --locked --release --bin diagnostix
./target/release/diagnostix --demo finished
```

The scenes are `starting`, `basics`, `graphics`, `ask-sound`, `ask-ethernet`,
`before-last`, `finished`, `finished-no-usb`, `download` and `manual`. Every scene
says "sample data" in its header. Hints name keyboard keys by default, and `--pad`
shows controller buttons instead.

## Controls

| Key | Action |
| --- | --- |
| Left / Right | Change diagnostic area |
| Up / Down | Choose an action |
| Enter | Open or run the selected action once |
| Tab | Change focus between areas and actions |
| Esc | Leave a form/report, or select the onboarding Continue action |
| F1 | Help |
| F2 | Network |
| F3 | Quick CPU/RAM check |
| F4 | Saved reports |
| F5 | Refresh controller state |
| F7 | Switch Overview / Advanced |
| M | Return from Overview or Automatic to the mode chooser |
| Ctrl+L | Clear old screen/scrollback contents and repaint the interface |
| V | Details of the last completed action |
| Q | Exit confirmation; does not power off the console |

Startup, Ctrl+L and terminal resize force a complete clean repaint. Clearing
does not cancel a job, close a form or discard a pending report. Cursor,
attributes and raw terminal state are restored on normal/error exit; the panic
hook also restores the interactive terminal before reporting a panic. Kernel
and PID1 output routing are managed by the image's separate console helper.
On the console keyboard, **Ctrl+Alt+F12** opens system logs and **Ctrl+Alt+F2**
opens the passwordless local rescue shell. **Ctrl+Alt+F1** returns to the menu.
These virtual-terminal shortcuts apply to the attached console keyboard;
SSH users keep their own terminal session.

Wi-Fi passwords exist only in the form and the outgoing typed connection
request. They are masked on screen, never placed in reports or logs, and are
discarded when the form is cancelled. A previous failed test asks what changed
before another attempt. Audio playback results request the operator observation.

## Controller contract

The client uses one newline-delimited JSON request/reply per Unix connection:

```json
{"id":1,"action":"state","params":{}}
{"ok":true,"data":{"system":{},"network":{},"ssh":{},"inventory":{}}}
```

Errors use `{"ok":false,"error":"readable reason"}`. Responses and requests are
bounded to 1 MiB. Socket read/write timeouts run in a standard-library worker
thread, so disconnected or slow controllers do not freeze terminal input.
State and active jobs are polled; actions are never retried automatically.

Supported action names include `state`, `diagnostics.auto`, `support.export`,
`quick`, `test.run`, `job.get`,
`network.scan`, `network.wifi.connect`, `network.ethernet.connect`,
`graphics.launch`, `results.list` and `result.observe`. Parameters are the
controller's fixed typed objects. The UI never constructs shell commands.

State contains `system`, `inventory`, `network`, `ssh`, `results`, `tests` and
`jobs`. Async submissions return an `id`; `job.get` returns `state`, `result`
and/or `error`. Input stays responsive while the controller owns the one active
job. The interface does not cancel driver or graphical jobs merely because a
user changes pages or exits the client.

`diagnostics.auto` and `support.export` both use empty parameters `{}` and the
same job submission/polling path as individual tests. Automatic results include
`test_id: "diagnostics.auto"`, scoped `status`, `message`, `steps`, individual
`results` and an optional `issue_report` or `issue_report_error`. Job progress
events expose `step_current`, `step_total` and `step_title`. Optional
`state.automatic` metadata is read only when present; older states remain usable.

An issue-report result contains `status`, `message`, `report_id`, `filename`,
`path`, `sha256`, `bytes` and `download_url`. A ready download path has the form
`/api/support/reports/<report_id>.zip`. The UI displays controller metadata and
does not construct an upload or issue-creation request.

Compact result summaries include `boot_id` and `retry_required`. The client
compares these with `state.identity.boot_id` so an old boot's failure cannot
require a retry explanation on a fresh boot. Pending audio observations from
this boot can be reopened from Reports after dismissing the first prompt.

Inventory is displayed as detected metadata. PASS applies to the selected
test's recorded oracle and never promotes overall hardware support or stability.

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
screen; choosing Continue opens the read-only Overview. It lists hardware and
recent test status for this boot. F7 switches between Overview and Advanced;
Advanced contains the existing tabbed controls. Enter on an Overview row opens
its Advanced area without starting a test. Switching views preserves active
jobs, saved reports and Settings selections.

Passed applies only to saved functional receipts: native pixel checks for
OpenGL/Vulkan, exact AVC reference decoding for UVD, and operator-confirmed
44.1 kHz for audio. Detection, addresses and API enumeration cannot pass a
component. Not tested means no matching result in the current cache; earlier
boots do not turn rows green. Blocked, unsupported, deferred and inconclusive
remain distinct. The optional `overview_results` cache retains latest per-test
summaries beyond the ordinary recent-report list. No check starts automatically.
Nerd Font icons appear in the known graphical terminal; VT/SSH uses plain
markers with the same readable status labels.

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

Supported action names are `state`, `quick`, `test.run`, `job.get`,
`network.scan`, `network.wifi.connect`, `network.ethernet.connect`,
`graphics.launch`, `results.list` and `result.observe`. Parameters are the
controller's fixed typed objects. The UI never constructs shell commands.

State contains `system`, `inventory`, `network`, `ssh`, `results`, `tests` and
`jobs`. Async submissions return an `id`; `job.get` returns `state`, `result`
and/or `error`. Input stays responsive while the controller owns the one active
job. The interface does not cancel driver or graphical jobs merely because a
user changes pages or exits the client.

Compact result summaries include `boot_id` and `retry_required`. The client
compares these with `state.identity.boot_id` so an old boot's failure cannot
require a retry explanation on a fresh boot. Pending audio observations from
this boot can be reopened from Reports after dismissing the first prompt.

Inventory is displayed as detected metadata. PASS applies to the selected
test's recorded oracle and never promotes overall hardware support or stability.

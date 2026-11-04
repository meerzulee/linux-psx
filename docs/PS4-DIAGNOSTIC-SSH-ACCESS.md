# Explicit diagnostic SSH setup

This optional appliance feature has synthetic host coverage, not hardware
acceptance. It does not change the network interface, reset a driver, replace
the SSH host identity, or remove the existing owner key.

Choose either method on the console's Network page:

* **GitHub public keys:** enter a username, compare the displayed SHA-256 key
  fingerprints with the account you intend to trust, then explicitly confirm
  Add keys. A preview alone does not authorize access or change files. Anyone
  holding a corresponding private key will gain root access to this diagnostic
  session. No GitHub token or arbitrary URL is accepted.
* **Temporary password:** explicitly open a ten-minute window and read the
  generated password in the private console modal. On your computer run
  `ssh-copy-id root@<console-IP>` and enter that password. Password access closes
  when the new public key is installed, when the window expires, when the
  controller exits, or when you explicitly close it. Existing key sessions are
  preserved by reloading the listener instead of restarting SSH.

Keys are merged with existing authorizations, deduplicated, and validated by
OpenSSH. Public keys persist in the selected USB's protected
`psx-diagnostics/state/ssh/authorized_keys`, then copy to RAM at
`/root/.ssh/authorized_keys`. Root ownership, mode 0600, atomic replacement and
file/directory fsync protect both copies. The original owner-key file remains
authorized. A later boot restores the merged keys without a network reset.

The password feature requires the matching diagnostic kernel, RAM-backed
`/etc`, `/root` and `/run`, the exact diagnostic SSH policy, a prepared unique
host identity, and the exact diagnostic root-lock placeholder `!`. A locked
preexisting password hash or another account-lock form is refused unchanged.
Its strong random
password is returned once through a synchronous root Unix IPC response. It
must never enter HTTP, state, Jobs, reports, history, command arguments or logs.
Only its salted SHA-512 credential is placed in RAM `/etc/shadow`; the plaintext
is never written to a file. The exact `!` lock is restored on expiry,
controller restart or guardian cleanup. An
unrelated credential change is preserved while SSH password policy is closed.

Before enabling access, a separate transient systemd guardian runs this same
module with the fixed `--guard` argument. No secret is passed to that service.
It watches the expiry, controller lifetime and public-key file independently
of the UI/controller thread. Its small lease/ready records contain no password
or password hash and exist only under `/run/psx-ssh-access`. Cleanup locks the
identified temporary credential **before** any key validation, USB persistence,
or policy reload involved in closing/importing. A stalled USB cannot extend
password access while holding the shared setup lock. Repeated enable requests
are rejected before USB access. The guardian binds its owner to the PID,
process start time and boot ID, so PID reuse cannot keep the window open.
Cleanup locks before validating/reloading key-only policy, so a failed
reload cannot leave the generated password usable. Reboot discards the RAM
credential/policy. This relies on the diagnostic operating system and systemd
continuing to execute; it is not protection against a malicious root process.

SSH state, key and policy paths reject symlinks. One image-owned OS alias is
recognized separately: `/etc/ssh/sshd_config.d/20-systemd-userdb.conf` may point
only to protected `/usr/lib/systemd/sshd_config.d/20-systemd-userdb.conf`, whose
active content must be exactly the expected root `userdbctl ssh-authorized-keys`
command and its root command user. Both are checked again in effective sshd
policy. The alias/target is preserved; this exception does not apply to keys,
state, other config aliases, writable targets or extra directives.

The unseeded public image starts with root locked and password authentication
disabled. It prepares a unique host identity independently, but starts SSH only
after explicit enrollment. A later boot may start key-only SSH to restore an
already enrolled USB installation. No default or shared password is shipped.

## Integration contract

`SSHAccess(root_prefix="/", executor=None, fetcher=None, clock=None)` performs
no operations during import/construction. Call `initialize()` once at real
controller startup after host-key preparation. Fixture roots require injected
commands and fetches; no test contacts GitHub or a real SSH daemon.

| Root Unix IPC action | Backend method | Public result |
| --- | --- | --- |
| `ssh.info` | `info()` | Login, public fingerprints, window status/seconds |
| `ssh.github.preview` | `github_preview(username)` | One-use preview ID, username, fingerprints, 300-second expiry |
| `ssh.github.add` | `github_add(preview_id)` | Added/total keys, `persisted`, `ssh_ready`, and any readiness warning |
| `ssh.password.enable` | `password_enable()` | **Secret once:** password, root login, 600-second window, instructions |
| `ssh.password.disable` | `password_disable()` | Closed status only |

The controller enforces local root peer credentials, action readiness and
single-action exclusion. None of these actions is exposed through HTTP.
The TUI must intercept SSH replies before generic result/history handling,
clear the secret modal on dismissal/expiry, and never copy its password to a
diagnostic report. `tick()` and `guardian()` are internal lifecycle methods.
If confirmed keys were saved but subsequent SSH validation/start failed, Add
returns `persisted: true`, `ssh_ready: false` and explicit guidance. The keys
remain enrolled; do not interpret that result as a rollback or add them again.

GitHub lookup is limited to the HTTPS public endpoint
`https://api.github.com/users/USERNAME/keys?per_page=33`: validated 1–39 character
username, no redirects/proxies/auth token, bounded response, at most 32 keys,
and bounded request/key-validation time. A failed lookup/validation does not
fall back to passwords or change existing authorizations.

Primary source basis:

* GitHub documents unauthenticated public-user key lookup and its JSON key
  list: https://docs.github.com/en/rest/users/keys#list-public-keys-for-a-user
* OpenSSH documents first-value policy, `AuthenticationMethods`, root/password
  controls and authorized-key paths: https://man.openbsd.org/sshd_config
* OpenSSH documents configuration testing and listener reload on SIGHUP:
  https://man.openbsd.org/sshd
* OpenSSH documents SHA-256 fingerprint validation with `ssh-keygen -l -E`:
  https://man.openbsd.org/ssh-keygen
* Linux documents numeric process directories, PID reuse and process start
  time in `/proc/PID/stat`: https://docs.kernel.org/filesystems/proc.html

Run `python3 -m unittest discover -s tests -p 'test_ps4_diagnostics_ssh_access.py'`.
These tests use synthetic files, fetches, clocks and commands; they do not
qualify the feature on the console or alter a working SSH connection.

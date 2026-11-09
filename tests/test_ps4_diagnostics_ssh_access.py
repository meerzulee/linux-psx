"""SSH enrollment against synthetic files/commands; never a real SSH service."""
import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("ssh_access_fixture", str(ROOT / "diagnostics/ps4/ssh_access.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
APP = importlib.util.module_from_spec(spec); loader.exec_module(APP)


def public_key(byte):
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([byte]) * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


class SSHAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.now = 1000.0
        self.commands = []; self.active = True; self.fail = None
        self.owner = public_key(1); self.imported = public_key(2)
        self.fetched = []; self.response = json.dumps([{"id": 42, "key": self.imported}]).encode()
        device = self.root.stat().st_dev
        self.write(f"/proc/{os.getpid()}/mountinfo", "\n".join([
            "1 0 0:1 / / ro - ext4 /dev/loop0 ro",
            *[f"{i} 1 0:2 /{name} /{name} rw - tmpfs tmpfs rw" for i, name in enumerate(("etc", "root", "run"), 2)],
            f"7 4 {os.major(device)}:{os.minor(device)} / /run/psx-media rw - vfat /dev/synthetic rw"]))
        self.path("/proc/self").symlink_to(str(os.getpid()), target_is_directory=True)
        self.write("/proc/sys/kernel/osrelease", APP.RELEASE)
        self.write("/proc/sys/kernel/random/boot_id", "12345678-1234-1234-1234-123456789abc")
        self.write(f"/proc/{os.getpid()}/stat", self.process_stat())
        self.write("/proc/cmdline", "psx.diag_uuid=ABCD-1234")
        self.write("/run/psx-media/psx-diagnostics/installed.json", json.dumps({
            "owner": "psx-diagnostics-usb-v1", "verified_readback": True, "target": {"uuid": "ABCD-1234"}}))
        self.path("/run/psx-media").chmod(0o700)
        self.write(APP.POLICY, "\n".join(k + " " + v for k, v in APP.SSHAccess.policy_values(False, original=True).items()) + "\n")
        self.write("/etc/ssh/sshd_config", "Include /etc/ssh/sshd_config.d/*.conf\nAuthorizedKeysFile .ssh/authorized_keys\n")
        self.write(APP.OWNER, self.owner + " owner\n")
        self.write("/etc/shadow", "root:!:20000:0:99999:7:::\npsx:!:20000:0:99999:7:::\n")
        self.access = self.new_access()
        self.access.initialize(); self.commands.clear()

    def path(self, name): return self.root / name.lstrip("/")

    def process_stat(self, start=123456, state="S"):
        return f"{os.getpid()} (fixture controller) {state} " + "0 " * 18 + str(start) + " 0\n"

    def write(self, name, data):
        path = self.path(name); path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data); path.chmod(0o600)

    def fetch(self, username):
        self.fetched.append(username)
        return self.response

    def new_access(self):
        return APP.SSHAccess(self.root, executor=self.execute, fetcher=self.fetch, clock=lambda: self.now)

    def execute(self, argv, timeout, data):
        # Record only arguments, exactly as an ordinary process listing would.
        self.commands.append((argv, timeout))
        tool = Path(argv[0]).name; args = argv[1:]; output = b""; code = 0
        if tool == "ssh-keygen":
            blob = base64.b64decode(data.split()[1])
            fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).rstrip(b"=").decode()
            output = ("256 " + fingerprint + " fixture (ED25519)\n").encode()
            if self.fail == "key-validation": code = 1
        elif tool == "openssl":
            self.assertEqual(args, ["passwd", "-6", "-stdin"])
            self.assertNotIn(data.decode().strip(), repr(argv))
            output = ("$6$fixturesalt$" + "a" * 86 + "\n").encode()
            if self.fail == "password-error": raise RuntimeError(data.decode())
        elif tool == "sshd":
            if args[0] == "-T":
                output = self.path(APP.POLICY).read_bytes()
                if self.path(APP.USERDB_ALIAS).is_symlink():
                    output += "".join(key + " " + value + "\n" for key, value in APP.USERDB_POLICY.items()).encode()
                if self.fail == "policy":
                    output = output.replace(b"pubkeyauthentication yes", b"pubkeyauthentication no")
        elif tool == "systemctl":
            if args[0] == "is-active": code = 0 if self.active else 3
            elif args[0] == "start": self.active = True
            elif args[0] == "reload":
                if self.fail == "reload": code = 1
            else: raise AssertionError("Unexpected service action")
        elif tool == "systemd-run":
            lease = json.loads(self.path(APP.RUNTIME + "/lease.json").read_text())
            self.write(APP.RUNTIME + "/ready", lease["id"])
        else: raise AssertionError("Unexpected synthetic tool")
        return subprocess.CompletedProcess(argv, code, output, b"fixture private stderr")

    def bytes_on_disk(self):
        return b"\n".join(path.read_bytes() for path in self.root.rglob("*") if path.is_file())

    def test_preview_is_read_only_and_confirmation_merges_preserving_owner(self):
        before = self.bytes_on_disk()
        preview = self.access.github_preview("octocat")
        self.assertEqual(self.fetched, ["octocat"])
        self.assertEqual(self.bytes_on_disk(), before)
        self.assertEqual(len(preview["fingerprints"]), 1)
        result = self.access.github_add(preview["preview_id"])
        self.assertEqual((result["added"], result["total"], result["persisted"]), (1, 2, True))
        for name in (APP.PERSISTENT, APP.AUTHORIZED):
            self.assertEqual(set(self.path(name).read_text().splitlines()), {self.owner, self.imported})
            self.assertEqual(stat.S_IMODE(self.path(name).stat().st_mode), 0o600)
        self.assertIn(self.owner, self.path(APP.OWNER).read_text())
        with self.assertRaises(APP.AccessError): self.access.github_add(preview["preview_id"])

    def test_username_url_injection_and_expired_preview_are_refused(self):
        for name in ("https://evil.example/x", "@octocat", "../root", "-name", "a--b", "x" * 40, "a\nroot"):
            with self.subTest(name=name), self.assertRaises(APP.AccessError): self.access.github_preview(name)
        self.assertEqual(self.fetched, [])
        preview = self.access.github_preview("octocat"); self.now += 301
        with self.assertRaisesRegex(APP.AccessError, "expired"): self.access.github_add(preview["preview_id"])
        self.assertNotIn(self.imported, self.path(APP.AUTHORIZED).read_text())

    def test_bounded_key_list_and_openssh_validation_have_no_auth_fallback(self):
        for value in ([], None, [{"key": self.imported}] * 33, [{"key": self.imported + "\n" + self.owner}], [{"key": "command=evil " + self.imported}]):
            self.response = json.dumps(value).encode()
            with self.subTest(value=value), self.assertRaises(APP.AccessError): self.access.github_preview("octocat")
        self.response = b"x" * (APP.MAX_BYTES + 1)
        with self.assertRaises(APP.AccessError): self.access.github_preview("octocat")
        self.response = json.dumps([{"key": self.imported}]).encode(); self.fail = "key-validation"
        with self.assertRaises(APP.AccessError): self.access.github_preview("octocat")
        self.assertEqual(self.access.root_credential(), "!")
        self.assertNotIn("passwordauthentication yes", self.path(APP.POLICY).read_text())

    def test_password_exists_only_in_one_result_and_expires_to_key_only(self):
        result = self.access.password_enable(); secret = result["password"]
        self.assertGreaterEqual(len(secret), 32); self.assertEqual(result["expires_in_seconds"], 600)
        self.assertTrue(self.access.info()["password_enabled"])
        self.assertNotIn(secret.encode(), self.bytes_on_disk())
        self.assertNotIn(secret, json.dumps(self.access.info()))
        self.assertNotIn(secret, repr(self.commands))
        self.assertTrue(self.access.root_credential().startswith("$6$"))
        self.assertTrue(any(Path(argv[0]).name == "systemd-run" for argv, _ in self.commands))
        self.now += 601; self.assertFalse(self.access.tick())
        self.assertEqual(self.access.root_credential(), "!")
        self.assertFalse(self.access.info()["password_enabled"])
        self.assertIn("passwordauthentication no", self.path(APP.POLICY).read_text())
        self.assertIn(self.owner, self.path(APP.AUTHORIZED).read_text())

    def test_controller_restart_and_owner_exit_close_temporary_access(self):
        self.access.password_enable()
        replacement = self.new_access(); replacement.initialize()
        self.assertEqual(replacement.root_credential(), "!")
        self.assertIsNone(replacement.lease())
        replacement.password_enable(); self.assertFalse(replacement.tick(owner_alive=False))
        self.assertEqual(replacement.root_credential(), "!")

    def test_independent_guardian_restores_locked_key_only_state_at_deadline(self):
        self.access.password_enable(); self.now += 601
        guardian = self.new_access()
        with mock.patch.object(APP.os, "kill", return_value=None): guardian.guardian()
        self.assertEqual(guardian.root_credential(), "!")
        self.assertIsNone(guardian.lease())

    def test_persistent_key_directory_cannot_cross_filesystems(self):
        inode = self.path(str(Path(APP.PERSISTENT).parent)).stat().st_ino
        actual = os.fstat
        def changed(fd):
            value = actual(fd)
            return SimpleNamespace(st_dev=value.st_dev + 1) if value.st_ino == inode else value
        with mock.patch.object(APP.os, "fstat", side_effect=changed):
            with self.assertRaisesRegex(APP.AccessError, "crosses"): self.access.merge()
        self.assertIn(self.owner, self.path(APP.PERSISTENT).read_text())

    def test_ssh_copy_id_key_is_persisted_before_password_window_closes(self):
        self.access.password_enable()
        self.write(APP.AUTHORIZED, self.owner + "\n" + self.imported + " laptop\n")
        self.assertFalse(self.access.tick())
        self.assertIn(self.imported, self.path(APP.PERSISTENT).read_text())
        self.assertEqual(self.access.root_credential(), "!")
        self.write(APP.AUTHORIZED, "")
        self.new_access().initialize()
        self.assertIn(self.imported, self.path(APP.AUTHORIZED).read_text())

    def test_git_import_closes_active_password_window(self):
        self.access.password_enable()
        preview = self.access.github_preview("octocat")
        self.access.github_add(preview["preview_id"])
        self.assertFalse(self.access.info()["password_enabled"])
        self.assertEqual(self.access.root_credential(), "!")

    def test_tick_locks_before_failing_or_blocking_public_key_persistence(self):
        self.access.password_enable()
        self.write(APP.AUTHORIZED, self.owner + "\n" + self.imported + "\n")
        self.now += 601
        def blocked_merge(*_):
            self.assertEqual(self.access.root_credential(), "!")
            raise OSError("synthetic USB persistence failure")
        with mock.patch.object(self.access, "merge", side_effect=blocked_merge):
            with self.assertRaises(OSError): self.access.tick()
        self.assertEqual(self.access.root_credential(), "!")

    def test_github_add_locks_before_failing_or_blocking_persistence(self):
        preview = self.access.github_preview("octocat")
        self.access.password_enable()
        def blocked_merge(*_):
            self.assertEqual(self.access.root_credential(), "!")
            raise OSError("synthetic USB persistence failure")
        with mock.patch.object(self.access, "merge", side_effect=blocked_merge):
            with self.assertRaises(OSError): self.access.github_add(preview["preview_id"])
        self.assertEqual(self.access.root_credential(), "!")

    def test_repeated_enable_rejects_before_usb_or_policy_checks(self):
        self.access.password_enable()
        with mock.patch.object(self.access, "media", side_effect=AssertionError("must not inspect USB")), \
                mock.patch.object(self.access, "effective", side_effect=AssertionError("must not wait for sshd")):
            with self.assertRaisesRegex(APP.AccessError, "already open"): self.access.password_enable()

    def test_committed_import_reports_partial_success_if_ssh_readiness_fails(self):
        preview = self.access.github_preview("octocat")
        with mock.patch.object(self.access, "close_password", side_effect=APP.AccessError("synthetic readiness failure")):
            result = self.access.github_add(preview["preview_id"])
        self.assertTrue(result["persisted"]); self.assertFalse(result["ssh_ready"])
        self.assertIn("Do not add them again", result["message"])
        self.assertIn(self.imported, self.path(APP.PERSISTENT).read_text())
        self.assertIn(self.owner, self.path(APP.AUTHORIZED).read_text())

    def test_pid_reuse_or_changed_boot_cannot_keep_the_password_window_open(self):
        for change in ("pid", "boot"):
            with self.subTest(change=change):
                self.access.password_enable()
                if change == "pid": self.write(f"/proc/{os.getpid()}/stat", self.process_stat(start=999999))
                else: self.write("/proc/sys/kernel/random/boot_id", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
                self.new_access().guardian()
                self.assertEqual(self.access.root_credential(), "!")
                self.assertIsNone(self.access.lease())

    def test_locked_preexisting_password_hash_is_preserved_and_not_eligible(self):
        for credential in ("!$6$owner-password-hash", "*", "!!"):
            with self.subTest(credential=credential):
                self.access.root_credential(credential)
                with self.assertRaisesRegex(APP.AccessError, "placeholder"): self.access.password_enable()
                self.assertEqual(self.access.root_credential(), credential)
                self.assertIsNone(self.access.lease())

    def test_guardian_failure_cannot_open_password_authentication(self):
        with mock.patch.object(self.access, "start_guard", side_effect=APP.AccessError("fixture guardian failure")):
            with self.assertRaises(APP.AccessError): self.access.password_enable()
        self.assertEqual(self.access.root_credential(), "!")
        self.assertIsNone(self.access.lease())
        self.assertIn("passwordauthentication no", self.path(APP.POLICY).read_text())

    def test_failed_reload_still_locks_temporary_credential(self):
        self.access.password_enable(); self.fail = "reload"
        with self.assertRaises(APP.AccessError): self.access.password_disable()
        self.assertEqual(self.access.root_credential(), "!")
        # The previous listener policy is retained if reload itself is broken,
        # but the owned temporary credential is already unusable.
        self.assertIsNotNone(self.access.lease())
        self.assertFalse(self.access.info()["password_enabled"])
        self.fail = None
        self.assertFalse(self.access.tick())
        self.assertIsNone(self.access.lease())

    def test_policy_failure_restores_exact_previous_configuration_and_owner_key(self):
        original = self.path(APP.POLICY).read_bytes()
        self.fail = "policy"
        with self.assertRaises(APP.AccessError): self.access.configure(True)
        self.assertEqual(self.path(APP.POLICY).read_bytes(), original)
        self.assertIn(self.owner, self.path(APP.AUTHORIZED).read_text())

    def test_unknown_baseline_is_not_rewritten_and_does_not_change_owner_credential(self):
        self.write(APP.POLICY, "# owner custom policy\nPasswordAuthentication no\n")
        before = self.path(APP.POLICY).read_bytes()
        self.access.root_credential("!owner-locked")
        with self.assertRaisesRegex(APP.AccessError, "differs"): self.access.initialize()
        self.assertEqual(self.path(APP.POLICY).read_bytes(), before)
        self.assertEqual(self.access.root_credential(), "!owner-locked")
        self.assertIn(self.owner, self.path(APP.AUTHORIZED).read_text())

    def test_corrupt_lease_closes_owned_policy_without_guessing_unknown_credential(self):
        self.access.password_enable()
        self.access.root_credential("!externally-locked")
        self.write(APP.RUNTIME + "/lease.json", "null")
        with self.assertRaises(APP.AccessError): self.new_access().initialize()
        self.assertEqual(self.access.root_credential(), "!externally-locked")
        self.assertIn("passwordauthentication no", self.path(APP.POLICY).read_text())
        self.assertIn(self.owner, self.path(APP.AUTHORIZED).read_text())

    def test_unrelated_credential_change_is_preserved_while_password_policy_closes(self):
        self.access.password_enable(); self.access.root_credential("!externally-locked")
        self.access.password_disable()
        self.assertEqual(self.access.root_credential(), "!externally-locked")

    def test_unlocked_account_or_wrong_effective_policy_refuses_enable(self):
        self.access.root_credential("$6$existing-user-password")
        with self.assertRaisesRegex(APP.AccessError, "locked root"): self.access.password_enable()
        self.access.root_credential("!"); self.fail = "policy"
        with self.assertRaisesRegex(APP.AccessError, "policy differs"): self.access.password_enable()
        self.assertIsNone(self.access.lease())
        self.assertFalse(any(Path(argv[0]).name == "openssl" for argv, _ in self.commands))

    def test_empty_owner_stays_key_only_inactive_until_explicit_enrollment(self):
        self.write(APP.OWNER, ""); self.write(APP.PERSISTENT, ""); self.write(APP.AUTHORIZED, ""); self.active = False
        self.access.initialize(); self.assertFalse(self.active)
        self.assertEqual(self.access.root_credential(), "!")
        self.access.password_enable(); self.assertTrue(self.active)
        self.access.password_disable(); self.active = False
        preview = self.access.github_preview("octocat"); self.access.github_add(preview["preview_id"])
        self.assertTrue(self.active)

    def test_a_key_seeded_by_a_development_ram_boot_starts_sshd_with_that_key(self):
        """What init-ram leaves behind, with no comment, read as the RAM backend does."""
        self.path(APP.AUTHORIZED).unlink()
        self.write(APP.OWNER, "")
        seeded = public_key(7)
        self.write("/run/psx-state/state/ssh/authorized_keys", seeded + "\n")
        self.path("/run/psx-state").chmod(0o700); self.path("/run/psx-state/state").chmod(0o700)
        self.active = False
        with mock.patch.object(APP, "ram_state", SimpleNamespace(active=lambda root: True, validate=lambda root, fixture: None)):
            info = self.new_access().initialize()
        self.assertEqual(self.path(APP.AUTHORIZED).read_text(), seeded + "\n")
        self.assertEqual(stat.S_IMODE(self.path(APP.AUTHORIZED).stat().st_mode), 0o600)
        self.assertEqual(len(info["key_fingerprints"]), 1)
        started = [argv[1:] for argv, _ in self.commands if Path(argv[0]).name == "systemctl" and argv[1] == "start"]
        self.assertEqual(started, [["start", "sshd.service"]])
        self.assertIn(APP.AUTHORIZED, self.path(APP.POLICY).read_text())

    def test_nonram_configuration_and_wrong_usb_are_rejected(self):
        path = self.path("/proc/self/mountinfo")
        original = path.read_text(); path.write_text(original.replace("- tmpfs", "- ext4"))
        with self.assertRaisesRegex(APP.AccessError, "RAM-backed"): self.access.password_enable()
        path.write_text(original)
        self.write("/proc/cmdline", "psx.diag_uuid=EEEE-FFFF")
        preview = self.access.github_preview("octocat")
        with self.assertRaisesRegex(APP.AccessError, "identity differs"): self.access.github_add(preview["preview_id"])
        self.assertEqual(self.access.root_credential(), "!")

    def test_proc_self_symlink_uses_numeric_pid_without_relaxing_state_paths(self):
        contents = self.path("/proc/self/mountinfo").read_bytes()
        self.write("/proc/424242/mountinfo", contents)
        self.path("/proc/self").unlink()
        self.path("/proc/self").symlink_to("424242", target_is_directory=True)
        with mock.patch.object(APP.os, "getpid", return_value=424242):
            self.access.environment()
            self.assertTrue(self.access.mounts())
        with self.assertRaisesRegex(APP.AccessError, "symlinks"):
            self.access.path("/proc/self/mountinfo")
        state = self.path(APP.AUTHORIZED)
        state.unlink(); state.symlink_to(self.path(APP.OWNER))
        with self.assertRaisesRegex(APP.AccessError, "symlinks"):
            self.access.read(APP.AUTHORIZED)

    def test_fixed_systemd_userdb_alias_is_supported_without_general_symlink_access(self):
        content = "# SPDX-License-Identifier: LGPL-2.1-or-later\n" + "\n".join(
            key + " " + value for key, value in APP.USERDB_POLICY.items()) + "\n"
        self.write(APP.USERDB_TARGET, content)
        self.path(APP.USERDB_ALIAS).symlink_to(APP.USERDB_TARGET)
        self.access.environment(); self.access.effective(False)
        self.assertEqual(self.path(APP.USERDB_TARGET).read_text(), content)
        with self.assertRaisesRegex(APP.AccessError, "symlinks"): self.access.path(APP.USERDB_ALIAS)
        self.write(APP.USERDB_TARGET, content + "PasswordAuthentication yes\n")
        with self.assertRaisesRegex(APP.AccessError, "userdb policy differs"): self.access.environment()

    def camelcase_effective_policy(self):
        self.write(APP.USERDB_TARGET, "AuthorizedKeysCommand /usr/bin/userdbctl ssh-authorized-keys %u\nAuthorizedKeysCommandUser root\n")
        self.path(APP.USERDB_ALIAS).symlink_to(APP.USERDB_TARGET)
        # OpenSSH_10.5p1's observed -T spelling, including the image userdb policy.
        return ("Port 22\nHostKey /run/psx-ssh/ssh_host_ed25519_key\n"
                "AuthorizedKeysFile /etc/psx-diagnostics/authorized_keys /root/.ssh/authorized_keys\n"
                "PermitRootLogin prohibit-password\nAllowUsers root\nAuthenticationMethods publickey\n"
                "PubkeyAuthentication yes\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n"
                "PermitEmptyPasswords no\nUsePAM yes\nStrictModes yes\n"
                "AuthorizedKeysCommand /usr/bin/userdbctl ssh-authorized-keys %u\n"
                "AuthorizedKeysCommandUser root\n").encode()

    def test_camelcase_effective_directives_with_userdb_are_accepted(self):
        output = self.camelcase_effective_policy()
        with mock.patch.object(self.access, "command", return_value=(0, output)):
            self.access.effective(False)

    def test_camelcase_effective_values_remain_exact_and_malformed_policy_is_refused(self):
        output = self.camelcase_effective_policy()
        for original, replacement in (
                (b"PasswordAuthentication no", b"PasswordAuthentication yes"),
                (b"PasswordAuthentication no", b"PasswordAuthentication No"),
                (b"AuthorizedKeysCommandUser root", b"AuthorizedKeysCommandUser Root"),
                (b"/usr/bin/userdbctl", b"/usr/bin/Userdbctl"),
                (b"AuthenticationMethods publickey\n", b"AuthenticationMethods\n")):
            with self.subTest(replacement=replacement), mock.patch.object(
                    self.access, "command", return_value=(0, output.replace(original, replacement))):
                with self.assertRaises(APP.AccessError): self.access.effective(False)

    def test_userdb_exception_rejects_other_aliases_targets_and_writable_target(self):
        content = "\n".join(key + " " + value for key, value in APP.USERDB_POLICY.items()) + "\n"
        self.write(APP.USERDB_TARGET, content)
        alias = self.path(APP.USERDB_ALIAS); alias.symlink_to(APP.USERDB_TARGET)
        self.path(APP.USERDB_TARGET).chmod(0o666)
        with self.assertRaises(APP.AccessError): self.access.environment()
        self.path(APP.USERDB_TARGET).chmod(0o600)
        alias.unlink(); alias.symlink_to("/tmp/other-userdb.conf")
        with self.assertRaisesRegex(APP.AccessError, "alias differs"): self.access.environment()
        alias.unlink()
        self.path("/etc/ssh/sshd_config.d/21-other.conf").symlink_to(APP.USERDB_TARGET)
        with self.assertRaisesRegex(APP.AccessError, "symlinks"): self.access.environment()

    def test_conditional_remote_policy_is_not_mistaken_for_localhost_policy(self):
        self.write("/etc/ssh/sshd_config.d/99-fixture.conf", "Match Address 192.0.2.0/24\nPasswordAuthentication yes\n")
        with self.assertRaisesRegex(APP.AccessError, "Conditional"): self.access.password_enable()
        self.assertEqual(self.access.root_credential(), "!")

    def test_secret_in_command_exception_is_never_returned_or_written(self):
        self.fail = "password-error"
        with mock.patch.object(APP.secrets, "token_urlsafe", return_value="synthetic-sensitive-password"):
            with self.assertRaises(APP.AccessError) as caught: self.access.password_enable()
        self.assertNotIn("synthetic-sensitive-password", str(caught.exception))
        self.assertNotIn(b"synthetic-sensitive-password", self.bytes_on_disk())
        self.assertIsNone(self.access.lease())

    def test_fixture_without_injections_never_runs_tools_or_network(self):
        other = APP.SSHAccess(self.root)
        with self.assertRaisesRegex(APP.AccessError, "never contact"): other.github_preview("octocat")
        with self.assertRaisesRegex(APP.AccessError, "never execute"): other.command("sshd", ["-t"])


if __name__ == "__main__": unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit local SSH enrollment; importing this module performs no operations.

Only root Unix IPC may call this API. The one-time password result must never
enter Jobs, state, reports, HTTP, or logs. All host tests inject their executor.
"""
from contextlib import contextmanager
import base64
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import subprocess
import sys
import threading
import time
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
if '/run/psx-ram' not in sys.path:
    sys.path.insert(1, '/run/psx-ram')
try:
    import ram_state
except ImportError:
    ram_state = None

RELEASE = "7.2.11-psx-ps4-baikal-native-dev3"
RUNTIME = "/run/psx-ssh-access"
PERSISTENT = "/run/psx-media/psx-diagnostics/state/ssh/authorized_keys"
OWNER = "/etc/psx-diagnostics/authorized_keys"
AUTHORIZED = "/root/.ssh/authorized_keys"
POLICY = "/etc/ssh/sshd_config.d/00-psx-diagnostics.conf"
USERDB_ALIAS = "/etc/ssh/sshd_config.d/20-systemd-userdb.conf"
USERDB_TARGET = "/usr/lib/systemd/sshd_config.d/20-systemd-userdb.conf"
USERDB_POLICY = {"authorizedkeyscommand": "/usr/bin/userdbctl ssh-authorized-keys %u",
                 "authorizedkeyscommanduser": "root"}
MAX_BYTES = 128 * 1024
MAX_KEYS = 32
TTL = 600
KEY_TYPES = {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
             "ecdsa-sha2-nistp521", "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com"}


class AccessError(ValueError):
    """Errors are fixed public explanations, never raw subprocess/network data."""


def github_fetch(username):
    """Fixed TLS endpoint, no proxy, credentials, redirects, or arbitrary URLs."""
    connection = http.client.HTTPSConnection("api.github.com", timeout=8, context=ssl.create_default_context())
    deadline = time.monotonic() + 11
    try:
        connection.request("GET", "/users/" + username + "/keys?per_page=33", headers={
            "Accept": "application/vnd.github+json", "User-Agent": "psx-diagnostics-key-preview",
            "X-GitHub-Api-Version": "2026-03-10"})
        if connection.sock: connection.sock.settimeout(max(0.01, min(3, deadline - time.monotonic())))
        response = connection.getresponse()
        if response.status != 200:
            raise AccessError("GitHub public keys are unavailable; check the username or try later.")
        data = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise AccessError("GitHub key lookup timed out.")
            if connection.sock: connection.sock.settimeout(min(remaining, 3))
            chunk = response.read1(min(4096, MAX_BYTES + 1 - len(data)))
            if not chunk: break
            data.extend(chunk)
            if len(data) > MAX_BYTES: raise AccessError("GitHub key response is too large.")
        return bytes(data)
    except AccessError: raise
    except Exception: raise AccessError("GitHub public-key lookup failed; no keys were changed.") from None
    finally: connection.close()


class SSHAccess:
    def __init__(self, root_prefix="/", executor=None, fetcher=None, clock=None):
        self.root = Path(root_prefix).resolve()
        self.fixture = self.root != Path("/")
        self.executor, self.fetcher = executor, fetcher
        self.clock = clock or time.monotonic
        self.previews = {}
        self.guard = threading.RLock()

    def ram(self):
        return ram_state is not None and ram_state.active(self.root)

    def persistent_path(self):
        return '/run/psx-state/state/ssh/authorized_keys' if self.ram() else PERSISTENT

    def path(self, name):
        path = self.root / name.lstrip("/")
        if path.resolve() != path or not path.is_relative_to(self.root):
            raise AccessError("SSH setup paths must not contain symlinks.")
        return path

    def read(self, name, missing=False, directory=None):
        try:
            fd = os.open(self.path(name) if directory is None else name,
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            if missing: return b""
            raise AccessError("Required diagnostic SSH setup files are missing.") from None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or info.st_mode & 0o022:
                raise AccessError("SSH setup needs bounded, protected regular files.")
            if directory is not None and info.st_dev != os.fstat(directory).st_dev:
                raise AccessError("SSH state crosses the selected filesystem.")
            if not self.fixture and info.st_uid != 0:
                raise AccessError("SSH setup files must belong to root.")
            value = stream.read(MAX_BYTES + 1)
        if len(value) > MAX_BYTES: raise AccessError("SSH setup input exceeds its bound.")
        return value

    def private_directory(self, name):
        directory = self.path(name)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or (not self.fixture and info.st_uid != 0):
            raise AccessError("SSH state directories must be private to root.")

    def atomic(self, name, value, directory=None):
        path = self.path(name) if directory is None else Path(name)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) if directory is None else os.dup(directory)
        temporary = "." + path.name + "." + secrets.token_hex(12)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(value); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try: os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError: pass
            os.close(parent)

    @contextmanager
    def locked(self):
        with self.guard:
            self.private_directory(RUNTIME)
            fd = os.open(self.path(RUNTIME + "/lock"), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally: os.close(fd)

    def command(self, tool, arguments, data=None, check=True, timeout=8):
        if self.fixture and self.executor is None:
            raise AccessError("Fixture roots never execute host tools.")
        argv = [str(self.root / "usr/bin" / tool), *arguments]
        try:
            if self.executor:
                result = self.executor(argv, timeout, data)
            else:
                result = subprocess.run(argv, input=data, capture_output=True, timeout=timeout, check=False,
                    env={"PATH": "/usr/bin:/usr/sbin", "LC_ALL": "C"}, start_new_session=True)
            output = result.stdout.encode() if isinstance(result.stdout, str) else result.stdout
            if len(output or b"") > MAX_BYTES or (check and result.returncode != 0):
                raise AccessError("The SSH setup command could not complete.")
            return result.returncode, output or b""
        except AccessError: raise
        except Exception: raise AccessError("The SSH setup command could not complete.") from None

    def mounts(self):
        rows = []
        # /proc/self is a kernel symlink. Address this process explicitly so
        # the strict no-symlink rule remains intact for all SSH state paths.
        for line in self.read(f"/proc/{os.getpid()}/mountinfo").decode().splitlines():
            left, right = line.split(" - ", 1)
            before, after = left.split(), right.split()
            rows.append({"target": before[4], "options": before[5].split(","), "fs": after[0], "device": before[2]})
        return rows

    def environment(self):
        if not self.fixture and os.geteuid() != 0: raise AccessError("SSH setup requires local root access.")
        if self.read("/proc/sys/kernel/osrelease").decode().strip() != RELEASE:
            raise AccessError("SSH setup requires the selected diagnostic kernel.")
        mounts = self.mounts()
        for path in ("/etc", "/root", "/run"):
            parents = [m for m in mounts if m["target"] == "/" or path == m["target"] or path.startswith(m["target"] + "/")]
            selected = max(parents, key=lambda m: len(m["target"]), default={})
            if selected.get("fs") != "tmpfs" or "rw" not in selected.get("options", []):
                raise AccessError("SSH setup requires the diagnostic RAM-backed /etc, /root and /run.")
        # A localhost -T check must describe every remote root login too.
        # Refuse address/user Match blocks or additional include trees instead
        # of claiming that one sampled connection proves their policy.
        main = self.path("/etc/ssh/sshd_config")
        configs = [main, *sorted(self.path("/etc/ssh/sshd_config.d").glob("*.conf"))]
        includes = 0
        for config in configs:
            relative = "/" + str(config.relative_to(self.root))
            content = self.read_os_ssh_config(relative)
            for line in content.decode().splitlines():
                words = line.split()
                if not words or words[0].startswith("#"): continue
                if words[0].lower() == "match": raise AccessError("Conditional SSH policy requires separate review.")
                if words[0].lower() == "include":
                    if config != main or words[1:] != ["/etc/ssh/sshd_config.d/*.conf"]:
                        raise AccessError("Additional SSH configuration includes require separate review.")
                    includes += 1
        if includes != 1: raise AccessError("The diagnostic SSH include contract differs.")
        parsed = {}
        for line in self.read(POLICY).decode().splitlines():
            words = line.split()
            if not words or words[0].startswith("#"): continue
            key = words[0].lower()
            if key in parsed: raise AccessError("Diagnostic SSH policy contains duplicate directives.")
            parsed[key] = " ".join(words[1:])
        if parsed not in (self.policy_values(False, original=True), self.policy_values(False), self.policy_values(True)):
            raise AccessError("Diagnostic SSH policy differs; preserve it for review.")

    def read_os_ssh_config(self, name):
        path = self.root / name.lstrip("/")
        if name != USERDB_ALIAS or not path.is_symlink(): return self.read(name)
        target = os.path.normpath(os.path.join(str(Path(USERDB_ALIAS).parent), os.readlink(path)))
        if target != USERDB_TARGET or (not self.fixture and path.lstat().st_uid != 0):
            raise AccessError("The image-owned SSH userdb alias differs.")
        content = self.read(USERDB_TARGET)
        directives = [line.split(None, 1) for line in content.decode().splitlines()
                      if line.strip() and not line.lstrip().startswith("#")]
        if len(directives) != 2 or any(len(parts) != 2 for parts in directives) or {
                key.lower(): value for key, value in directives} != USERDB_POLICY:
            raise AccessError("The image-owned SSH userdb policy differs.")
        return content

    @staticmethod
    def policy_values(temporary, original=False):
        return {"port": "22", "hostkey": "/run/psx-ssh/ssh_host_ed25519_key",
            "authorizedkeysfile": OWNER if original else OWNER + " " + AUTHORIZED,
            "permitrootlogin": "yes" if temporary else "prohibit-password", "allowusers": "root",
            "authenticationmethods": "publickey password" if temporary else "publickey",
            "pubkeyauthentication": "yes", "passwordauthentication": "yes" if temporary else "no",
            "kbdinteractiveauthentication": "no", "permitemptypasswords": "no", "usepam": "yes", "strictmodes": "yes"}

    def effective(self, temporary):
        self.command("sshd", ["-t"])
        _, data = self.command("sshd", ["-T", "-C", "user=root,host=localhost,addr=127.0.0.1"])
        values = {key.lower(): value for key, value in
                  (line.split(" ", 1) for line in data.decode().splitlines() if " " in line)}
        alias = self.root / USERDB_ALIAS.lstrip("/")
        if alias.exists() or alias.is_symlink():
            self.read_os_ssh_config(USERDB_ALIAS)
            if any(values.get(key) != item for key, item in USERDB_POLICY.items()):
                raise AccessError("Effective SSH userdb policy differs.")
        elif any(values.get(key, "none") != "none" for key in USERDB_POLICY):
            raise AccessError("An unrecognized SSH authorized-key command is configured.")
        expected = self.policy_values(temporary)
        for key in ("passwordauthentication", "authenticationmethods", "kbdinteractiveauthentication",
                    "permitemptypasswords", "pubkeyauthentication", "authorizedkeysfile", "allowusers", "usepam",
                    "port", "hostkey", "strictmodes"):
            if values.get(key) != expected[key]: raise AccessError("Effective SSH authentication policy differs.")
        if values.get("permitrootlogin") not in (("yes",) if temporary else ("prohibit-password", "without-password")):
            raise AccessError("Effective root SSH authentication policy differs.")

    def configure(self, temporary, start=False, restore=None):
        policy = "# Explicit local diagnostic SSH access; temporary changes exist only in RAM.\n"
        policy += "".join(key + " " + value + "\n" for key, value in self.policy_values(temporary).items())
        candidate = restore if restore is not None else policy.encode()
        previous = self.read(POLICY)
        try:
            if candidate != previous: self.atomic(POLICY, candidate)
            self.effective(temporary)
            active, _ = self.command("systemctl", ["is-active", "--quiet", "sshd.service"], check=False)
            if active == 0 and candidate != previous: self.command("systemctl", ["reload", "sshd.service"])
            elif active != 0 and (temporary or start): self.command("systemctl", ["start", "sshd.service"])
        except Exception:
            # Restore exact prior bytes; never replace an owner's configuration
            # with a candidate that failed its actual sshd checks.
            if candidate != previous:
                self.atomic(POLICY, previous)
                try:
                    self.command("sshd", ["-t"])
                    if self.command("systemctl", ["is-active", "--quiet", "sshd.service"], check=False)[0] == 0:
                        self.command("systemctl", ["reload", "sshd.service"])
                except AccessError: pass
            raise

    def media(self):
        if self.ram():
            ram_state.validate(self.root, fixture=self.fixture)
            self.private_directory('/run/psx-state/state/ssh')
            return self.path('/run/psx-state').stat().st_dev
        mounts = [m for m in self.mounts() if m["target"] == "/run/psx-media"]
        if len(mounts) != 1 or mounts[0]["fs"] != "vfat" or "rw" not in mounts[0]["options"]:
            raise AccessError("The selected diagnostic USB must be mounted writable.")
        media = self.path("/run/psx-media").stat()
        if media.st_mode & 0o077 or (not self.fixture and media.st_uid != 0):
            raise AccessError("Diagnostic USB SSH state must be private to root.")
        if mounts[0]["device"] != f"{os.major(media.st_dev)}:{os.minor(media.st_dev)}":
            raise AccessError("Diagnostic USB filesystem identity differs.")
        uuids = [word.split("=", 1)[1] for word in self.read("/proc/cmdline").decode().split() if word.startswith("psx.diag_uuid=")]
        installed = json.loads(self.read("/run/psx-media/psx-diagnostics/installed.json"))
        if len(uuids) != 1 or not re.fullmatch(r"[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}", uuids[0]) or not isinstance(installed, dict):
            raise AccessError("Diagnostic USB installation identity is missing.")
        target = installed.get("target")
        if (installed.get("owner") != "psx-diagnostics-usb-v1" or installed.get("verified_readback") is not True
                or not isinstance(target, dict) or str(target.get("uuid", "")).upper() != uuids[0].upper()):
            raise AccessError("Diagnostic USB installation identity differs.")
        self.private_directory(str(Path(PERSISTENT).parent))
        return media.st_dev

    def keys(self, data, deadline=None):
        if len(data) > MAX_BYTES: raise AccessError("Public keys exceed the supported size.")
        try: lines = data.decode("utf-8").splitlines()
        except UnicodeError: raise AccessError("Public keys must be UTF-8 text.") from None
        keys = {}
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split()
            if len(line) > 8192 or len(parts) < 2 or parts[0] not in KEY_TYPES:
                raise AccessError("Use ordinary OpenSSH public keys without authorization options.")
            try:
                blob = base64.b64decode(parts[1], validate=True)
                count = int.from_bytes(blob[:4], "big")
                if blob[4:4 + count].decode() != parts[0]: raise ValueError
            except (ValueError, UnicodeError): raise AccessError("Invalid public-key encoding.") from None
            canonical = parts[0] + " " + parts[1]
            fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
            remaining = 2 if deadline is None else min(2, deadline - self.clock())
            if remaining <= 0: raise AccessError("Public-key preview timed out.")
            _, output = self.command("ssh-keygen", ["-l", "-E", "sha256", "-f", "/dev/stdin"],
                                     (canonical + "\n").encode(), timeout=remaining)
            fields = output.decode().split()
            if len(fields) < 2 or fields[1] != fingerprint:
                raise AccessError("OpenSSH did not validate the public key.")
            keys[canonical] = fingerprint
            if len(keys) > MAX_KEYS: raise AccessError("At most 32 public keys may be enrolled.")
        return keys

    def merge(self, additions=None):
        device = self.media()
        self.private_directory("/root/.ssh")
        directory = os.open(self.path(str(Path(self.persistent_path()).parent)), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(directory).st_dev != device:
                raise AccessError("SSH state crosses the selected USB filesystem.")
            merged = self.keys(self.read(OWNER, missing=True))
            merged.update(self.keys(self.read("authorized_keys", missing=True, directory=directory)))
            merged.update(self.keys(self.read(AUTHORIZED, missing=True)))
            merged.update(additions or {})
            if len(merged) > MAX_KEYS: raise AccessError("At most 32 public keys may be enrolled.")
            data = ("\n".join(merged) + ("\n" if merged else "")).encode()
            # Hold the selected USB directory through full readback; a later
            # reboot can finish any interrupted RAM-copy step.
            self.atomic("authorized_keys", data, directory=directory)
            if self.read("authorized_keys", directory=directory) != data:
                raise AccessError("Persistent public-key readback differs.")
            self.atomic(AUTHORIZED, data)
            return merged
        finally: os.close(directory)

    def lease(self):
        raw = self.read(RUNTIME + "/lease.json", missing=True)
        if not raw: return None
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or not re.fullmatch(r"[a-f0-9]{32}", value["id"]): raise ValueError
            if type(value["deadline"]) not in (int, float) or not 0 < value["deadline"] < float("inf") or type(value["owner_pid"]) is not int or value["owner_pid"] <= 0: raise ValueError
            if type(value["active"]) is not bool: raise ValueError
            if type(value["owner_start_ticks"]) is not int or value["owner_start_ticks"] <= 0: raise ValueError
            if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value["owner_boot_id"]): raise ValueError
            if any(not re.fullmatch(r"[a-f0-9]{64}", value[key]) for key in ("credential_sha256", "keys_sha256")): raise ValueError
            if not isinstance(value["previous_policy"], str) or len(value["previous_policy"]) > 8192: raise ValueError
            previous = dict(line.split(None, 1) for line in value["previous_policy"].splitlines() if line.strip() and not line.lstrip().startswith("#"))
            if {key.lower(): item for key, item in previous.items()} != self.policy_values(False): raise ValueError
            return value
        except (ValueError, TypeError, KeyError): raise AccessError("Temporary SSH lease metadata is invalid.") from None

    def write_lease(self, lease): self.atomic(RUNTIME + "/lease.json", (json.dumps(lease) + "\n").encode())

    def root_credential(self, replacement=None):
        lines = self.read("/etc/shadow").decode().splitlines()
        indexes = [i for i, line in enumerate(lines) if line.startswith("root:")]
        if len(indexes) != 1: raise AccessError("Root credential metadata is ambiguous.")
        index = indexes[0]; fields = lines[index].split(":")
        if len(fields) != 9: raise AccessError("Root credential metadata is incomplete.")
        previous = fields[1]
        if replacement is not None:
            fields[1] = replacement; lines[index] = ":".join(fields)
            self.atomic("/etc/shadow", ("\n".join(lines) + "\n").encode())
        return previous

    def lock_owned_credential(self, lease):
        current = self.root_credential()
        matches = lease and hashlib.sha256(current.encode()).hexdigest() == lease["credential_sha256"]
        if matches:
            self.root_credential("!")
            lease["active"] = False
            self.write_lease(lease)

    def close_password(self, lease=None, start=False):
        self.lock_owned_credential(lease)
        # Lock first: a failed reload cannot leave the generated password usable.
        self.environment()
        self.configure(False, start=start, restore=lease["previous_policy"].encode() if lease else None)
        self.path(RUNTIME + "/lease.json").unlink(missing_ok=True)
        self.path(RUNTIME + "/ready").unlink(missing_ok=True)

    def initialize(self):
        with self.locked():
            self.environment()
            try: lease = self.lease()
            except AccessError:
                # Invalid metadata cannot authorize changing an unrelated root
                # credential; close only our recognized temporary SSH policy.
                if self.read(POLICY).startswith(b"# Explicit local diagnostic SSH access;"):
                    self.configure(False)
                raise
            self.close_password(lease)
            merged = self.merge()
            if merged:
                active, _ = self.command("systemctl", ["is-active", "--quiet", "sshd.service"], check=False)
                if active != 0: self.command("systemctl", ["start", "sshd.service"])
        return self.info()

    def info(self):
        lease = self.lease()
        keys = self.keys(self.read(OWNER, missing=True)); keys.update(self.keys(self.read(AUTHORIZED, missing=True)))
        seconds = max(0, int(lease["deadline"] - self.clock())) if lease else 0
        return {"login": "root", "password_enabled": bool(lease and lease.get("active") and seconds),
                "expires_in_seconds": seconds, "key_fingerprints": list(keys.values()), "github_available": True}

    def github_preview(self, username):
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", username) or "--" in username:
            raise AccessError("Enter a GitHub username, without a URL or @ sign.")
        if self.fixture and self.fetcher is None: raise AccessError("Fixture roots never contact GitHub.")
        deadline = self.clock() + 12
        try:
            data = (self.fetcher or github_fetch)(username)
            if not isinstance(data, bytes) or len(data) > MAX_BYTES: raise ValueError
            records = json.loads(data)
            if not isinstance(records, list) or not 1 <= len(records) <= MAX_KEYS: raise ValueError
            values = []
            for item in records:
                if not isinstance(item, dict) or not isinstance(item.get("key"), str) or "\n" in item["key"] or "\r" in item["key"]: raise ValueError
                values.append(item["key"])
            keys = self.keys(("\n".join(values) + "\n").encode(), deadline=deadline)
        except AccessError: raise
        except Exception: raise AccessError("GitHub returned no supported bounded public-key list.") from None
        if not keys: raise AccessError("This GitHub account has no usable public SSH keys.")
        with self.guard:
            self.previews.clear()
            identifier = secrets.token_hex(16)
            self.previews[identifier] = (self.clock() + 300, keys)
        return {"preview_id": identifier, "username": username, "fingerprints": list(keys.values()), "expires_in_seconds": 300}

    def github_add(self, preview_id):
        with self.locked():
            candidate = self.previews.pop(preview_id, None) if isinstance(preview_id, str) else None
            if not candidate or candidate[0] <= self.clock(): raise AccessError("Preview expired; fetch and confirm the fingerprints again.")
            lease = self.lease()
            # The shared lock must never protect a USB/key-validation wait
            # while the temporary credential remains usable.
            if lease: self.lock_owned_credential(lease)
            self.environment()
            before = self.keys(self.read(AUTHORIZED, missing=True)); before.update(self.keys(self.read(OWNER, missing=True)))
            try: merged = self.merge(candidate[1])
            except Exception:
                if lease:
                    try: self.close_password(lease)
                    except Exception: pass
                raise
            result = {"added": len(set(merged) - set(before)), "total": len(merged),
                      "fingerprints": list(merged.values()), "persisted": not self.ram(), "ssh_ready": False,
                      "state_lifetime": "this boot" if self.ram() else "USB installation"}
            try: self.close_password(lease, start=True)
            except Exception:
                result["message"] = ("Keys were saved, but SSH readiness could not be confirmed. "
                                     "Do not add them again; keep existing access and review SSH setup.")
                return result
            result["ssh_ready"] = True
            return result

    def owner_identity(self, pid):
        boot = self.read("/proc/sys/kernel/random/boot_id").decode().strip()
        raw = self.read(f"/proc/{pid}/stat").decode()
        try:
            if not raw.startswith(str(pid) + " ("): raise ValueError
            fields = raw.rsplit(") ", 1)[1].split()
            if fields[0] in ("Z", "X", "x"): raise ValueError
            start = int(fields[19])  # /proc/PID/stat field 22, after pid/comm.
            if start <= 0 or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", boot): raise ValueError
        except (ValueError, IndexError):
            raise AccessError("The SSH setup controller process identity is unavailable.") from None
        return {"owner_boot_id": boot, "owner_start_ticks": start}

    def start_guard(self, lease):
        helper = str(Path(__file__).resolve())
        self.command("systemd-run", ["--quiet", "--collect", "--unit=psx-ssh-access-" + lease["id"],
            "--property=Type=exec", "--property=Restart=on-failure", "--property=RestartSec=1",
            "--property=UMask=0077", "/usr/bin/python3", helper, "--guard"])
        deadline = self.clock() + 5
        while self.clock() < deadline:
            if self.read(RUNTIME + "/ready", missing=True).decode() == lease["id"]: return
            time.sleep(0.05)
        raise AccessError("Temporary SSH expiry guardian did not become ready.")

    def password_enable(self):
        with self.locked():
            if self.lease(): raise AccessError("A temporary SSH window is already open.")
            self.environment(); self.effective(False); self.media()
            original = self.root_credential()
            if original != "!":
                raise AccessError("Temporary access requires the diagnostic locked root placeholder (!).")
            password = secrets.token_urlsafe(24)
            _, output = self.command("openssl", ["passwd", "-6", "-stdin"], (password + "\n").encode())
            credential = output.decode().strip()
            if not re.fullmatch(r"\$6\$[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}", credential):
                raise AccessError("Temporary credential generation failed.")
            lease = {"id": secrets.token_hex(16), "deadline": self.clock() + TTL, "owner_pid": os.getpid(),
                     "credential_sha256": hashlib.sha256(credential.encode()).hexdigest(), "active": False,
                     "previous_policy": self.read(POLICY).decode(),
                     "keys_sha256": hashlib.sha256(self.read(AUTHORIZED, missing=True)).hexdigest()}
            lease.update(self.owner_identity(lease["owner_pid"]))
            self.write_lease(lease)
            try:
                self.start_guard(lease)
                self.root_credential(credential)
                self.configure(True)
                lease["active"] = True; self.write_lease(lease)
            except Exception:
                self.close_password(lease)
                raise AccessError("Temporary SSH access could not start; password access is closed.") from None
            return {"password": password, "login": "root", "expires_in_seconds": TTL,
                    "instructions": ["On your computer, run: ssh-copy-id root@<console-IP>",
                                     "Enter this temporary password when prompted.",
                                     "Password access closes after a key is installed or after 10 minutes."]}

    def password_disable(self):
        with self.locked():
            self.environment(); self.close_password(self.lease())
        return {"password_enabled": False, "message": "Temporary password access is closed."}

    def tick(self, owner_alive=True):
        """Lock first; optional persistence cannot extend password access."""
        with self.locked():
            lease = self.lease()
            if not lease: return False
            changed = hashlib.sha256(self.read(AUTHORIZED, missing=True)).hexdigest() != lease["keys_sha256"]
            if not lease["active"] or self.clock() >= lease["deadline"] or not owner_alive or changed:
                self.lock_owned_credential(lease)
                try:
                    if changed: self.merge()
                finally: self.close_password(lease)
                return False
            return True

    def guardian(self):
        self.environment()
        lease = self.lease()
        if not lease: return
        self.atomic(RUNTIME + "/ready", lease["id"].encode())
        while True:
            try: current = self.lease()
            except AccessError:
                # Our in-memory validated lease can still identify the owned
                # credential if the public metadata is later damaged.
                with self.locked(): self.close_password(lease)
                raise
            if not current or current["id"] != lease["id"]: return
            try:
                identity = self.owner_identity(lease["owner_pid"])
                alive = all(identity[key] == lease[key] for key in ("owner_boot_id", "owner_start_ticks"))
            except (AccessError, OSError): alive = False
            if not self.tick(owner_alive=alive): return
            time.sleep(1)


if __name__ == "__main__":
    # Fixed guardian entry point; never accepts or prints credentials.
    access = None
    try:
        if sys.argv[1:] != ["--guard"] or os.geteuid() != 0: raise AccessError("Local SSH guardian requires root.")
        access = SSHAccess(); access.guardian()
    except Exception:
        if access is not None:
            try:
                with access.locked():
                    lease = access.lease()
                    if lease: access.close_password(lease)
            except Exception: pass
        raise SystemExit("SSH access guardian stopped; preserve diagnostic state for review.") from None

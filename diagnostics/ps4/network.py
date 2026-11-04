#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""NetworkManager/SSH backend for the native PS4 diagnostic image.

No commands run on import. Fixture roots require an injected executor. The
executor contract is executor(argv: list[str], timeout: float), returning an
object with returncode, stdout and stderr (like subprocess.CompletedProcess).
Passwords occur only in private RAM keyfiles, never command arguments or errors.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid


RELEASE = "7.2.11-psx-ps4-baikal-native-dev3"
WIRED_UUID = "cc5a17a7-2026-4404-a418-7bdad2909d38"
MTS = "/sys/module/psx_mts_net/parameters"
MAX_OUTPUT = 1024 * 1024


class NetworkError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Files:
    def __init__(self, root_prefix="/"):
        self.root = Path(root_prefix).resolve()
        self.fixture = self.root != Path("/")

    def path(self, name):
        path = self.root / str(name).lstrip("/")
        if not path.resolve().is_relative_to(self.root):
            raise NetworkError("INVALID_PATH", "Path leaves the selected filesystem")
        return path

    def text(self, name):
        try:
            with self.path(name).open("rb") as stream:
                return stream.read(MAX_OUTPUT).decode("utf-8", "replace").strip()
        except OSError:
            return None

    def tool(self, name):
        for directory in ("/usr/bin", "/usr/sbin", "/bin", "/sbin"):
            path = self.path(directory + "/" + name)
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        raise NetworkError("TOOL_MISSING", "Required image tool is unavailable: " + name)


def execute(argv, timeout):
    """Bound trusted image tools and their descendants; never expose raw errors."""
    environment = {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "LC_ALL": "C", "LANG": "C",
                   "HOME": "/root", "NM_CLI_COLOR": "never"}
    process = None
    # Temporary output is anonymous, private, RAM-backed in the image; no logs.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=error, env=environment, start_new_session=True,
                                       close_fds=True, shell=False)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise NetworkError("TIMEOUT", "The selected network operation timed out")
                if max(os.fstat(output.fileno()).st_size, os.fstat(error.fileno()).st_size) > MAX_OUTPUT:
                    raise NetworkError("OUTPUT_LIMIT", "Network tool output exceeded its bound")
                time.sleep(0.025)
            output.seek(0); error.seek(0)
            return subprocess.CompletedProcess(argv, process.returncode,
                    output.read(MAX_OUTPUT).decode("utf-8", "replace"),
                    error.read(MAX_OUTPUT).decode("utf-8", "replace"))
        except OSError:
            raise NetworkError("COMMAND_FAILED", "The selected network tool could not start") from None
        finally:
            if process:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)


def fields(line):
    """Decode nmcli's escaped terse columns, including SSIDs containing colons."""
    result, value, escaped = [], [], False
    for character in line:
        if escaped:
            value.append(character); escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            result.append("".join(value)); value = []
        else:
            value.append(character)
    if escaped:
        value.append("\\")
    return result + ["".join(value)]


def interface_name(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}", value) is not None


def public_fingerprint(public):
    try:
        parts = public.split()
        blob = base64.b64decode(parts[1], validate=True)
        length = int.from_bytes(blob[:4], "big")
        if length > 64 or blob[4:4 + length].decode() != parts[0]:
            return None
        return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    except (ValueError, IndexError, UnicodeError):
        return None


class Network:
    def __init__(self, root_prefix="/", executor=None):
        self.fs = Files(root_prefix)
        self.executor = executor
        self._local = threading.local()

    def _command(self, tool, arguments, timeout=8, check=True):
        if self.fs.fixture and self.executor is None:
            raise NetworkError("FIXTURE_ONLY", "Fixture roots never execute host tools")
        deadline = getattr(self._local, "deadline", None)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkError("TIMEOUT", "The selected network operation reached its time limit")
            timeout = min(timeout, remaining)
        argv = [self.fs.tool(tool), *arguments]
        try:
            result = (self.executor or execute)(argv, timeout)
        except NetworkError:
            raise
        except Exception:
            # A fake executor or subprocess exception can carry private output.
            raise NetworkError("COMMAND_FAILED", "The selected network operation failed") from None
        if len(result.stdout or "") > MAX_OUTPUT or len(result.stderr or "") > MAX_OUTPUT:
            raise NetworkError("OUTPUT_LIMIT", "Network tool output exceeded its bound")
        if check and result.returncode != 0:
            raise NetworkError("COMMAND_FAILED", "NetworkManager could not complete the selected operation")
        return result

    def _nm(self, arguments, timeout=8, check=True):
        return self._command("nmcli", ["--colors", "no", *arguments], timeout, check)

    def _platform(self):
        if self.fs.text("/proc/sys/kernel/osrelease") != RELEASE:
            raise NetworkError("WRONG_KERNEL", "Network control requires the selected native PS4 kernel")

    @contextmanager
    def _operation(self):
        self._platform()
        directory = self.fs.path("/run/psx-diagnostics")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(directory / "network.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise NetworkError("BUSY", "Another connection operation is already running") from None
            self._local.deadline = time.monotonic() + 55
            yield
        finally:
            self._local.deadline = None
            os.close(fd)

    def _interfaces(self):
        result = self._nm(["--terse", "--escape", "yes", "--fields", "DEVICE,TYPE,STATE", "device", "status"])
        addresses = self._command("ip", ["-json", "-4", "address", "show"])
        try:
            records = json.loads(addresses.stdout)
            if not isinstance(records, list):
                raise ValueError
        except ValueError:
            raise NetworkError("INVALID_REPLY", "Address information was not readable") from None
        address_map = {r.get("ifname"): r for r in records if isinstance(r, dict)}
        interfaces = []
        for line in result.stdout.splitlines():
            row = fields(line)
            if len(row) != 3 or not interface_name(row[0]):
                continue
            name, kind, state = row
            address = address_map.get(name, {})
            ipv4 = []
            items = address.get("addr_info", [])
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("family") == "inet" and item.get("local"):
                    try:
                        ipv4.append(str(ipaddress.IPv4Address(item["local"])))
                    except ValueError:
                        continue
            interfaces.append({"name": name, "type": kind, "state": state,
                               "ipv4": ipv4, "mac": address.get("address")})
        return interfaces

    def _access_points(self, interface=None, rescan=False):
        arguments = ["--wait", "15", "--terse", "--escape", "yes", "--fields", "SSID,SIGNAL,SECURITY,IN-USE",
                     "device", "wifi", "list", "--rescan", "yes" if rescan else "no"]
        if interface:
            arguments += ["ifname", interface]
        result = self._nm(arguments, timeout=18 if rescan else 8)
        points = {}
        for line in result.stdout.splitlines():
            row = fields(line)
            if (len(row) != 4 or not row[0] or not row[1].isdigit() or
                    any(ord(c) < 32 or ord(c) == 127 for c in row[0])):
                continue
            ssid, strength, security, active = row
            point = {"ssid": ssid, "signal": min(100, max(0, int(strength))),
                     "security": security if security != "--" else "", "active": active == "*"}
            key = (ssid, point["security"])
            old = points.get(key)
            if old is None or (point["active"], point["signal"]) > (old["active"], old["signal"]):
                points[key] = point
        return sorted(points.values(), key=lambda p: (not p["active"], -p["signal"], p["ssid"]))

    def status(self):
        interfaces = self._interfaces()
        available = any(item["type"] == "wifi" for item in interfaces)
        enabled = self._nm(["radio", "wifi"]).stdout.strip() == "enabled" if available else False
        return {"interfaces": interfaces, "wifi": {"available": available, "enabled": enabled,
                "access_points": self._access_points() if available and enabled else []}}

    def _wifi_interface(self):
        # The PS4 vendor driver also exposes ap0/ap1 as NM "wifi" devices.
        # They are not the station used for infrastructure connections.
        interfaces = [i for i in self._interfaces() if i["type"] == "wifi"
                      and not re.fullmatch(r"ap\d+|p2p.*", i["name"])]
        if not interfaces:
            raise NetworkError("WIFI_UNAVAILABLE", "No Wi-Fi station interface is available")
        return min(interfaces, key=lambda i: (i["name"] != "wlan0", i["name"]))["name"]

    def wifi_scan(self):
        with self._operation():
            interface = self._wifi_interface()
            self._nm(["radio", "wifi", "on"])
            access_points = self._access_points(interface, rescan=True)
            return {"interfaces": self._interfaces(), "wifi": {
                "available": True, "enabled": True, "access_points": access_points}}

    def _disconnect(self, name):
        interface = next((item for item in self._interfaces() if item["name"] == name), None)
        if interface is None:
            raise NetworkError("NOT_READY", "The selected network interface is not available")
        active = interface["state"].startswith(("connected", "connecting"))
        if active:
            self._nm(["--wait", "15", "device", "disconnect", name], timeout=20)
            interface = next((item for item in self._interfaces() if item["name"] == name), None)
        disconnected = interface is not None and not interface["state"].startswith(("connected", "connecting"))
        return {"ok": disconnected, "interface": name,
                "state": "disconnected" if disconnected else "unconfirmed",
                "already_disconnected": not active,
                "message": ("Disconnected. SSH sessions using this link will close." if active and disconnected else
                            "This link is already disconnected." if disconnected else
                            "Disconnection could not be confirmed; no retry was performed.")}

    def wifi_disconnect(self):
        with self._operation():
            return self._disconnect(self._wifi_interface())

    def ethernet_disconnect(self):
        with self._operation():
            self._mts()
            result = self._disconnect("psx0")
            self._mts()
            return result

    def wifi_connect(self, ssid, password):
        if not isinstance(ssid, str) or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
            raise NetworkError("INVALID_SSID", "Choose a printable Wi-Fi name")
        try:
            ssid_bytes = ssid.encode("utf-8")
        except UnicodeError:
            raise NetworkError("INVALID_SSID", "The Wi-Fi name is not valid UTF-8") from None
        if not 1 <= len(ssid_bytes) <= 32:
            raise NetworkError("INVALID_SSID", "Wi-Fi names must contain 1–32 UTF-8 bytes")
        if not isinstance(password, str) or any(ord(c) < 32 or ord(c) == 127 for c in password):
            raise NetworkError("INVALID_PASSWORD", "The Wi-Fi password contains unsupported characters")
        with self._operation():
            interface = self._wifi_interface()
            self._nm(["radio", "wifi", "on"])
            matches = [p for p in self._access_points(interface) if p["ssid"] == ssid]
            if not matches:
                raise NetworkError("SSID_NOT_FOUND", "Scan first and select a visible Wi-Fi network")
            security = matches[0]["security"].upper()
            if any(word in security for word in ("802.1X", "EAP", "WEP")):
                raise NetworkError("UNSUPPORTED_AUTH", "This interface supports open and WPA personal networks; enterprise/WEP setup needs a separate profile")
            protected = "WPA" in security
            if security and not protected:
                raise NetworkError("UNSUPPORTED_AUTH", "This Wi-Fi security mode needs a separate profile")
            if protected:
                management = "sae" if "WPA3" in security and "WPA2" not in security else "wpa-psk"
                try:
                    encoded = password.encode("utf-8")
                except UnicodeError:
                    raise NetworkError("INVALID_PASSWORD", "The Wi-Fi password is not valid UTF-8") from None
                valid = (1 <= len(encoded) <= 63) if management == "sae" else (
                    8 <= len(encoded) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", password))
                if not valid:
                    message = ("Use a 1–63 byte WPA3 personal password" if management == "sae" else
                               "Use an 8–63 byte personal-network password or a 64-digit hexadecimal PSK")
                    raise NetworkError("INVALID_PASSWORD", message)
            elif password:
                raise NetworkError("INVALID_PASSWORD", "The selected open network does not use a password")
            identifier = str(uuid.uuid4())
            content = ("[connection]\nid=psx-wifi\nuuid=" + identifier +
                       "\ntype=wifi\ninterface-name=" + interface +
                       "\nautoconnect=false\nautoconnect-retries=1\nauth-retries=1\n\n[wifi]\nmode=infrastructure\nssid=" +
                       ";".join(str(b) for b in ssid_bytes) + ";\n")
            if protected:
                escaped = password.replace("\\", "\\\\").replace(" ", "\\s")
                content += "\n[wifi-security]\nkey-mgmt=" + management + "\npsk=" + escaped + "\n"
            content += "\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n"
            directory = self.fs.path("/run/NetworkManager/system-connections")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / ("psx-wifi-" + identifier + ".nmconnection")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            try:
                self._nm(["connection", "load", str(path)])
                self._nm(["--wait", "35", "connection", "up", "uuid", identifier, "ifname", interface], timeout=40)
                self._nm(["connection", "modify", "uuid", identifier, "connection.autoconnect", "yes"])
            except Exception:
                # A failed request does not leave a secret file available for autoactivation.
                path.unlink(missing_ok=True)
                try:
                    self._nm(["connection", "delete", "uuid", identifier], check=False)
                except NetworkError:
                    pass
                raise
            return {"ok": True, "interface": interface, "ssid": ssid, "state": "connected",
                    "credentials_persisted": False, "profile_scope": "current RAM-root boot"}

    def _mts(self):
        if self.fs.text(MTS + "/autoarm") not in ("1", "Y", "y", "yes"):
            raise NetworkError("WRONG_PROFILE", "Ethernet control requires the native automatic-open profile")
        try:
            report = json.loads(self.fs.text(MTS + "/report") or "null")
        except ValueError:
            report = None
        if not isinstance(report, dict) or report.get("initializing_or_going") or report.get("bound") != 1:
            raise NetworkError("NOT_READY", "The native Ethernet driver is not ready")
        flags = ("bound", "attempted", "published", "engine_live", "admin_up", "terminal_fault")
        if (any(type(report.get(key)) not in (int, bool) or report[key] not in (0, 1) for key in flags)
                or type(report.get("error")) is not int):
            raise NetworkError("INVALID_REPLY", "The native Ethernet report is incomplete")
        if report.get("terminal_fault") or report.get("error"):
            raise NetworkError("TERMINAL_STATE", "Ethernet reports a terminal/error state; preserve it for inspection")
        if report.get("attempted") and not report.get("published"):
            raise NetworkError("UNCERTAIN_STATE", "The first Ethernet publication did not complete; it will not be retried")
        if report.get("published") and report.get("engine_live") != 1:
            raise NetworkError("UNCERTAIN_STATE", "Published Ethernet is not live; it will not be reopened")
        return report

    def ethernet_connect(self):
        with self._operation():
            report = self._mts()
            interface = next((i for i in self._interfaces() if i["name"] == "psx0"), None)
            if not interface:
                raise NetworkError("NOT_READY", "The native Ethernet interface is not available")
            active = interface["state"].startswith(("connected", "connecting"))
            if not active:
                # Normal NM activation only: no gate writes, modprobe, reset, down or rearm.
                self._mts()
                self._nm(["--wait", "30", "connection", "up", "uuid", WIRED_UUID, "ifname", "psx0"], timeout=35)
            for attempt in range(16):
                report = self._mts()
                interface = next((i for i in self._interfaces() if i["name"] == "psx0"), None)
                if (interface and interface["state"].startswith("connected") and interface["ipv4"]
                        and report.get("published") == 1 and report.get("admin_up") == 1):
                    return {"ok": True, "interface": "psx0", "state": "connected", "ipv4": interface["ipv4"], "reused": active}
                if attempt == 15 or not interface or not interface["state"].startswith(("connected", "connecting")):
                    break
                time.sleep(0.25)
            return {"ok": False, "interface": "psx0", "state": "waiting", "message": "Waiting for Ethernet carrier/address; no interface restart was performed"}

    def ssh_status(self):
        result = self._command("systemctl", ["is-active", "--quiet", "sshd.service"], check=False)
        public = self.fs.text("/run/psx-ssh/ssh_host_ed25519_key.pub")
        return {"enabled": result.returncode == 0, "port": 22,
                "host_key_fingerprint": public_fingerprint(public) if public else None, "login": "root"}

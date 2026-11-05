"""Data-only image/USB safeguards and actual MTS helper decisions; no devices."""
import gzip
import base64
import dis
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(name, loader)
    app = importlib.util.module_from_spec(spec); sys.modules[name] = app
    loader.exec_module(app)
    return app


IMAGE = load("diagnostics_image", "scripts/build-ps4-diagnostics")
STAGE = load("diagnostics_stage", "scripts/stage-ps4-diagnostics-usb")
NET = load("diagnostics_net_helper", "rootfs/ps4-diagnostics/ethernet-up")
DHCP = load("diagnostics_dhcp_hook", "rootfs/ps4-diagnostics/udhcpc-hook")
RECEIPT = load("diagnostics_receipt", "rootfs/ps4-diagnostics/verify-boot-set")
GRAPHICS = load("diagnostics_graphical_launcher", "rootfs/ps4-diagnostics-desktop/launch-graphics")


class DiagnosticImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime_libraries = {}
        providers = {}
        for name, provider in IMAGE.RUNTIME_LIBRARY_PROVIDERS.items():
            data = bytearray(64); data[:7] = b"\x7fELF\x02\x01\x01"
            struct.pack_into("<HH", data, 16, 3, 62)
            data = bytes(data) + name.encode()
            self.runtime_libraries[name] = data
            providers[name] = dict(provider, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        patch = mock.patch.object(IMAGE, "RUNTIME_LIBRARY_PROVIDERS", providers)
        patch.start(); self.addCleanup(patch.stop)
        self.runtime_assets = dict(self.runtime_libraries)
        files = {}
        for name, provider in IMAGE.RUNTIME_FILE_PROVIDERS.items():
            if provider["kind"] == "program":
                data = bytearray(64); data[:7] = b"\x7fELF\x02\x01\x01"
                struct.pack_into("<HH", data, 16, 3, 62)
                data = bytes(data) + name.encode()
            elif provider["kind"] == "font":
                data = b"\x00\x01\x00\x00" + b"synthetic TrueType fixture"
            else:
                data = b"Synthetic OFL license fixture"
            self.runtime_assets[name] = data
            files[name] = dict(provider, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        patch = mock.patch.object(IMAGE, "RUNTIME_FILE_PROVIDERS", files)
        patch.start(); self.addCleanup(patch.stop)

    def test_checksum_normalization_keeps_snapshot_paths_and_rejects_alias_duplicates(self):
        pin = "a" * 64
        p = self.root / "SHA256SUMS"
        p.write_text(pin + "  ./repo/package.pkg.tar.zst\n")
        self.assertEqual(IMAGE.checksums(self.root), {"repo/package.pkg.tar.zst": pin})
        p.write_text(p.read_text() + pin + "  repo/package.pkg.tar.zst\n")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            IMAGE.checksums(self.root)

    def test_checksum_path_traversal_rejected(self):
        (self.root / "SHA256SUMS").write_text("a" * 64 + "  ../elsewhere\n")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            IMAGE.checksums(self.root)

    def test_emitted_initramfs_has_early_console_cp_and_bound_root_hash(self):
        profile = {"kernel_release": "test-release", "ram_root_mib": 1536,
                   "source_date_epoch": 1234}
        path = self.root / "initramfs.gz"
        IMAGE.initramfs(profile, {}, b"synthetic-busybox-fixture", {}, "b" * 64, path, 5678)
        parser = load("diagnostics_cpio_test", "scripts/ps4-diagnostics-archive")
        entries, _ = parser.parse_newc(gzip.decompress(path.read_bytes()))
        found = {entry.name.decode(): entry for entry in entries}
        self.assertEqual(found["bin/cp"].data, b"/bin/busybox")
        self.assertEqual(found["bin/chroot"].data, b"/bin/busybox")
        self.assertTrue(stat.S_ISLNK(found["bin/cp"].mode))
        for name, major, minor in (("dev/console", 5, 1), ("dev/null", 1, 3)):
            self.assertTrue(stat.S_ISCHR(found[name].mode))
            self.assertEqual(int(found[name].header[78:86], 16), major)
            self.assertEqual(int(found[name].header[86:94], 16), minor)
        self.assertIn(("ROOTFS_SHA256='" + "b" * 64 + "'").encode(),
                      found["etc/psx-diagnostics.env"].data)
        self.assertEqual(json.loads(found["etc/psx-diagnostics-expected.json"].data)["rootfs_sha256"], "b" * 64)
        self.assertEqual(found["init"].data, (ROOT / "rootfs/ps4-diagnostics/init").read_bytes())

    def test_console_cpio_binds_readonly_format_and_native_autoload_policy(self):
        profile = {"kernel_release": "test-release", "ram_root_mib": 0,
                   "source_date_epoch": 1234, "root_format": "ext4-ro-loop",
                   "mts_autoarm": 1, "writable_root_mib": 512}
        path = self.root / "initramfs.gz"
        IMAGE.initramfs(profile, {}, b"fixture", {}, "b" * 64, path, 5678)
        parser = load("diagnostics_loop_cpio_test", "scripts/ps4-diagnostics-archive")
        entries, _ = parser.parse_newc(gzip.decompress(path.read_bytes()))
        found = {entry.name.decode(): entry for entry in entries}
        env = found["etc/psx-diagnostics.env"].data.decode()
        self.assertIn("ROOT_FORMAT='ext4-ro-loop'", env)
        self.assertIn("ROOTFS_FILENAME='rootfs.ext4'", env)
        self.assertIn("MTS_AUTOARM='1'", env)
        self.assertIn("WRITABLE_ROOT_MIB='512'", env)
        self.assertEqual(found["bin/losetup"].data, b"/bin/busybox")

    def test_scoped_sata_profile_embeds_guard_before_userspace(self):
        profile = {"kernel_release": "test-release", "ram_root_mib": 0,
                   "source_date_epoch": 1234, "sata_policy": "read-only-scoped-probe"}
        path = self.root / "initramfs.gz"
        IMAGE.initramfs(profile, {}, b"fixture", {}, "b" * 64, path, 5678)
        parser = load("diagnostics_sata_cpio_test", "scripts/ps4-diagnostics-archive")
        entries, _ = parser.parse_newc(gzip.decompress(path.read_bytes()))
        found = {entry.name.decode(): entry for entry in entries}
        self.assertIn(b"SATA_POLICY='read-only-scoped-probe'", found["etc/psx-diagnostics.env"].data)
        self.assertEqual(found["etc/psx-sata-readonly"].data,
                         (ROOT / "rootfs/ps4-diagnostics/sata-readonly").read_bytes())
        self.assertEqual(found["bin/blockdev"].data, b"/bin/busybox")
        init = found["init"].data
        self.assertLess(init.index(b"psx_sata_readonly /sys /dev"), init.index(b"exec switch_root"))

    def test_unknown_sata_policy_refuses_image_assembly(self):
        profile = {"kernel_release": "test-release", "ram_root_mib": 0,
                   "source_date_epoch": 1234, "sata_policy": "allow-writes"}
        with self.assertRaisesRegex(ValueError, "SATA"):
            IMAGE.initramfs(profile, {}, b"fixture", {}, "b" * 64,
                            self.root / "initramfs.gz", 5678)

    def frontend(self):
        executable = bytearray(64)
        executable[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", executable, 16, 3, 62)
        return {"psx-diagnostics-tui": bytes(executable),
                "start-console": b"#!/bin/sh\necho console\n",
                "prepare-console": b"#!/usr/bin/python3\nprint('prepare')\n",
                "style.css": b"body { color: olive; }\n",
                "network.py": b"#!/usr/bin/env python3\nVALUE = 'network-fixture'\n",
                "controller.py": b"#!/usr/bin/env python3\nVALUE = 'controller-fixture'\n",
                "launch-graphics": b"#!/usr/bin/python3\npass\n",
                "graphics-start": b"#!/bin/bash\ntrue\n",
                "graphics-client": b"#!/bin/sh\ntrue\n",
                "graphics-session": b"#!/bin/bash\ntrue\n",
                "graphics-window": b"#!/bin/bash\ntrue\n",
                "wait-graphics-wm": b"#!/usr/bin/python3\npass\n",
                "xorg-diagnostics.conf": b'Section "Device"\nEndSection\n',
                "preferences.py": b"#!/usr/bin/env python3\npass\n",
                "themes.json": b'{"schema_version":1,"themes":[]}\n',
                "themes-LICENSE": b"Synthetic palette license\n",
                "index.html": b"<!doctype html><title>Lab fixture</title>\n",
                "lab.js": b"const fixture = true;\n",
                "prepare-chromium-provider": b"#!/usr/bin/python3\npass\n",
                "launch-chromium-provider": b"#!/usr/bin/python3\npass\n",
                "ssh_access.py": b"#!/usr/bin/env python3\npass\n",
                **self.runtime_assets}

    def test_frontend_manifest_rejects_unknown_missing_and_oversized_payloads(self):
        frontend = self.frontend()
        facts = IMAGE.frontend_manifest(frontend)
        self.assertEqual(facts["total_bytes"], sum(map(len, frontend.values())))
        self.assertEqual(facts["ram_limit_bytes"], 16*1024**2)
        self.assertEqual({item["name"] for item in facts["files"]}, set(frontend))
        self.assertIsNone(next(item for item in facts["files"]
                               if item["name"] == "prepare-console")["bind_target"])
        network = next(item for item in facts["files"] if item["name"] == "network.py")
        self.assertEqual(network["bind_target"], "/opt/psx-diagnostics/network.py")
        self.assertEqual(network["mode"], "0755")
        self.assertEqual(network["sha256"], hashlib.sha256(frontend["network.py"]).hexdigest())
        self.assertEqual(facts["runtime_libraries"], IMAGE.runtime_library_receipt())
        for name in self.runtime_libraries:
            fact = next(item for item in facts["files"] if item["name"] == name)
            self.assertEqual(fact["mode"], "0644")
            self.assertIsNone(fact["bind_target"])
        for bad in (dict(frontend, **{"../../etc/passwd": b"bad"}),
                    {name: data for name, data in frontend.items() if name != "style.css"},
                    {name: data for name, data in frontend.items() if name != "network.py"},
                    {name: data for name, data in frontend.items() if name != "libdisplay-info.so.3"},
                    dict(frontend, **{"style.css": b"x" * (8*1024**2)})):
            with self.assertRaises(ValueError):
                IMAGE.frontend_manifest(bad)

    def test_frontend_cpio_contains_pinned_payload_with_unchanged_root_contract(self):
        profile = {"kernel_release": "test", "ram_root_mib": 0, "source_date_epoch": 1234,
                   "ui_backend": "ratatui", "root_format": "ext4-ro-loop"}
        frontend = self.frontend()
        expected = dict(profile, rootfs_sha256="b"*64,
                        frontend_overrides=IMAGE.frontend_manifest(frontend))
        output = self.root / "frontend.gz"
        IMAGE.initramfs(profile, {}, b"fixture", {}, "b"*64, output, 5678,
                        expected=expected, frontend=frontend)
        parser = load("diagnostics_frontend_cpio_test", "scripts/ps4-diagnostics-archive")
        entries, _ = parser.parse_newc(gzip.decompress(output.read_bytes()))
        found = {entry.name.decode(): entry for entry in entries}
        self.assertEqual(json.loads(found["etc/psx-diagnostics-expected.json"].data), expected)
        self.assertIn(b"FRONTEND_OVERRIDES='1'", found["etc/psx-diagnostics.env"].data)
        self.assertIn(b"ROOTFS_SHA256='" + b"b"*64 + b"'", found["etc/psx-diagnostics.env"].data)
        self.assertEqual(found["bin/wc"].data, b"/bin/busybox")
        for item in expected["frontend_overrides"]["files"]:
            entry = found["psx-frontend/" + item["name"]]
            self.assertEqual(entry.data, frontend[item["name"]])
            self.assertEqual(stat.S_IMODE(entry.mode), int(item["mode"], 8))
        with self.assertRaisesRegex(ValueError, "hashes"):
            IMAGE.initramfs(profile, {}, b"fixture", {}, "b"*64, output, 5678,
                            expected=dict(expected, frontend_overrides={}), frontend=frontend)
        with self.assertRaisesRegex(ValueError, "no override payload"):
            IMAGE.initramfs(profile, {}, b"fixture", {}, "b"*64, output, 5678, expected=expected)

    def run_frontend_install(self, fault=None):
        frontend = self.frontend()
        facts = IMAGE.frontend_manifest(frontend)
        root, payload = self.root / "os", self.root / "payload"
        runtime, binary = self.root / "runtime", self.root / "bin"
        payload.mkdir(); binary.mkdir()
        manifest, log = self.root / "manifest.tsv", self.root / "mount.log"
        rows = []
        for item in facts["files"]:
            (payload / item["name"]).write_bytes(frontend[item["name"]])
            rows.append("\t".join(str(item[key]) for key in ("name", "sha256", "bytes", "mode")))
            if item["bind_target"] is not None:
                target = root / item["bind_target"].lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"original verified-root file")
        manifest.write_text("\n".join(rows) + "\n")
        if fault == "hash":
            (payload / "psx-diagnostics-tui").write_bytes(b"x"*len(frontend["psx-diagnostics-tui"]))
        elif fault == "missing":
            (payload / "prepare-console").unlink()
        elif fault == "missing-network":
            (payload / "network.py").unlink()
        elif fault == "network-hash":
            (payload / "network.py").write_bytes(b"x"*len(frontend["network.py"]))
        elif fault == "missing-library":
            (payload / "libdisplay-info.so.3").unlink()
        elif fault == "library-hash":
            (payload / "libxcb-keysyms.so.1").write_bytes(b"x"*len(frontend["libxcb-keysyms.so.1"]))
        elif fault == "missing-font":
            (payload / "JetBrainsMonoNerdFont-Regular.ttf").unlink()
        elif fault == "missing-waiter":
            (payload / "wait-graphics-wm").unlink()
        elif fault == "missing-ssh-access":
            (payload / "ssh_access.py").unlink()
        elif fault == "missing-xorg-target":
            (root / "etc/X11/xorg.conf.d/20-psx-diagnostics.conf").unlink()
        elif fault == "old-four-file-manifest":
            manifest.write_text("\n".join(row for row in rows if not row.startswith("network.py\t")) + "\n")
        elif fault == "target-symlink":
            target = root / "usr/bin/psx-diagnostics-tui"
            outside = self.root / "outside"; outside.write_bytes(b"untouched")
            target.unlink(); target.symlink_to(outside)
        elif fault == "parent-symlink":
            directory = root / "usr/bin"
            outside = self.root / "outside-dir"
            directory.rename(outside); directory.symlink_to(outside, target_is_directory=True)
        elif fault == "unknown":
            manifest.write_text(manifest.read_text().replace("psx-diagnostics-tui\t", "../../escape\t", 1))
        mount = binary / "mount"
        mount.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$PSX_MOUNT_LOG"\n')
        mount.chmod(0o755)
        source = (ROOT / "rootfs/ps4-diagnostics/init").read_text()
        body = source.split("install_frontend() {\n", 1)[1].split("\n}\n", 1)[0]
        replacements = {"/newroot": str(root), "/run/psx-frontend": str(runtime),
                        "/psx-frontend": str(payload), "/etc/psx-frontend.tsv": str(manifest)}
        body = re.sub("|".join(map(re.escape, replacements)),
                      lambda match: replacements[match.group()], body)
        script = ('rescue() { printf "%s\\n" "$*" >&2; exit 41; }\n'
                  'install_frontend() {\n' + body + '\n}\ninstall_frontend\n')
        result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True,
            env=dict(os.environ, PATH=str(binary)+os.pathsep+os.environ["PATH"], PSX_MOUNT_LOG=str(log)))
        return result, log.read_text() if log.exists() else "", runtime

    def test_init_frontend_uses_twelve_readonly_file_binds_and_ram_only_helpers(self):
        result, mounts, runtime = self.run_frontend_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mode=0755,nosuid,nodev,size=16m", mounts)
        self.assertEqual(mounts.count("--bind "), 12)
        self.assertEqual(mounts.count("remount,bind,ro"), 12)
        self.assertNotIn("--bind " + str(runtime / "prepare-console"), mounts)
        self.assertNotIn("--bind " + str(runtime / "graphics-window"), mounts)
        self.assertNotIn("--bind " + str(runtime / "wait-graphics-wm"), mounts)
        for name in ("preferences.py", "themes.json", "themes-LICENSE"):
            self.assertNotIn("--bind " + str(runtime / name), mounts)
        for name in ("prepare-chromium-provider", "launch-chromium-provider", "ssh_access.py"):
            self.assertNotIn("--bind " + str(runtime / name), mounts)
            self.assertEqual(stat.S_IMODE((runtime / name).stat().st_mode), 0o755)
        self.assertEqual((runtime / "prepare-console").read_bytes(), self.frontend()["prepare-console"])
        self.assertEqual(stat.S_IMODE((runtime / "style.css").stat().st_mode), 0o644)
        self.assertEqual((runtime / "network.py").read_bytes(), self.frontend()["network.py"])
        target = self.root / "os/opt/psx-diagnostics/network.py"
        self.assertIn(f"--bind {runtime / 'network.py'} {target}", mounts)
        self.assertIn(f"remount,bind,ro {runtime / 'network.py'} {target}", mounts)
        target = self.root / "os/opt/psx-diagnostics/controller.py"
        self.assertIn(f"--bind {runtime / 'controller.py'} {target}", mounts)
        self.assertIn(f"remount,bind,ro {runtime / 'controller.py'} {target}", mounts)
        for name, data in self.runtime_libraries.items():
            self.assertEqual((runtime / name).read_bytes(), data)
            self.assertEqual(stat.S_IMODE((runtime / name).stat().st_mode), 0o644)
            self.assertNotIn(f"--bind {runtime / name} ", mounts)
        for name in IMAGE.RUNTIME_FILE_PROVIDERS:
            self.assertEqual((runtime / name).read_bytes(), self.runtime_assets[name])
            self.assertNotIn(f"--bind {runtime / name} ", mounts)
        for name in ("launch-graphics", "graphics-start", "graphics-client", "graphics-session"):
            target = self.root / "os/usr/lib/psx-diagnostics" / name
            self.assertIn(f"--bind {runtime / name} {target}", mounts)
        target = self.root / "os/etc/X11/xorg.conf.d/20-psx-diagnostics.conf"
        self.assertIn(f"--bind {runtime / 'xorg-diagnostics.conf'} {target}", mounts)
        self.assertIn(f"remount,bind,ro {runtime / 'xorg-diagnostics.conf'} {target}", mounts)
        for name in ("index.html", "lab.js"):
            target = self.root / "os/opt/psx-diagnostics/lab" / name
            self.assertIn(f"--bind {runtime / name} {target}", mounts)
            self.assertIn(f"remount,bind,ro {runtime / name} {target}", mounts)

    def test_init_frontend_requires_the_window_manager_waiter(self):
        result, _, _ = self.run_frontend_install("missing-waiter")
        self.assertEqual(result.returncode, 41)
        self.assertIn("missing or not a regular file", result.stderr)

    def test_init_frontend_requires_the_new_ram_ssh_access_helper(self):
        result, _, _ = self.run_frontend_install("missing-ssh-access")
        self.assertEqual(result.returncode, 41)
        self.assertIn("missing or not a regular file", result.stderr)

    def test_init_frontend_refuses_an_absent_xorg_configuration_target(self):
        result, mounts, runtime = self.run_frontend_install("missing-xorg-target")
        self.assertEqual(result.returncode, 41)
        self.assertIn("bind target is missing", result.stderr)
        self.assertNotIn(f"--bind {runtime / 'xorg-diagnostics.conf'} ", mounts)

    def test_init_frontend_requires_both_runtime_libraries(self):
        result, _, _ = self.run_frontend_install("missing-library")
        self.assertEqual(result.returncode, 41)
        self.assertIn("missing or not a regular file", result.stderr)

    def test_init_frontend_verifies_ram_library_hashes(self):
        result, _, _ = self.run_frontend_install("library-hash")
        self.assertEqual(result.returncode, 41)
        self.assertIn("checksum differs", result.stderr)

    def test_init_frontend_requires_the_real_font_asset(self):
        result, _, _ = self.run_frontend_install("missing-font")
        self.assertEqual(result.returncode, 41)
        self.assertIn("missing or not a regular file", result.stderr)

    def test_nerd_font_and_terminal_bytes_must_match_the_exact_provider_pins(self):
        for name in IMAGE.RUNTIME_FILE_PROVIDERS:
            data = self.runtime_assets[name]
            with self.subTest(name=name), self.assertRaises(ValueError):
                IMAGE.runtime_provider_bytes(name, data[:-1] + bytes([data[-1] ^ 1]))

    def test_console_pruning_keeps_regular_nerd_font_and_license_only(self):
        profile = json.loads((ROOT / "manifests/ps4-diagnostics-console.json").read_text())
        regular = self.root / "usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf"
        regular.parent.mkdir(parents=True)
        regular.write_bytes(b"retained regular Nerd Font")
        bold = regular.with_name("JetBrainsMonoNerdFont-Bold.ttf"); bold.write_bytes(b"unused variant")
        license = self.root / "usr/share/licenses/ttf-jetbrains-mono-nerd-basic/OFL.txt"
        license.parent.mkdir(parents=True); license.write_bytes(b"retained font license")
        IMAGE.prune_root(self.root, profile)
        self.assertTrue(regular.exists())
        self.assertFalse(bold.exists())
        self.assertTrue(license.exists())

    def test_full_root_keeps_the_original_diagnostic_mit_notice_through_pruning(self):
        IMAGE.install_diagnostic_license(self.root)
        license = self.root / "usr/share/licenses/psx-diagnostics/LICENSE"
        expected = (ROOT / "diagnostics/LICENSE").read_bytes()
        self.assertTrue(expected.startswith(b"MIT License\n"))
        self.assertNotEqual(expected, (ROOT / "LICENSE").read_bytes())
        profile = json.loads((ROOT / "manifests/ps4-diagnostics-console.json").read_text())
        IMAGE.prune_root(self.root, profile)
        self.assertEqual(license.read_bytes(), expected)
        self.assertEqual(stat.S_IMODE(license.stat().st_mode), 0o644)

    def test_runtime_library_directory_requires_exact_pinned_providers_and_receipt(self):
        directory = self.root / "libraries"; directory.mkdir()
        profile = {"snapshot_sums_sha256": IMAGE.RUNTIME_SNAPSHOT_SHA256}
        receipt = directory / "provider-receipt.json"
        receipt.write_text(json.dumps(IMAGE.runtime_library_receipt(profile)))
        for name, data in self.runtime_assets.items():
            (directory / name).write_bytes(data)
        self.assertEqual(IMAGE.runtime_libraries_from_dir(directory, profile), self.runtime_assets)
        name = "libxcb-keysyms.so.1"
        (directory / name).write_bytes(b"x"*len(self.runtime_libraries[name]))
        with self.assertRaises(ValueError):
            IMAGE.runtime_libraries_from_dir(directory, profile)
        (directory / name).write_bytes(self.runtime_libraries[name])
        changed = IMAGE.runtime_library_receipt(profile)
        changed["files"][0]["archive_sha256"] = "a"*64
        receipt.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "receipt"):
            IMAGE.runtime_libraries_from_dir(directory, profile)
        receipt.write_text(json.dumps(IMAGE.runtime_library_receipt(profile)))
        (directory / name).unlink(); (directory / name).symlink_to(directory / "libdisplay-info.so.3")
        with self.assertRaisesRegex(ValueError, "regular"):
            IMAGE.runtime_libraries_from_dir(directory, profile)

    def test_runtime_library_snapshot_validates_manifest_and_archive_before_extraction(self):
        snapshot = self.root / "snapshot"; (snapshot / "repo").mkdir(parents=True)
        providers = {}
        for name, provider in IMAGE.runtime_providers().items():
            archive = snapshot / "repo" / provider["archive"]
            archive.write_bytes(("synthetic archive " + provider["archive"]).encode())
            providers[name] = dict(provider, archive_sha256=IMAGE.digest(archive))
        archives = {item['archive']: item['archive_sha256'] for item in providers.values()}
        sums = "".join(f"{checksum}  repo/{name}\n" for name, checksum in archives.items())
        (snapshot / "SHA256SUMS").write_text(sums)
        checksum = IMAGE.digest(snapshot / "SHA256SUMS")
        results = [type("Result", (), {"stdout": self.runtime_assets[name]})() for name in providers]
        # A publicly reconstructed database has a new explicit receipt pin;
        # the archive/member/file identities must remain independently pinned.
        self.assertNotEqual(checksum, IMAGE.RUNTIME_SNAPSHOT_SHA256)
        with mock.patch.object(IMAGE, "runtime_providers", return_value=providers), \
             mock.patch.object(IMAGE.subprocess, "run", side_effect=results) as run:
            self.assertEqual(IMAGE.runtime_libraries_from_snapshot(snapshot, {"snapshot_sums_sha256": checksum}),
                             self.runtime_assets)
            self.assertEqual(run.call_count, len(self.runtime_assets))
            for call, provider in zip(run.call_args_list, providers.values()):
                self.assertEqual(call.args[0][-1], provider["member"])
            first = next(iter(archives))
            changed_sums = sums.replace(archives[first], "a"*64)
            (snapshot / "SHA256SUMS").write_text(changed_sums)
            with self.assertRaisesRegex(ValueError, "provider archive"):
                IMAGE.runtime_libraries_from_snapshot(snapshot, {
                    "snapshot_sums_sha256": IMAGE.digest(snapshot / "SHA256SUMS")})
            self.assertEqual(run.call_count, len(self.runtime_assets))
            (snapshot / "SHA256SUMS").write_text("changed")
            with self.assertRaises(ValueError):
                IMAGE.runtime_libraries_from_snapshot(snapshot, {"snapshot_sums_sha256": checksum})
            self.assertEqual(run.call_count, len(self.runtime_assets))

    def test_init_frontend_requires_the_network_override(self):
        result, _, _ = self.run_frontend_install("missing-network")
        self.assertEqual(result.returncode, 41)
        self.assertIn("missing or not a regular file", result.stderr)

    def test_init_frontend_rejects_old_manifest_without_the_network_override(self):
        result, _, _ = self.run_frontend_install("old-four-file-manifest")
        self.assertEqual(result.returncode, 41)
        self.assertIn("every required override", result.stderr)

    def test_init_frontend_checks_network_bytes_before_binding_them(self):
        result, mounts, runtime = self.run_frontend_install("network-hash")
        self.assertEqual(result.returncode, 41)
        self.assertIn("checksum differs", result.stderr)
        self.assertNotIn(f"--bind {runtime / 'network.py'} ", mounts)

    def test_init_frontend_stops_on_payload_checksum_mismatch(self):
        result, mounts, _ = self.run_frontend_install("hash")
        self.assertEqual(result.returncode, 41)
        self.assertIn("checksum differs", result.stderr)
        self.assertNotIn("--bind", mounts)

    def test_init_frontend_stops_on_missing_override(self):
        result, _, _ = self.run_frontend_install("missing")
        self.assertEqual(result.returncode, 41)
        self.assertIn("missing or not a regular file", result.stderr)

    def test_init_frontend_rejects_symlink_bind_target(self):
        result, mounts, _ = self.run_frontend_install("target-symlink")
        self.assertEqual(result.returncode, 41)
        self.assertIn("not a regular file", result.stderr)
        self.assertNotIn("--bind", mounts)

    def test_init_frontend_rejects_parent_symlink_escape(self):
        result, mounts, _ = self.run_frontend_install("parent-symlink")
        self.assertEqual(result.returncode, 41)
        self.assertIn("fixed root path", result.stderr)
        self.assertNotIn("--bind", mounts)

    def test_init_frontend_rejects_manifest_path_outside_allowlist(self):
        result, mounts, _ = self.run_frontend_install("unknown")
        self.assertEqual(result.returncode, 41)
        self.assertIn("allowlist", result.stderr)
        self.assertNotIn("--bind", mounts)

    def test_ext4_size_leaves_metadata_headroom_and_obeys_fat32_limit(self):
        size = IMAGE.ext4_image_bytes(1024**3)
        self.assertGreater(size, 1024**3 + 128*1024**2)
        self.assertEqual(size % (64*1024**2), 0)
        with self.assertRaisesRegex(ValueError, "FAT32"):
            IMAGE.ext4_image_bytes(4*1024**3)

    def test_packer_refuses_symlink_or_special_output_before_any_tool(self):
        source = self.root / "unrelated"; source.write_bytes(b"keep")
        target = self.root / "image"; target.symlink_to(source)
        with mock.patch.object(IMAGE, "run") as run:
            with self.assertRaisesRegex(ValueError, "regular file"):
                IMAGE.pack_root(self.root, {"root_format": "ext4-ro-loop"}, target, 1024)
            run.assert_not_called()
        self.assertEqual(source.read_bytes(), b"keep")

    def test_ext4_packer_only_formats_the_selected_regular_output_file(self):
        root = self.root / "root"; root.mkdir()
        target = self.root / "rootfs.ext4"
        result = type("Command", (), {"stdout": "Block count: 32768\nBlock size: 4096\n"})()
        with mock.patch.object(IMAGE, "run", return_value=result) as run:
            size = IMAGE.pack_root(root, {"root_format": "ext4-ro-loop"}, target, 1024)
        self.assertEqual(target.stat().st_size, size)
        self.assertEqual(size, 128*1024**2)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][-2:], [root, target])
        self.assertEqual(commands[0][0], "mkfs.ext4")
        self.assertIn("^has_journal", commands[0])
        self.assertEqual(commands[1], ["resize2fs", "-M", target])
        self.assertEqual(commands[-1], ["e2fsck", "-fn", target])

    def test_ext4_packer_obeys_the_one_kib_block_profile(self):
        root = self.root / "root"; root.mkdir()
        target = self.root / "rootfs.ext4"
        result = type("Command", (), {"stdout": "Block count: 65536\nBlock size: 1024\n"})()
        with mock.patch.object(IMAGE, "run", return_value=result) as run:
            size = IMAGE.pack_root(root, {"root_format": "ext4-ro-loop", "ext4_block_size": 1024}, target, 1024)
        self.assertEqual(size, 64*1024**2)
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[command.index("-b")+1], "1024")
        self.assertEqual(target.stat().st_size, size)

    def test_fixed_ext4_budget_never_runs_the_growing_minimum_estimator(self):
        root = self.root / "root"; root.mkdir()
        target = self.root / "rootfs.ext4"
        profile = {"root_format": "ext4-ro-loop", "ext4_block_size": 1024,
                   "ext4_maximum_bytes": 1000*1024**2}
        with mock.patch.object(IMAGE, "run") as run:
            size = IMAGE.pack_root(root, profile, target, 950*1024**2)
        self.assertEqual(size, 1048576000)
        self.assertEqual(target.stat().st_size, size)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([command[0] for command in commands], ["mkfs.ext4", "e2fsck"])
        self.assertEqual(commands[-1], ["e2fsck", "-fn", target])

    def test_mesa_wrapper_options_cannot_reassign_the_kernel_runtime_input(self):
        # The full-build failure rebound this Path to an option string after
        # validation, so copying bzImage failed only after expensive packing.
        # Keep the validated input binding immutable throughout main's flow.
        bindings = [instruction for instruction in dis.get_instructions(IMAGE.main)
                    if instruction.opname in ("STORE_FAST", "STORE_DEREF")
                    and instruction.argval == "runtime"]
        self.assertEqual(len(bindings), 1)

    def test_pruning_is_scoped_recorded_and_preserves_runtime_data(self):
        doc = self.root / "usr/share/doc/example"; doc.mkdir(parents=True)
        (doc / "manual").write_bytes(b"documentation")
        license_dir = self.root / "usr/share/licenses/example"; license_dir.mkdir(parents=True)
        license = license_dir / "LICENSE"; license.write_bytes(b"retained license")
        result = IMAGE.prune_root(self.root, {"prune_paths": ["usr/share/doc"]})
        self.assertEqual(result["removed_bytes"], len(b"documentation"))
        self.assertFalse(doc.exists()); self.assertEqual(license.read_bytes(), b"retained license")
        for path in ("../outside", "usr/share/licenses", "usr/share/terminfo"):
            with self.assertRaises(ValueError):
                IMAGE.prune_root(self.root, {"prune_paths": [path]})

    def test_cursor_pruning_keeps_requested_data_and_its_aliases(self):
        directory = self.root / "usr/share/icons/Adwaita/cursors"
        directory.mkdir(parents=True)
        (directory / "default").write_bytes(b"retained cursor")
        (directory / "left_ptr").symlink_to("default")
        (directory / "cursor-hash-alias").symlink_to("left_ptr")
        (directory / "unneeded").write_bytes(b"unused cursor")
        profile = {"prune_directory_except": {
            "usr/share/icons/Adwaita/cursors": ["left_ptr"]}}
        result = IMAGE.prune_root(self.root, profile)
        self.assertEqual((directory / "left_ptr").read_bytes(), b"retained cursor")
        self.assertEqual((directory / "cursor-hash-alias").read_bytes(), b"retained cursor")
        self.assertFalse((directory / "unneeded").exists())
        self.assertEqual(result["removed_bytes"], len(b"unused cursor"))

    def test_payload_budget_includes_nested_source_files_but_not_builder_metadata(self):
        (self.root / "bzImage").write_bytes(b"kernel")
        (self.root / "SHA256SUMS").write_bytes(b"generated checksums")
        (self.root / ".psx-diagnostics-build.json").write_bytes(b"local marker")
        payload = self.root / "psx-diagnostics"; payload.mkdir()
        (payload / "rootfs.ext4").write_bytes(b"root")
        self.assertEqual(IMAGE.boot_set_bytes(self.root), len(b"kernelroot"))

    def public_key(self, declared="ssh-ed25519", encoded="ssh-ed25519"):
        kind = encoded.encode()
        blob = struct.pack(">I", len(kind)) + kind + struct.pack(">I", 32) + b"x" * 32
        return declared + " " + base64.b64encode(blob).decode() + " fixture\n"

    def test_explicit_public_key_keeps_one_key_and_records_its_fingerprint(self):
        path = self.root / "owner.pub"; path.write_text(self.public_key())
        data, fingerprint = IMAGE.authorized_key(path)
        self.assertEqual(data, path.read_bytes())
        self.assertTrue(fingerprint.startswith("SHA256:"))
        for value in ("-----BEGIN OPENSSH PRIVATE KEY-----\n", self.public_key()*2,
                      self.public_key(encoded="ssh-rsa"), "ssh-ed25519 !broken!\n"):
            path.write_text(value)
            with self.assertRaises(ValueError):
                IMAGE.authorized_key(path)

    def test_public_console_has_no_seeded_credential_and_keeps_ssh_dormant(self):
        data, facts = IMAGE.console_ssh_seed(None)
        self.assertEqual(data, b"")
        self.assertIsNone(facts["ssh_authorized_key_sha256"])
        self.assertIsNone(facts["ssh_authorized_key_fingerprint"])
        self.assertTrue(facts["ssh_setup_required"])
        IMAGE.install_console(self.root, data, self.frontend()["psx-diagnostics-tui"])
        owner = self.root / "etc/psx-diagnostics/authorized_keys"
        self.assertEqual(owner.read_bytes(), b"")
        self.assertEqual(stat.S_IMODE(owner.stat().st_mode), 0o644)
        wants = self.root / "etc/systemd/system/multi-user.target.wants"
        self.assertTrue((wants / "psx-ssh-host-key.service").is_symlink())
        self.assertTrue((wants / "psx-diagnostics-console.service").is_symlink())
        self.assertFalse((wants / "sshd.service").is_symlink())
        self.assertFalse((self.root / "etc/systemd/system/sshd.service").is_symlink())
        policy = (self.root / "etc/ssh/sshd_config.d/00-psx-diagnostics.conf").read_text()
        self.assertIn("AuthenticationMethods publickey", policy)
        self.assertIn("PasswordAuthentication no", policy)
        self.assertIn("PermitRootLogin prohibit-password", policy)
        self.assertIn("AuthorizedKeysFile /etc/psx-diagnostics/authorized_keys /root/.ssh/authorized_keys", policy)
        shadow = self.root / "etc/shadow"
        shadow.write_text("root:old-fixture-hash:1::::::\npsx:!:1::::::\n")
        IMAGE.lock_root_password(self.root)
        self.assertEqual(shadow.read_text(), "root:!:1::::::\npsx:!:1::::::\n")
        self.assertEqual(stat.S_IMODE(shadow.stat().st_mode), 0o600)

    def test_owner_console_preserves_its_key_and_automatic_key_only_ssh(self):
        source = self.root / "owner.pub"; source.write_text(self.public_key())
        data, facts = IMAGE.console_ssh_seed(source)
        self.assertEqual(data, source.read_bytes())
        self.assertFalse(facts["ssh_setup_required"])
        self.assertEqual(facts["ssh_authorized_key_sha256"], hashlib.sha256(data).hexdigest())
        self.assertTrue(facts["ssh_authorized_key_fingerprint"].startswith("SHA256:"))
        IMAGE.install_console(self.root, data, self.frontend()["psx-diagnostics-tui"])
        self.assertEqual((self.root / "etc/psx-diagnostics/authorized_keys").read_bytes(), data)
        wants = self.root / "etc/systemd/system/multi-user.target.wants"
        self.assertTrue((wants / "sshd.service").is_symlink())
        # A resumed public assembly cannot retain the previous owner activation.
        IMAGE.install_console(self.root, b"", self.frontend()["psx-diagnostics-tui"])
        self.assertFalse((wants / "sshd.service").is_symlink())

    def test_console_plan_accepts_no_owner_key_without_assembling_or_using_private_inputs(self):
        profile = json.loads((ROOT / "manifests/ps4-diagnostics-console.json").read_text())
        profile_file = self.root / "profile.json"; profile_file.write_text(json.dumps(profile))
        source = self.root / "source"; source.mkdir()
        (source / "artifacts.json").write_text("{}")
        seed = self.root / "seed"; seed.write_bytes(b"synthetic seed bytes")
        tui = self.root / "tui"; tui.write_bytes(self.frontend()["psx-diagnostics-tui"])
        video = self.root / "video.webm"; video.write_bytes(b"\x1aE\xdf\xa3fixture")
        args = ["build-ps4-diagnostics", "--plan", "--profile", str(profile_file),
                "--kernel-runtime", str(self.root / "runtime"), "--kernel-source", str(source),
                "--snapshot", str(self.root / "snapshot"), "--seed", str(seed),
                "--mesa-archive", str(self.root / "mesa"), "--work", str(self.root / "never-work"),
                "--output", str(self.root / "never-output"), "--firmware-dir", str(self.root / "firmware"),
                "--tui-binary", str(tui), "--browser-fixture", str(video)]
        output = io.StringIO()
        with mock.patch.object(sys, "argv", args), mock.patch.object(sys, "stdout", output), \
             mock.patch.object(IMAGE, "validate_kernel", return_value=({}, "modules.tar.gz")), \
             mock.patch.object(IMAGE, "checksums", return_value={f"source-{profile['kernel_release']}.tar.zst": "a"*64}), \
             mock.patch.object(IMAGE, "verify", side_effect=lambda path, _pin: path), \
             mock.patch.object(IMAGE, "select_packages", return_value=[]), \
             mock.patch.object(IMAGE, "seed_files", return_value=({}, b"fixture", {}, None)), \
             mock.patch.object(IMAGE, "extra_firmware", return_value=({}, [])), \
             mock.patch.object(IMAGE, "authorized_key") as owner_key:
            IMAGE.main()
        plan = json.loads(output.getvalue())
        self.assertTrue(plan["ssh_setup_required"])
        self.assertIsNone(plan["ssh_authorized_key_fingerprint"])
        owner_key.assert_not_called()
        self.assertFalse((self.root / "never-work").exists())
        self.assertFalse((self.root / "never-output").exists())

    def test_executable_input_refuses_other_architectures_and_relocatable_objects(self):
        data = bytearray(64); data[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", data, 16, 3, 62)
        path = self.root / "tui"; path.write_bytes(data)
        self.assertEqual(IMAGE.executable_input(path), bytes(data))
        for kind, machine in ((1, 62), (3, 183)):
            struct.pack_into("<HH", data, 16, kind, machine); path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "x86-64"):
                IMAGE.executable_input(path)

    def test_external_firmware_manifest_hash_size_and_path_are_checked(self):
        manifest = self.root / "firmware.json"
        data = b"pinned firmware fixture"
        item = {"name": "mediatek/test.bin", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        manifest.write_text(json.dumps({"files": [item]}))
        firmware = self.root / "firmware"; (firmware / "mediatek").mkdir(parents=True)
        (firmware / item["name"]).write_bytes(data)
        profile = {"firmware_manifest": "firmware.json",
                   "firmware_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}
        with mock.patch.object(IMAGE, "REPO", self.root):
            found, facts = IMAGE.extra_firmware(firmware, profile)
            self.assertEqual(found, {"lib/firmware/mediatek/test.bin": data})
            self.assertEqual(facts, [item])
            (firmware / item["name"]).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                IMAGE.extra_firmware(firmware, profile)
            item["name"] = "../escape.bin"
            manifest.write_text(json.dumps({"files": [item]}))
            profile.pop("firmware_manifest_sha256")
            with self.assertRaisesRegex(ValueError, "unsafe"):
                IMAGE.extra_firmware(firmware, profile)

    def test_graphical_launcher_rejects_unknown_modes_before_system_calls(self):
        with mock.patch.object(GRAPHICS.sys, "argv", ["launch-graphics", "shell"]), \
             mock.patch.object(GRAPHICS.os, "geteuid", return_value=0), \
             mock.patch.object(GRAPHICS.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                GRAPHICS.main()
            run.assert_not_called()

    def disk(self):
        return {"type": "disk", "tran": "usb", "rm": True, "serial": "fixture-serial",
                "size": 16000000000, "children": [{"type": "part", "fstype": "vfat",
                "uuid": "1234-ABCD", "mountpoints": [str(self.root)]}]}

    def test_usb_identity_requires_matching_transport_capacity_serial_and_mount(self):
        self.assertEqual(STAGE.validate_description(self.disk(), "fixture-serial", 16000000000,
                                                   self.root)["uuid"], "1234-ABCD")
        for field, value in (("tran", "nvme"), ("serial", "other"), ("size", 128000000000), ("rm", False)):
            with self.subTest(field=field):
                disk = self.disk(); disk[field] = value
                with self.assertRaises(ValueError):
                    STAGE.validate_description(disk, "fixture-serial", 16000000000, self.root)

    def test_usb_rejects_nonfat_ambiguous_or_wrong_mount(self):
        for mutate in (lambda x: x["children"][0].update(fstype="ext4"),
                       lambda x: x["children"].append(dict(x["children"][0])),
                       lambda x: x["children"][0].update(mountpoints=["/other"])):
            disk = self.disk(); mutate(disk)
            with self.assertRaises(ValueError):
                STAGE.validate_description(disk, "fixture-serial", 16000000000, self.root)

    def source(self):
        payloads = {"bzImage": b"kernel", "initramfs.cpio.gz": b"initramfs",
                    "vram.txt": b"1024\n",
                    "bootargs.template.txt": b"psx.diag_uuid=@USB_UUID@\n",
                    "psx-diagnostics/rootfs.tar.gz": b"rootfs"}
        meta = {key: hashlib.sha256(payloads[name]).hexdigest() for name, key in
                (("bzImage", "kernel_sha256"), ("initramfs.cpio.gz", "initramfs_sha256"),
                 ("psx-diagnostics/rootfs.tar.gz", "rootfs_sha256"))}
        meta["hardware_acceptance"] = False
        meta["vram_mib"] = 1024
        payloads["psx-diagnostics/boot-set.json"] = json.dumps(meta).encode()
        for name, data in payloads.items():
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
        self.rehash_source()
        return meta

    def rehash_source(self):
        files = sorted(p for p in self.root.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
        (self.root / "SHA256SUMS").write_text("".join(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + str(p.relative_to(self.root)) + "\n"
            for p in files))

    def test_source_boot_receipt_and_every_payload_must_agree(self):
        self.source()
        self.assertEqual(len(STAGE.source_files(self.root)[0]), 6)
        (self.root / "bzImage").write_bytes(b"different-kernel")
        self.rehash_source()
        with self.assertRaisesRegex(ValueError, "disagrees"):
            STAGE.source_files(self.root)

    def test_source_refuses_hardware_acceptance_and_unrelated_root_files(self):
        meta = self.source(); meta["hardware_acceptance"] = True
        (self.root / "psx-diagnostics/boot-set.json").write_text(json.dumps(meta)); self.rehash_source()
        with self.assertRaisesRegex(ValueError, "acceptance"):
            STAGE.source_files(self.root)
        self.source(); (self.root / "unrelated.txt").write_text("not a boot payload"); self.rehash_source()
        with self.assertRaisesRegex(ValueError, "unexpected"):
            STAGE.source_files(self.root)

    def test_vram_loader_override_must_match_the_pinned_profile(self):
        self.source()
        (self.root / "vram.txt").write_text("4096\n"); self.rehash_source()
        with self.assertRaisesRegex(ValueError, "VRAM"):
            STAGE.source_files(self.root)

    def test_atomic_copy_replaces_one_regular_file_and_cleans_temporary(self):
        source = self.root / "source"; source.write_bytes(b"selected payload")
        destination = self.root / "destination"
        STAGE.atomic_write(destination, source=source)
        self.assertEqual(destination.read_bytes(), b"selected payload")
        self.assertEqual(list(self.root.glob(".psx-copy-*")), [])

    def test_detached_mount_path_cannot_redirect_copy_onto_parent_filesystem(self):
        mountpoint = self.root / "mounted"; mountpoint.mkdir()
        held = self.root / "detached"
        with STAGE.pinned_mount(mountpoint, mountpoint.stat().st_dev):
            mountpoint.rename(held)
            mountpoint.mkdir()
            STAGE.atomic_write(Path("payload"), data=b"held filesystem")
        self.assertEqual((held / "payload").read_bytes(), b"held filesystem")
        self.assertEqual(list(mountpoint.iterdir()), [])

    def test_mount_device_mismatch_stops_before_any_write(self):
        before = Path.cwd()
        with self.assertRaisesRegex(ValueError, "partition"), STAGE.pinned_mount(self.root, -1):
            self.fail("unmatched filesystem became writable")
        self.assertEqual(Path.cwd(), before)

    def test_generated_usb_metadata_requires_matching_owned_receipt(self):
        state = self.root / "psx-diagnostics"; state.mkdir()
        sums = state / "USB-SHA256SUMS"; sums.write_text("unowned\n")
        device = {"serial": "fixture", "bytes": 16000000000, "uuid": "1234-ABCD"}
        with self.assertRaisesRegex(ValueError, "unowned"):
            STAGE.previous_install(self.root, device)
        previous = {"owner": "psx-diagnostics-usb-v1", "target": device, "files": {"bzImage": "a" * 64},
                    "verified_readback": True, "hardware_acceptance": False}
        (state / "installed.json").write_text(json.dumps(previous))
        with self.assertRaisesRegex(ValueError, "checksum"):
            STAGE.previous_install(self.root, device)
        sums.write_text(STAGE.installed_sums(previous["files"]))
        self.assertEqual(STAGE.previous_install(self.root, device), previous)

    def test_boot_set_cannot_overwrite_generated_install_state(self):
        self.source()
        (self.root / "psx-diagnostics/installed.json").write_text("{}")
        self.rehash_source()
        with self.assertRaisesRegex(ValueError, "reserved"):
            STAGE.source_files(self.root)

    def test_mts_first_publication_refuses_attempts_allowed_or_terminal_state(self):
        state = {"bound": 1, "allowed": 0, "attempted": 0, "published": 0,
                 "terminal_fault": 0, "admin_up": 0}
        self.assertTrue(NET.fresh(state))
        for key in ("allowed", "attempted", "published", "terminal_fault", "admin_up"):
            with self.subTest(key=key):
                self.assertFalse(NET.fresh(dict(state, **{key: 1})))

    def test_mts_existing_connection_never_writes_gate_or_reopens_device(self):
        (self.root / "autoarm").write_text("N\n")
        (self.root / "allow_open").write_text("unchanged")
        state = {"bound": 1, "published": 1, "engine_live": 1, "terminal_fault": 0,
                 "carrier": 1, "admin_up": 1, "dma": [[1234, 4096]]}
        calls = []
        def command(args, timeout=15):
            calls.append(args)
            return '[{"dst":"default"}]' if args[:3] == ["ip", "-json", "-4"] else ""
        with mock.patch.object(NET, "PARAMETERS", self.root), mock.patch.object(NET, "report", return_value=state), \
             mock.patch.object(NET, "connected", return_value=True), mock.patch.object(NET, "command", side_effect=command), \
             mock.patch.object(NET, "service_active", return_value=False), \
             mock.patch.object(NET.os, "geteuid", return_value=0), \
             mock.patch.object(NET.os, "uname", return_value=type("U", (), {"release": NET.RELEASE})()), \
             mock.patch.object(NET.sys, "argv", ["ethernet-up"]), mock.patch("sys.stdout", new_callable=io.StringIO):
            NET.main()
        self.assertEqual((self.root / "allow_open").read_text(), "unchanged")
        self.assertFalse(any("link" in c or "start" in c for c in calls))

    def test_receipt_binds_root_contract_even_when_kernel_release_is_unchanged(self):
        expected = {"kernel_release": "same-release", "rootfs_sha256": "b" * 64,
                    "hardware_acceptance": False}
        external = dict(expected, initramfs_sha256="c" * 64)
        actual = RECEIPT.validate(expected, external, "same-release", "c" * 64)
        self.assertFalse(actual["manifest_validation"]["hardware_acceptance"])
        self.assertTrue(actual["manifest_validation"]["embedded_root_contract_verified"])
        for other in (dict(external, rootfs_sha256="d" * 64), dict(external, additional=True),
                      dict(external, initramfs_sha256="e" * 64)):
            with self.subTest(external=other), self.assertRaises(ValueError):
                RECEIPT.validate(expected, other, "same-release", "c" * 64)
        with self.assertRaisesRegex(ValueError, "kernel release"):
            RECEIPT.validate(expected, external, "other-release", "c" * 64)

    def lease_env(self):
        return {"interface": "psx0", "ip": "192.0.2.2", "subnet": "255.255.255.0",
                "lease": "60", "router": "192.0.2.1", "dns": "192.0.2.53 2001:db8::53"}

    def test_dhcp_validates_address_netmask_lifetime_and_dns_without_shell(self):
        self.assertEqual(DHCP.lease(self.lease_env())["prefix"], 24)
        for field, value in (("interface", "eth0"), ("ip", "$(touch /oops)"), ("subnet", "255.0.255.0"),
                             ("lease", "0"), ("lease", "4294967296"), ("dns", "server;bad")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                DHCP.lease(dict(self.lease_env(), **{field: value}))

    def test_dhcp_deconfig_only_removes_owned_label_and_route_preserving_manual_dns(self):
        state, resolv = self.root / "lease.json", self.root / "resolv.conf"
        state.write_text(json.dumps(DHCP.lease(self.lease_env())))
        resolv.write_text("nameserver 198.51.100.53\n")
        addresses = {"addr_info": [
            {"local": "192.0.2.2", "prefixlen": 24, "label": DHCP.LABEL},
            {"local": "192.0.2.10", "prefixlen": 24, "label": "psx0"}]}
        calls = []
        def ip(*args, **kwargs):
            calls.append(args)
            return type("Result", (), {"stdout": json.dumps([addresses])})()
        with mock.patch.object(DHCP, "STATE", state), mock.patch.object(DHCP, "RESOLV", resolv), \
             mock.patch.object(DHCP, "ip", side_effect=ip):
            DHCP.deconfigure()
        self.assertFalse(state.exists())
        self.assertEqual(resolv.read_text(), "nameserver 198.51.100.53\n")
        self.assertIn(("address", "del", "192.0.2.2/24", "dev", "psx0"), calls)
        self.assertIn(("route", "del", "default", "via", "192.0.2.1", "dev", "psx0", "proto", "dhcp"), calls)
        self.assertFalse(any("192.0.2.10/24" in c or "down" in c for c in calls))

    def test_dhcp_deconfig_does_not_remove_an_unlabelled_replacement_address(self):
        state, resolv = self.root / "lease.json", self.root / "resolv.conf"
        old = DHCP.lease(self.lease_env()); state.write_text(json.dumps(old)); resolv.write_text(old["resolv"])
        addresses = [{"addr_info": [{"local": old["address"], "prefixlen": 24, "label": "psx0"}]}]
        with mock.patch.object(DHCP, "STATE", state), mock.patch.object(DHCP, "RESOLV", resolv), \
             mock.patch.object(DHCP, "ip", return_value=type("Result", (), {"stdout": json.dumps(addresses)})()) as ip:
            DHCP.deconfigure()
        self.assertFalse(any(call.args[:2] == ("address", "del") for call in ip.call_args_list))
        self.assertEqual(resolv.read_text(), "")


if __name__ == "__main__":
    unittest.main()

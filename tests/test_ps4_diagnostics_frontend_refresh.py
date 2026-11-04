"""Refresh a synthetic four-asset bundle without touching devices or networking."""
import gzip
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(name, loader)
    app = importlib.util.module_from_spec(spec)
    sys.modules[name] = app
    loader.exec_module(app)
    return app


REFRESH = load("diagnostics_refresh_fixture", "scripts/refresh-ps4-diagnostics-frontend")
IMAGE = load("diagnostics_refresh_image_fixture", "scripts/build-ps4-diagnostics")
WRITER = load("diagnostics_refresh_cpio_writer", "scripts/ps4-diagnostics-archive")
CPIO = load("diagnostics_refresh_cpio_parser", "scripts/ps4-diagnostics-archive")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FrontendRefreshTests(unittest.TestCase):
    def test_old_four_asset_bundle_gains_current_network_helper_without_root_or_kernel_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            repo, base, output = directory / "repo", directory / "base", directory / "output"
            (base / "psx-diagnostics").mkdir(parents=True)
            network = b"#!/usr/bin/env python3\nNETWORK_REVISION = 'wifi-fixed-fixture'\n"
            inputs = {
                "rootfs/ps4-diagnostics-desktop/start-console": b"#!/bin/sh\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/prepare-console": b"#!/usr/bin/python3\npass\n",
                "diagnostics/ps4/lab/style.css": b"body {color: olive}\n",
                "diagnostics/ps4/network.py": network,
                "diagnostics/ps4/controller.py": b"#!/usr/bin/env python3\nCONTROLLER_REVISION = 'review-fixture'\n",
                "rootfs/ps4-diagnostics-desktop/launch-graphics": b"#!/usr/bin/python3\npass\n",
                "rootfs/ps4-diagnostics-desktop/graphics-start": b"#!/bin/bash\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/graphics-client": b"#!/bin/sh\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/graphics-session": b"#!/bin/bash\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/graphics-window": b"#!/bin/bash\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/wait-graphics-wm": b"#!/usr/bin/python3\npass\n",
                "rootfs/ps4-diagnostics-desktop/20-psx-diagnostics.conf": b'Section "Device"\nEndSection\n',
                "diagnostics/ps4/preferences.py": b"#!/usr/bin/env python3\npass\n",
                "diagnostics/ps4/themes.json": b'{"schema_version":1,"themes":[]}\n',
                "diagnostics/ps4/themes-LICENSE": b"Synthetic palette license\n",
                "diagnostics/ps4/lab/index.html": b"<!doctype html><title>Lab fixture</title>\n",
                "diagnostics/ps4/lab/lab.js": b"const fixture = true;\n",
                "rootfs/ps4-diagnostics-desktop/prepare-chromium-provider": b"#!/usr/bin/python3\npass\n",
                "rootfs/ps4-diagnostics-desktop/launch-chromium-provider": b"#!/usr/bin/python3\npass\n",
                "diagnostics/ps4/ssh_access.py": b"#!/usr/bin/env python3\npass\n",
                "scripts/build-ps4-diagnostics": (ROOT / "scripts/build-ps4-diagnostics").read_bytes(),
            }
            for name, data in inputs.items():
                path = repo / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            executable = bytearray(64); executable[:7] = b"\x7fELF\x02\x01\x01"
            struct.pack_into("<HH", executable, 16, 3, 62)
            tui = directory / "tui"; tui.write_bytes(executable)
            libraries = {name: bytes(executable) + name.encode() for name in IMAGE.RUNTIME_LIBRARY_PROVIDERS}
            providers = {name: dict(provider, bytes=len(libraries[name]), sha256=sha(libraries[name]))
                         for name, provider in IMAGE.RUNTIME_LIBRARY_PROVIDERS.items()}
            file_providers = {}
            for name, provider in IMAGE.RUNTIME_FILE_PROVIDERS.items():
                data = (bytes(executable) if provider['kind'] == 'program'
                        else b'\x00\x01\x00\x00synthetic TrueType font' if provider['kind'] == 'font'
                        else b'Synthetic OFL license')
                libraries[name] = data
                file_providers[name] = dict(provider, bytes=len(data), sha256=sha(data))
            library_dir = directory / "libraries"; library_dir.mkdir()
            for name, data in libraries.items():
                (library_dir / name).write_bytes(data)
            with mock.patch.object(IMAGE, "RUNTIME_LIBRARY_PROVIDERS", providers), \
                 mock.patch.object(IMAGE, "RUNTIME_FILE_PROVIDERS", file_providers):
                provider_receipt = IMAGE.runtime_library_receipt()
            (library_dir / "provider-receipt.json").write_text(json.dumps(provider_receipt))
            root_image, kernel = b"unchanged-root-fixture", b"unchanged-kernel-fixture"
            old_frontend = {
                "psx-diagnostics-tui": bytes(executable), "start-console": b"#!/bin/sh\ntrue\n",
                "prepare-console": b"#!/usr/bin/python3\npass\n", "style.css": b"old css fixture",
            }
            old_facts = {'schema_version': 1, 'total_bytes': sum(map(len, old_frontend.values())),
                         'files': [{'name': name, 'bytes': len(data), 'sha256': sha(data),
                                    'mode': f'{IMAGE.FRONTEND_FILES[name][1]:04o}',
                                    'bind_target': IMAGE.FRONTEND_FILES[name][0]}
                                   for name, data in old_frontend.items()]}
            expected = {
                "profile": "fixture-console", "kernel_release": "fixture", "source_date_epoch": 1234,
                "ram_root_mib": 0, "ui_backend": "ratatui", "root_format": "ext4-ro-loop",
                "rootfs_file": "psx-diagnostics/rootfs.ext4", "rootfs_sha256": sha(root_image),
                "kernel_sha256": sha(kernel), "vram_mib": 1024, "hardware_acceptance": False,
                "maximum_boot_set_bytes": 16*1024**2, "tui_sha256": "a"*64,
                "snapshot_sums_sha256": IMAGE.RUNTIME_SNAPSHOT_SHA256,
                "frontend_overrides": old_facts,
            }
            busybox = b"synthetic-static-busybox"
            members = {"init": b"old fixture init", "bin/busybox": busybox,
                       "etc/psx-diagnostics-expected.json": json.dumps(expected).encode(),
                       "lib/firmware/fixture.bin": b"preserved firmware fixture"}
            members.update({"psx-frontend/" + name: data for name, data in old_frontend.items()})
            raw = b"".join(WRITER.entry(index, 0o100755, 1, 1234, name, data)
                           for index, (name, data) in enumerate(members.items(), 1))
            raw += WRITER.entry(len(members)+1, 0, 1, 1234, "TRAILER!!!")
            initial = {
                "bzImage": kernel, "psx-diagnostics/rootfs.ext4": root_image,
                "initramfs.cpio.gz": gzip.compress(raw, mtime=0), "vram.txt": b"1024\n",
                "bootargs.template.txt": b"fixture-bootargs\n",
            }
            external = dict(expected, initramfs_sha256=sha(initial["initramfs.cpio.gz"]))
            initial["psx-diagnostics/boot-set.json"] = (json.dumps(external)+"\n").encode()
            for name, data in initial.items():
                (base / name).write_bytes(data)
            (base / "SHA256SUMS").write_text("".join(f"{sha(data)}  {name}\n" for name, data in initial.items()))
            real_load = REFRESH.load
            validator = mock.Mock()

            def fixture_load(name, path):
                if name == "psx_frontend_elf":
                    return SimpleNamespace(validate_busybox=validator)
                app = real_load(name, ROOT / "scripts" / path.name)
                if name == "psx_frontend_image":
                    app.RUNTIME_LIBRARY_PROVIDERS = providers
                    app.RUNTIME_FILE_PROVIDERS = file_providers
                return app

            with mock.patch.object(REFRESH, "REPO", repo), mock.patch.object(REFRESH, "load", side_effect=fixture_load):
                result = REFRESH.refresh(base, tui, output, revision="wifi-fixture", runtime_library_dir=library_dir)
            validator.assert_called_once_with(busybox)
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["root_image_unchanged"])
            self.assertEqual((output / "bzImage").read_bytes(), kernel)
            self.assertEqual((output / "psx-diagnostics/rootfs.ext4").read_bytes(), root_image)
            for name, data in initial.items():
                self.assertEqual((base / name).read_bytes(), data)
            entries, _ = CPIO.parse_newc(gzip.decompress((output / "initramfs.cpio.gz").read_bytes()))
            found = {entry.name.decode(): entry for entry in entries}
            self.assertEqual(found["psx-frontend/network.py"].data, network)
            self.assertEqual(found["lib/firmware/fixture.bin"].data, members["lib/firmware/fixture.bin"])
            receipt = json.loads((output / "psx-diagnostics/boot-set.json").read_text())
            files = receipt["frontend_overrides"]["files"]
            self.assertEqual(len(files), 29)
            network_fact = next(item for item in files if item["name"] == "network.py")
            self.assertEqual(network_fact["sha256"], sha(network))
            self.assertEqual(network_fact["bind_target"], "/opt/psx-diagnostics/network.py")
            self.assertEqual(found["psx-frontend/controller.py"].data, inputs["diagnostics/ps4/controller.py"])
            self.assertEqual(found["psx-frontend/wait-graphics-wm"].data,
                             inputs["rootfs/ps4-diagnostics-desktop/wait-graphics-wm"])
            self.assertEqual(found["psx-frontend/xorg-diagnostics.conf"].data,
                             inputs["rootfs/ps4-diagnostics-desktop/20-psx-diagnostics.conf"])
            self.assertEqual(receipt["frontend_overrides"]["runtime_libraries"], provider_receipt)
            for name, data in libraries.items():
                self.assertEqual(found["psx-frontend/" + name].data, data)
            self.assertEqual(receipt["rootfs_sha256"], external["rootfs_sha256"])
            self.assertEqual(receipt["kernel_sha256"], external["kernel_sha256"])


if __name__ == "__main__":
    unittest.main()

"""SATA reader fixtures only: no host device, controller or workload is used."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("psx_sata_read_tests", ROOT / "diagnostics/ps4/sata.py")
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
R, U = S.module("runner"), S.module("uvd")


class SataReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runner = R.Runner(self.root / "results", root_prefix=self.root)
        self.runner.catalogue["expected_kernel_release"] = S.RELEASE
        self.runner.results_dir.mkdir()
        self.controller = self.root / "sys/devices/pci0000:00/0000:00:14.7"
        self.controller.mkdir(parents=True)
        pci = self.root / "sys/bus/pci/devices/0000:00:14.7"
        pci.parent.mkdir(parents=True); pci.symlink_to(self.controller)
        self.write("/sys/devices/pci0000:00/0000:00:14.7/vendor", "0x104d")
        self.write("/sys/devices/pci0000:00/0000:00:14.7/device", "0x90de")
        self.ata = self.controller / "ata7"
        port = self.ata / "ata_port/ata7"; port.mkdir(parents=True)
        (port / "port_no").write_text("1"); (port / "nr_pmp_links").write_text("0")
        link = self.root / "sys/class/ata_port/ata7"
        link.parent.mkdir(parents=True); link.symlink_to(port)
        self.disk = self.ata / "host0/target0:0:0/0:0:0:0/block/sdc"
        self.disk.mkdir(parents=True)
        (self.root / "sys/class/block").mkdir(parents=True)
        (self.root / "sys/class/block/sdc").symlink_to(self.disk)
        for key, value in {"ro": "1", "removable": "0", "size": "262144", "dev": "8:32",
                           "diskseq": "17", "queue/logical_block_size": "512",
                           "queue/physical_block_size": "4096", "device/type": "0",
                           "device/serial": "PRIVATE-SERIAL-DO-NOT-RETAIN"}.items():
            path = self.disk / key; path.parent.mkdir(exist_ok=True, parents=True); path.write_text(value)
        (self.disk / "holders").mkdir()
        self.part = self.disk / "sdc1"; self.part.mkdir()
        for key, value in (("ro", "1"), ("dev", "8:33"), ("partition", "1")):
            (self.part / key).write_text(value)
        (self.part / "holders").mkdir()
        (self.root / "sys/class/block/sdc1").symlink_to(self.part)
        self.write("/dev/sdc", "")
        self.write("/proc/1/mountinfo", "1 0 8:1 / / rw - ext4 /dev/sda1 rw\n")
        self.write("/proc/swaps", "Filename Type Size Used Priority\n")
        self.write("/proc/sys/kernel/random/boot_id", "a029bacb-91e0-4c9c-94a0-128fd5eae7bf")
        self.write("/proc/sys/kernel/osrelease", S.RELEASE)
        self.write("/proc/cmdline", " ".join(S.ARGUMENTS + tuple(U.POWEROFF_BOOT_ARGUMENTS)))
        for key, value in (("/sys/module/libahci/parameters/ps4_probe_ms", "5"),
                           ("/sys/module/libahci/parameters/ps4_ahci_poll_ms", "0"),
                           ("/sys/module/ps4_bpcie/parameters/ps4_sata_phy_orbit", "Y")):
            self.write(key, value)
        for argument in U.POWEROFF_BOOT_ARGUMENTS:
            self.write("/sys/module/amdgpu/parameters/" + argument.split(".", 1)[1].split("=", 1)[0], "1")
        manifest = copy.deepcopy(U.ALPHA5)
        manifest.update(rootfs_sha256="a" * 64, initramfs_sha256="b" * 64,
            manifest_validation={"embedded_root_contract_verified": True,
                                 "observed_kernel_release": S.RELEASE,
                                 "observed_media_initramfs_sha256": "b" * 64})
        self.manifest = self.root / self.runner.manifest_path.lstrip("/")
        self.write(self.runner.manifest_path, json.dumps(manifest))
        self.records = {}
        for index, test in enumerate(("cpu.compute", "memory.pattern")):
            record = {"test_id": test, "test_version": self.runner.tests[test]["version"],
                "identity": self.runner.identity(), "phase": "completed", "status": "PASS",
                "stop_required": False, "hardware_acceptance": False, "run_id": "core-" + str(index),
                "started_at": "2026-11-05T20:00:00Z", "parameters": {"seconds": 2} if index == 0 else {"memory_mib": 32},
                "evidence": {"digest": R.CPU_ANSWER, "iterations": 1} if index == 0 else {"verified_mib": 32, "patterns": [0, 85, 170, 255]},
                "commands": [{"returncode": 0, "signal": None, "timed_out": False,
                              "interrupted": False, "output_limited": False}]}
            path = self.runner.results_dir / ("core-" + str(index)) / "result.json"
            path.parent.mkdir(mode=0o700); path.write_text(json.dumps(record)); path.chmod(0o600)
            self.records[test] = path

    def write(self, logical, value):
        path = self.root / logical.lstrip("/"); path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(value)

    def report(self, target):
        hashes = ["a" * 64, "b" * 64, "c" * 64]
        return {"offsets_bytes": list(S.positions(target)), "region_bytes": S.SPAN, "passes": 2,
                "total_read_bytes": 6 * S.SPAN, "sha256": [hashes, list(hashes)],
                "host_page_cache_bypassed": True, "method": "O_RDONLY|O_DIRECT + aligned mmap + preadv", "elapsed_seconds": 1}

    def test_default_cli_is_inert_before_runner_or_hardware(self):
        with mock.patch.object(S, "module", side_effect=AssertionError("must not import runner")), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(S.main([]), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "NOT_RUN")

    def test_existing_output_is_refused_before_runner_or_hardware(self):
        path = self.root / "old.json"; path.write_text("preserved")
        with mock.patch.object(S, "module", side_effect=AssertionError("must not import runner")), self.assertRaises(ValueError):
            S.main(["--run", "--output", str(path)])
        self.assertEqual(path.read_text(), "preserved")

    def test_exact_preflight_and_private_serial_is_not_read_or_retained(self):
        identity, target, core = S.preflight(self.runner)
        self.assertEqual(identity["kernel_release"], S.RELEASE)
        self.assertEqual(target["name"], "sdc")
        self.assertEqual(target["ata"], "ata7")
        self.assertEqual(target["hardware_port"], 0)
        self.assertEqual(len(core), 2)
        self.assertNotIn("PRIVATE-SERIAL", json.dumps(target))

    def test_wrong_profile_unverified_root_and_changed_artifact_are_blocked(self):
        original = self.manifest.read_text()
        for field, value in (("profile", U.ALPHA4["profile"]), ("kernel_sha256", "0" * 64),
                             ("manifest_validation", {}), ("uvd_sources_unchanged_from_dev3", False)):
            changed = json.loads(original); changed[field] = value; self.manifest.write_text(json.dumps(changed))
            with self.subTest(field=field), self.assertRaises(ValueError): S.preflight(self.runner)
        self.manifest.write_text(original)

    def test_uvd_stop_and_boot_alias_conflicts_block_before_worker(self):
        with mock.patch.object(self.runner, "uvd_stop_reason", return_value="terminal"), self.assertRaises(ValueError):
            S.preflight(self.runner)
        current = self.runner.fs.text("/proc/cmdline")
        for suffix in (" libata.force=noncq", " libahci.ps4_probe_ms=0", " libahci.ps4_ahci_completion_probe_ms=5"):
            self.write("/proc/cmdline", current + suffix)
            with self.subTest(suffix=suffix), self.assertRaises(ValueError): S.preflight(self.runner)

    def test_core_oracle_latest_failure_and_identity_changes_block(self):
        path = self.records["cpu.compute"]; original = path.read_text()
        for change in (lambda r: r["evidence"].update(digest="0" * 64),
                       lambda r: r["identity"].update(boot_id="00000000-0000-0000-0000-000000000001"),
                       lambda r: r.update(status="FAIL")):
            value = json.loads(original); change(value); path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): S.preflight(self.runner)
        path.write_text(original)
        newer = path.parent.parent / "later" / "result.json"; newer.parent.mkdir()
        bad = json.loads(original); bad.update(status="FAIL", started_at="2026-11-05T21:00:00Z")
        newer.write_text(json.dumps(bad)); newer.chmod(0o600)
        with self.assertRaises(ValueError): S.preflight(self.runner)

    def test_block_readonly_and_partition_holders_are_mandatory(self):
        for path in (self.disk / "ro", self.part / "ro"):
            path.write_text("0")
            with self.assertRaises(ValueError): S.discover(self.runner.fs)
            path.write_text("1")
        (self.part / "holders/dm-0").touch()
        with self.assertRaises(ValueError): S.discover(self.runner.fs)

    def test_any_mount_namespace_or_swap_blocks(self):
        self.write("/proc/42/mountinfo", "2 0 8:33 / /secret rw - ext4 /dev/elsewhere rw\n")
        with self.assertRaises(ValueError): S.discover(self.runner.fs)
        (self.root / "proc/42/mountinfo").unlink(); (self.root / "proc/42").rmdir()
        self.write("/proc/swaps", "Filename Type Size Used Priority\n/dev/anything partition 4 0 -2\n")
        with self.assertRaises(ValueError): S.discover(self.runner.fs)

    def test_missing_kernel_thread_namespace_is_distinct_from_missing_live_namespace(self):
        self.write("/proc/2/status", "Name:\tkthreadd\nState:\tS (sleeping)\nKthread:\t1\n")
        self.assertEqual(S.discover(self.runner.fs)["name"], "sdc")
        self.write("/proc/3/status", "Name:\texited\nState:\tZ (zombie)\nKthread:\t0\n")
        self.assertEqual(S.discover(self.runner.fs)["name"], "sdc")
        self.write("/proc/4/status", "Name:\tlive\nState:\tS (sleeping)\nKthread:\t0\n")
        with self.assertRaises(ValueError): S.discover(self.runner.fs)

    def test_port_vendor_or_disk_generation_metadata_failure_is_rejected(self):
        for path, bad in ((self.controller / "vendor", "0x8086"),
                          (self.ata / "ata_port/ata7/port_no", "0"),
                          (self.ata / "ata_port/ata7/nr_pmp_links", "1"), (self.disk / "diskseq", "0")):
            old = path.read_text(); path.write_text(bad)
            with self.subTest(path=path), self.assertRaises(ValueError): S.discover(self.runner.fs)
            path.write_text(old)

    def test_usb_shared_parent_is_excluded_but_second_ata_disk_is_ambiguous(self):
        usb = self.controller / "usb1/1-1/host9/block/sdd"; usb.mkdir(parents=True)
        (self.root / "sys/class/block/sdd").symlink_to(usb)
        self.assertEqual(S.discover(self.runner.fs)["name"], "sdc")
        (self.root / "sys/class/block/sdd").unlink()
        other = self.controller / "ata8/host1/block/sdd"; other.mkdir(parents=True)
        (self.root / "sys/class/block/sdd").symlink_to(other)
        with self.assertRaises(ValueError): S.discover(self.runner.fs)

    def test_direct_reads_use_readonly_aligned_fd_and_exact_48_mib(self):
        target = S.discover(self.runner.fs)
        descriptor = os.open(self.root / "dev/sdc", os.O_RDONLY)
        opened, offsets = [], []
        def fake_open(path, flags):
            opened.append((path, flags)); return descriptor
        def fake_ioctl(fd, request, fmt):
            return {0x80081272: target["bytes"], 0x1268: 512, 0x125e: 1, 0x80081280: 17}[request]
        def fake_read(fd, buffers, offset):
            self.assertEqual(fd, descriptor); self.assertEqual(offset % 4096, 0)
            offsets.append(offset); buffers[0][:] = bytes([offset // S.SPAN % 256]) * S.SPAN
            return S.SPAN
        with mock.patch.object(S.os, "open", side_effect=fake_open), \
             mock.patch.object(S.os, "fstat", return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 32))), \
             mock.patch.object(S, "ioctl_number", side_effect=fake_ioctl), mock.patch.object(S.os, "preadv", side_effect=fake_read):
            result = S.read_direct(target, self.runner.fs)
        self.assertEqual(opened[0][1] & os.O_ACCMODE, os.O_RDONLY)
        self.assertTrue(opened[0][1] & os.O_DIRECT)
        self.assertTrue(opened[0][1] & os.O_NOFOLLOW)
        self.assertEqual(offsets, list(S.positions(target)) * 2)
        S.validate_reads(result, target)

    def test_real_fixture_worker_and_regular_file_device_are_refused(self):
        target = S.discover(self.runner.fs)
        with mock.patch.object(S.os, "fork", side_effect=AssertionError("must not fork")), self.assertRaises(ValueError):
            S.fork_read(target, self.runner.fs, 100)
        # The fixture node is a regular file. Its contents are never read.
        with mock.patch.object(S.os, "preadv", side_effect=AssertionError("must not read")), self.assertRaises(ValueError):
            S.read_direct(target, self.runner.fs)

    def test_direct_short_read_and_generation_change_never_retry(self):
        target = S.discover(self.runner.fs)
        for generation, count in ((99, S.SPAN), (17, S.SPAN - 512)):
            descriptor = os.open(self.root / "dev/sdc", os.O_RDONLY)
            def fake_ioctl(fd, request, fmt):
                return {0x80081272: target["bytes"], 0x1268: 512, 0x125e: 1, 0x80081280: generation}[request]
            with mock.patch.object(S.os, "open", return_value=descriptor), \
                 mock.patch.object(S.os, "fstat", return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 32))), \
                 mock.patch.object(S, "ioctl_number", side_effect=fake_ioctl), \
                 mock.patch.object(S.os, "preadv", return_value=count) as reader:
                with self.assertRaises(ValueError): S.read_direct(target, self.runner.fs)
            self.assertEqual(reader.call_count, 0 if generation == 99 else 1)

    def test_worker_pipe_and_deadline_with_only_a_synthetic_child(self):
        # The real fork/pipe/deadline code is exercised, but the only child
        # function is a mock returning a typed receipt, never device access.
        target = S.discover(self.runner.fs)
        pretend_live = types.SimpleNamespace(root=Path("/"), fixture=False)
        with mock.patch.object(S, "read_direct", return_value=self.report(target)):
            self.assertEqual(S.fork_read(target, pretend_live, time.monotonic() + 2), self.report(target))
            with self.assertRaises(TimeoutError):
                S.fork_read(target, pretend_live, time.monotonic() - 1)

    def test_typed_receipt_rejects_cached_short_or_mismatching_reads(self):
        target = S.discover(self.runner.fs)
        for change in (lambda r: r.update(total_read_bytes=1), lambda r: r.update(host_page_cache_bypassed=False),
                       lambda r: r["sha256"][1].__setitem__(0, "f" * 64), lambda r: r.update(offsets_bytes=[0, 1, 2])):
            value = self.report(target); change(value)
            with self.assertRaises(ValueError): S.validate_reads(value, target)

    def test_run_pass_is_repeatability_only_and_after_fault_is_terminal(self):
        target = S.discover(self.runner.fs)
        with mock.patch.object(S, "kernel_snapshot", return_value={"raw_retained": False}), \
             mock.patch.object(S, "fork_read", return_value=self.report(target)):
            result = S.run(self.runner)
        self.assertEqual(result["status"], "PASS"); self.assertIs(result["hardware_acceptance"], False)
        self.assertFalse(result["stop_required"]); self.assertNotIn("PRIVATE-SERIAL", json.dumps(result))
        with mock.patch.object(S, "kernel_snapshot", side_effect=[{}, ValueError("EH error"), ValueError("EH error")]), \
             mock.patch.object(S, "fork_read", return_value=self.report(target)):
            result = S.run(self.runner)
        self.assertEqual(result["status"], "INCONCLUSIVE"); self.assertTrue(result["stop_required"])
        with mock.patch.object(S, "kernel_snapshot", return_value={}), \
             mock.patch.object(S, "fork_read", side_effect=TimeoutError("worker timeout")):
            result = S.run(self.runner)
        self.assertEqual(result["status"], "INCONCLUSIVE"); self.assertTrue(result["stop_required"])

    def test_terminal_before_io_and_parent_budget_prevent_worker(self):
        with mock.patch.object(S, "kernel_snapshot", side_effect=S.KernelFault("EH before read")), \
             mock.patch.object(S, "fork_read") as worker:
            result = S.run(self.runner)
        self.assertEqual(result["status"], "BLOCKED"); self.assertTrue(result["stop_required"])
        worker.assert_not_called()
        prepared = S.preflight(self.runner)
        with mock.patch.object(S, "preflight", return_value=prepared), \
             mock.patch.object(S, "kernel_snapshot", return_value={}), \
             mock.patch.object(S, "fork_read") as worker, \
             mock.patch.object(S.time, "monotonic", side_effect=[0, 181, 182]):
            result = S.run(self.runner)
        self.assertEqual(result["status"], "BLOCKED"); worker.assert_not_called()

    def test_kernel_fault_classes_and_default_profile_are_conservative(self):
        for text in ("ata7.00: failed command: READ FPDMA QUEUED", "ata7: hard resetting link",
                     "ps4: completion probe stopped after 20s", "Buffer I/O error", "illegal qc_active transition", "Oops:"):
            self.assertTrue(any(p.search(text) for p in S.FAULTS.values()), text)
        with mock.patch.object(S.subprocess, "Popen", side_effect=AssertionError("must not execute")), self.assertRaises(ValueError):
            S.kernel_snapshot(self.runner, 100)


if __name__ == "__main__":
    unittest.main()

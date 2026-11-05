"""Small frontend FTP publication checks; all endpoints and bytes are synthetic."""
import contextlib
import copy
import io
import gzip
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import importlib.machinery
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / relative))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


HELPERS = load("psx_frontend_ftp_helpers", "tests/test_ps4_diagnostics_ftp.py")
APP = load("psx_frontend_ftp_tests", "scripts/update-ps4-diagnostics-frontend-ftp")
USB, SERIAL, CAPACITY, UUID = HELPERS.USB, HELPERS.SERIAL, HELPERS.CAPACITY, HELPERS.UUID
sha = HELPERS.sha
SMALL = {"initramfs.cpio.gz", "bootargs.txt", "psx-diagnostics/boot-set.json"}


class RecordingFTP(HELPERS.FakeFTP):
    def __init__(self, files):
        super().__init__(files)
        self.reads = []

    def retrbinary(self, command, callback, blocksize=8192):
        self.reads.append(command.removeprefix("RETR "))
        return super().retrbinary(command, callback, blocksize)


class FrontendFTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        self.source = self.temp / "source"
        self.source.mkdir()
        self.backup = self.temp / "backup"
        self.receipt = self.temp / "receipt.json"
        self.new = {
            "bzImage": b"same-kernel",
            "initramfs.cpio.gz": b"new-olive-initramfs",
            "vram.txt": b"1024\n",
            "bootargs.template.txt": b"psx.diag_uuid=@USB_UUID@ olive=1\n",
            "psx-diagnostics/rootfs.ext4": b"same-ext4-root",
            "psx-diagnostics/config": b"same-config",
        }
        self.metadata = {
            "profile": "fixture-ratatui", "kernel_release": "fixture-native",
            "kernel_sha256": sha(self.new["bzImage"]),
            "initramfs_sha256": sha(self.new["initramfs.cpio.gz"]),
            "config_sha256": sha(self.new["psx-diagnostics/config"]),
            "rootfs_sha256": sha(self.new["psx-diagnostics/rootfs.ext4"]),
            "rootfs_file": "psx-diagnostics/rootfs.ext4", "root_format": "ext4-ro-loop",
            "hardware_acceptance": False, "vram_mib": 1024,
            "maximum_boot_set_bytes": 2 * 1024**2, "ui_backend": "ratatui",
            "frontend_revision": "olive-fixture", "frontend_overrides": {"console": True},
            "frontend_refresh": {"root_image_unchanged": True},
        }
        old_meta = copy.deepcopy(self.metadata)
        old_meta.update(initramfs_sha256=sha(b"old-initramfs"), frontend_revision="old")
        self.old = {name: data for name, data in self.new.items() if name != "bootargs.template.txt"}
        self.old.update({"initramfs.cpio.gz": b"old-initramfs",
                         "bootargs.txt": b"psx.diag_uuid=1234-ABCD time=1600000000\n",
                         "psx-diagnostics/boot-set.json": (json.dumps(old_meta) + "\n").encode()})
        self.previous = {
            "owner": "psx-diagnostics-usb-v1", "verified_readback": True,
            "hardware_acceptance": False, "profile": self.metadata["profile"],
            "kernel_release": self.metadata["kernel_release"],
            "target": {"serial": SERIAL, "bytes": CAPACITY, "uuid": UUID},
            "files": {name: sha(data) for name, data in self.old.items()},
        }
        self.previous_raw = (json.dumps(self.previous) + "\n").encode()
        self.sums = APP.DEPLOY.STAGE.installed_sums(self.previous["files"]).encode()
        self.mutable = {
            USB + "/psx-diagnostics/results/run/result.json": b"operator result",
            USB + "/psx-diagnostics/state/ssh/key": b"synthetic state",
            USB + "/psx-diagnostics/backups/note": b"operator note",
        }
        self.ftp = RecordingFTP({USB + "/" + name: data for name, data in self.old.items()} | self.mutable | {
            USB + "/psx-diagnostics/installed.json": self.previous_raw,
            USB + "/psx-diagnostics/USB-SHA256SUMS": self.sums})
        self.write_source()

    def write_source(self):
        self.new["psx-diagnostics/boot-set.json"] = (json.dumps(self.metadata) + "\n").encode()
        for name, data in self.new.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.source / "SHA256SUMS").write_text(APP.DEPLOY.STAGE.installed_sums({
            name: sha(data) for name, data in self.new.items()}))

    def run_update(self, apply=True, extra=()):
        args = ["update", "--source", str(self.source), "--host", "fixture.invalid",
                "--expect-serial", SERIAL, "--expect-bytes", str(CAPACITY), "--expect-uuid", UUID,
                "--backup", str(self.backup), "--receipt", str(self.receipt), *extra]
        if apply:
            args.append("--apply")
        with mock.patch.object(APP.ftplib, "FTP", return_value=self.ftp), \
             mock.patch.object(APP.sys, "argv", args), \
             mock.patch.object(APP.uuid, "uuid4", return_value=SimpleNamespace(hex="frontendfixture")), \
             mock.patch.object(APP.DEPLOY.STAGE.time, "time", return_value=1800000000), \
             contextlib.redirect_stdout(io.StringIO()):
            APP.main()

    def assert_preserved(self):
        for name, data in self.mutable.items():
            self.assertEqual(self.ftp.files[name], data)
        for name in set(self.old) - SMALL:
            self.assertEqual(self.ftp.files[USB + "/" + name], self.old[name])
            self.assertNotIn(USB + "/" + name, self.ftp.reads)

    def assert_rolled_back(self):
        for name, data in self.old.items():
            self.assertEqual(self.ftp.files.get(USB + "/" + name), data, name)
        self.assertEqual(self.ftp.files[USB + "/psx-diagnostics/installed.json"], self.previous_raw)
        self.assertEqual(self.ftp.files[USB + "/psx-diagnostics/USB-SHA256SUMS"], self.sums)
        self.assertFalse(self.receipt.exists())
        self.assert_preserved()

    def use_alpha2_snapshot(self):
        self.metadata.update(profile='ps4-baikal-diagnostics-uvd-alpha2',
            kernel_release='7.2.11-psx-ps4-baikal-uvd-runtime-dev2', image_version='alpha2',
            uvd_runtime=True, frontend_refresh=None)
        additional = ['README.txt', 'alpha-build-receipt.json', 'alpha-inputs/MODULES.json',
            'alpha-inputs/PACKAGE.json', 'alpha-inputs/RUNTIME-IDENTITY.json',
            'alpha-inputs/build-inputs/runtime/LINK-ATTEMPT-01.json',
            'alpha-inputs/build-inputs/runtime/SOURCE-RECEIPT.json', 'artifacts.json',
            'manifest.txt', 'modules-'+self.metadata['kernel_release']+'.tar.zst',
            'source-pins.json', 'version.txt']
        for name in additional:
            self.new['psx-diagnostics/'+name] = ('synthetic alpha2 '+name).encode()
        self.metadata['modules_sha256'] = sha(self.new['psx-diagnostics/modules-'+self.metadata['kernel_release']+'.tar.zst'])
        self.write_source()
        self.old = {name:data for name,data in self.new.items() if name != 'bootargs.template.txt'}
        self.old['bootargs.txt'] = (b'psx.diag_uuid=1234-ABCD time=1600000000 pci=use_crs '
            b'libata.force=1.00:disable psx_mts_net.autoarm=1 amdgpu.ps4_uvd_runtime=1 '
            b'amdgpu.ps4_uvd_ready=1 amdgpu.ps4_uvd_poll_state=1 loglevel=3\n')
        self.publish_old_snapshot()
        self.assertEqual(len(self.previous['files']), 19)

    def publish_old_snapshot(self):
        self.previous.update(profile=self.metadata['profile'], kernel_release=self.metadata['kernel_release'],
            files={name:sha(data) for name,data in self.old.items()})
        self.previous_raw = (json.dumps(self.previous)+'\n').encode()
        self.sums = APP.DEPLOY.STAGE.installed_sums(self.previous['files']).encode()
        self.ftp = RecordingFTP({USB+'/'+name:data for name,data in self.old.items()} | self.mutable | {
            USB+'/psx-diagnostics/installed.json':self.previous_raw,
            USB+'/psx-diagnostics/USB-SHA256SUMS':self.sums})

    def test_default_plan_has_no_remote_or_local_mutation(self):
        original = dict(self.ftp.files)
        self.run_update(apply=False)
        self.assertEqual(self.ftp.files, original)
        self.assertEqual(self.ftp.mutations, [])
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.receipt.exists())
        self.assert_preserved()

    def test_actual_root_kernel_and_config_changes_refused_before_mutation(self):
        for name, key in (("psx-diagnostics/rootfs.ext4", "rootfs_sha256"),
                          ("bzImage", "kernel_sha256"), ("psx-diagnostics/config", "config_sha256")):
            original_data, original_pin = self.new[name], self.metadata[key]
            self.new[name] = b"changed unrelated payload"
            self.metadata[key] = sha(self.new[name])
            self.write_source()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.run_update()
            self.assertEqual(self.ftp.mutations, [])
            self.assertFalse(self.backup.exists())
            self.new[name], self.metadata[key] = original_data, original_pin

    def test_metadata_config_pin_cannot_change_independently(self):
        self.metadata["config_sha256"] = sha(b"unrelated config")
        self.write_source()
        with self.assertRaises(ValueError):
            self.run_update()
        self.assertEqual(self.ftp.mutations, [])

    def test_missing_frontend_contract_refused_before_mutation(self):
        self.metadata["frontend_refresh"]["root_image_unchanged"] = False
        self.write_source()
        with self.assertRaises(ValueError):
            self.run_update()
        self.assertEqual(self.ftp.mutations, [])

    def test_success_only_transfers_small_payloads_and_exactly_reads_back(self):
        self.run_update()
        marker = json.loads(self.ftp.files[USB + "/psx-diagnostics/installed.json"])
        self.assertEqual(marker["frontend_revision"], "olive-fixture")
        self.assertTrue(marker["verified_readback"])
        self.assertFalse(marker["hardware_acceptance"])
        self.assertEqual(set(marker["files"]), set(self.old))
        changed = set(json.loads(self.receipt.read_text())["changed_files"])
        self.assertEqual(changed, SMALL - {'bootargs.txt'})
        self.assertEqual(self.ftp.files[USB + '/bootargs.txt'], self.old['bootargs.txt'])
        for name in changed:
            data = self.ftp.files[USB + "/" + name]
            self.assertEqual(sha(data), marker["files"][name])
            self.assertGreaterEqual(self.ftp.reads.count(USB + "/" + name), 3)
            self.assertEqual((self.backup / name).read_bytes(), self.old[name])
        self.assertEqual((self.backup / "installed.json").read_bytes(), self.previous_raw)
        self.assertEqual((self.backup / "USB-SHA256SUMS").read_bytes(), self.sums)
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "PASS")
        self.assertEqual(self.ftp.files[USB + "/psx-diagnostics/USB-SHA256SUMS"],
                         APP.DEPLOY.STAGE.installed_sums(marker["files"]).encode())
        uploads = [path for operation, *paths in self.ftp.mutations if operation == "store" for path in paths]
        allowed = {USB + "/psx-diagnostics/" + name for name in ("installed.json", "USB-SHA256SUMS")}
        allowed |= {USB + "/.psx-frontend-frontendfixture/new/" + name for name in changed}
        self.assertEqual(set(uploads), allowed)
        self.assert_preserved()

    def test_rename_failure_before_publication_restores_old_set(self):
        self.ftp.fail_rename = (USB + "/initramfs.cpio.gz", "before")
        with self.assertRaises(OSError):
            self.run_update()
        self.assert_rolled_back()

    def test_lost_rename_acknowledgement_restores_old_set(self):
        self.ftp.fail_rename = (USB + "/initramfs.cpio.gz", "after")
        with self.assertRaises(OSError):
            self.run_update()
        self.assert_rolled_back()

    def test_frontend_preserves_verbose_and_unrelated_boot_arguments(self):
        value = b'psx.diag_uuid=1234-ABCD pci=use_crs intremap=off psx_mts_net.autoarm=1 libata.force=1.00:disable loglevel=7 ignore_loglevel time=1600000000\n'
        self.old['bootargs.txt'] = value
        self.previous['files']['bootargs.txt'] = sha(value)
        self.ftp.files[USB + '/bootargs.txt'] = value
        self.ftp.files[USB + '/psx-diagnostics/installed.json'] = (json.dumps(self.previous) + '\n').encode()
        self.ftp.files[USB + '/psx-diagnostics/USB-SHA256SUMS'] = APP.DEPLOY.STAGE.installed_sums(self.previous['files']).encode()
        self.run_update()
        self.assertEqual(self.ftp.files[USB + '/bootargs.txt'], value)
        self.assertNotIn('bootargs.txt', json.loads(self.receipt.read_text())['changed_files'])

    def test_verbose_uart_changes_only_logging_tokens(self):
        value = b'psx.diag_uuid=1234-ABCD pci=use_crs psx_mts_net.autoarm=1 libata.force=1.00:disable systemd.show_status=false loglevel=3\n'
        result = APP.verbose_uart_arguments(value).decode().split()
        self.assertIn('loglevel=7', result)
        self.assertIn('ignore_loglevel', result)
        retained = lambda seq: [x for x in seq if not x.startswith('loglevel=') and x != 'ignore_loglevel']
        self.assertEqual(retained(result), retained(value.decode().split()))
        self.assertEqual(APP.verbose_uart_arguments(APP.verbose_uart_arguments(value)), APP.verbose_uart_arguments(value))

    def test_generated_receipt_partial_upload_restores_old_set(self):
        self.ftp.fail_store = USB + "/psx-diagnostics/USB-SHA256SUMS"
        with self.assertRaises(OSError):
            self.run_update()
        self.assert_rolled_back()

    def test_corrupt_staging_readback_prevents_publication(self):
        self.ftp.corrupt_stage = True
        with self.assertRaises(ValueError):
            self.run_update()
        self.assert_rolled_back()

    def test_bootargs_only_requires_explicit_verbose_and_keeps_default_refresh_guard(self):
        self.use_alpha2_snapshot()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_update(extra=('--bootargs-only',))
        with self.assertRaisesRegex(ValueError, 'frontend-only refresh'):
            self.run_update(extra=('--verbose-uart',))
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.ftp.mutations, [])

    def test_bootargs_only_success_retains_the_entire_alpha2_set_without_large_reads(self):
        self.use_alpha2_snapshot()
        self.run_update(extra=('--bootargs-only', '--verbose-uart'))
        result = json.loads(self.receipt.read_text())
        marker = json.loads(self.ftp.files[USB+'/psx-diagnostics/installed.json'])
        self.assertEqual(result['changed_files'], ['bootargs.txt'])
        self.assertTrue(result['bootargs_only'])
        self.assertEqual(len(marker['files']), 19)
        expected = APP.verbose_uart_arguments(self.old['bootargs.txt'])
        self.assertEqual(self.ftp.files[USB+'/bootargs.txt'], expected)
        self.assertEqual((self.backup/'bootargs.txt').read_bytes(), self.old['bootargs.txt'])
        self.assertIn(b'loglevel=7', expected)
        self.assertIn(b'ignore_loglevel', expected)
        self.assertNotIn(b'initcall_debug', expected)
        for key in ('runtime', 'ready', 'poll_state'):
            self.assertIn(('amdgpu.ps4_uvd_'+key+'=1').encode(), expected)
        for name, data in self.old.items():
            if name != 'bootargs.txt':
                self.assertEqual(self.ftp.files[USB+'/'+name], data)
                self.assertEqual(marker['files'][name], self.previous['files'][name])
                if name != 'psx-diagnostics/boot-set.json':
                    self.assertNotIn(USB+'/'+name, self.ftp.reads)
        uploads = {paths[0] for operation,*paths in self.ftp.mutations if operation == 'store'}
        self.assertEqual(uploads, {USB+'/.psx-frontend-frontendfixture/new/bootargs.txt',
            USB+'/psx-diagnostics/installed.json', USB+'/psx-diagnostics/USB-SHA256SUMS'})
        self.assert_preserved()

    def test_bootargs_only_plan_is_read_only_for_alpha2_without_refresh_metadata(self):
        self.use_alpha2_snapshot()
        original = dict(self.ftp.files)
        self.run_update(apply=False, extra=('--bootargs-only', '--verbose-uart'))
        self.assertEqual(self.ftp.files, original)
        self.assertEqual(self.ftp.mutations, [])
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.receipt.exists())

    def test_bootargs_only_rejects_every_non_argument_payload_or_metadata_pin_change(self):
        self.use_alpha2_snapshot()
        names = ['bzImage', 'initramfs.cpio.gz', 'psx-diagnostics/rootfs.ext4',
            'psx-diagnostics/config', 'psx-diagnostics/modules-'+self.metadata['kernel_release']+'.tar.zst',
            'psx-diagnostics/alpha-inputs/RUNTIME-IDENTITY.json']
        fields = {'bzImage':'kernel_sha256', 'initramfs.cpio.gz':'initramfs_sha256',
            'psx-diagnostics/rootfs.ext4':'rootfs_sha256', 'psx-diagnostics/config':'config_sha256',
            names[4]:'modules_sha256'}
        original_new, original_metadata = copy.deepcopy(self.new), copy.deepcopy(self.metadata)
        for name in names:
            self.new, self.metadata = copy.deepcopy(original_new), copy.deepcopy(original_metadata)
            self.new[name] = b'changed bytes'
            if name in fields:
                self.metadata[fields[name]] = sha(self.new[name])
            self.write_source()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.run_update(extra=('--bootargs-only', '--verbose-uart'))
            self.assertEqual(self.ftp.mutations, [])
            self.assertFalse(self.backup.exists())
        for field in ('frontend_revision', 'frontend_overrides', 'rootfs_tui_sha256'):
            self.new, self.metadata = copy.deepcopy(original_new), copy.deepcopy(original_metadata)
            self.metadata[field] = None
            self.write_source()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_update(extra=('--bootargs-only', '--verbose-uart'))
            self.assertEqual(self.ftp.mutations, [])

    def test_bootargs_only_refuses_incomplete_set_other_profiles_and_no_argument_change(self):
        self.use_alpha2_snapshot()
        for changes in ({'profile':'ps4-baikal-diagnostics-uvd-alpha1'}, {'uvd_runtime':False}):
            original = copy.deepcopy(self.metadata)
            self.metadata.update(changes); self.write_source()
            with self.assertRaises(ValueError):
                self.run_update(extra=('--bootargs-only', '--verbose-uart'))
            self.assertEqual(self.ftp.mutations, [])
            self.metadata = original
        del self.new['psx-diagnostics/README.txt']
        (self.source/'psx-diagnostics/README.txt').unlink()
        self.write_source()
        with self.assertRaisesRegex(ValueError, '19-file'):
            self.run_update(extra=('--bootargs-only', '--verbose-uart'))
        self.use_alpha2_snapshot()
        self.old['bootargs.txt'] = APP.verbose_uart_arguments(self.old['bootargs.txt'])
        self.publish_old_snapshot()
        with self.assertRaisesRegex(ValueError, 'exactly bootargs.txt'):
            self.run_update(extra=('--bootargs-only', '--verbose-uart'))
        self.assertEqual(self.ftp.mutations, [])

    def test_bootargs_only_failure_rolls_back_arguments_and_generated_receipts(self):
        for failure in ('rename', 'receipt'):
            self.use_alpha2_snapshot()
            self.backup = self.temp/('backup-'+failure)
            if failure == 'rename':
                self.ftp.fail_rename = (USB+'/bootargs.txt', 'after')
            else:
                self.ftp.fail_store = USB+'/psx-diagnostics/USB-SHA256SUMS'
            with self.subTest(failure=failure), self.assertRaises(OSError):
                self.run_update(extra=('--bootargs-only', '--verbose-uart'))
            self.assert_rolled_back()


class PowerTestFTPTests(unittest.TestCase):
    def setUp(self):
        self.f = FrontendFTPTests('test_default_plan_has_no_remote_or_local_mutation')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        fixtures = load('psx_power_ftp_fixture_builder', 'tests/test_ps4_diagnostix_power.py')
        self.p = fixtures.PowerTestDerivationTests('test_real_receipts_cpio_and_every_immutable_payload_survive_derivation')
        self.p.setUp(); self.addCleanup(self.p.doCleanups); self.p.use_alpha4()
        self.p.metadata['frontend_revision']='uvd-alpha4'
        expected=dict(self.p.metadata);expected.pop('initramfs_sha256')
        path=self.p.source/'initramfs.cpio.gz'
        entries,trailer=fixtures.ARCHIVE.parse_newc(gzip.decompress(path.read_bytes()))
        path.write_bytes(gzip.compress(b''.join(entry.raw if entry.name.decode()!='etc/psx-diagnostics-expected.json'else
            fixtures.ARCHIVE.entry(50,entry.mode,entry.nlink,int(entry.header[46:54],16),entry.name.decode(),fixtures.APP.encoded(expected))
            for entry in entries)+trailer,mtime=0))
        for name in ['README.txt', 'alpha-inputs/MODULES.json', 'alpha-inputs/PACKAGE.json',
                     'alpha-inputs/RUNTIME-IDENTITY.json', 'alpha-inputs/build-inputs/runtime/SOURCE-RECEIPT.json',
                     'alpha-inputs/build-inputs/runtime/LINK-ATTEMPT-01.json', 'artifacts.json',
                     'manifest.txt', 'source-pins.json', 'version.txt']:
            path=self.p.source/'psx-diagnostics'/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(('synthetic preserved '+name).encode())
        self.p.publish()
        fixtures.APP.prepare(self.p.source,self.p.output,self.p.profile_path)
        self.f.source=self.p.output
        self.old_source=self.p.source
        self.archive=fixtures.ARCHIVE
        old_files,old=APP.DEPLOY.STAGE.source_files(self.old_source)
        self.f.old={name:(self.old_source/name).read_bytes()for name in old_files if name!='bootargs.template.txt'}
        self.f.old['bootargs.txt']=((self.old_source/'bootargs.template.txt').read_bytes()
            .replace(b'@USB_UUID@',UUID.encode()).rstrip()+b' loglevel=7 ignore_loglevel time=1600000000 pci=use_crs\n')
        self.f.previous.update(profile=old['profile'],kernel_release=old['kernel_release'],image_version='alpha4',
            files={name:sha(data)for name,data in self.f.old.items()})
        self.publish_remote()
        self.new_files={str(path.relative_to(self.f.source)):path.read_bytes()for path in self.f.source.rglob('*')if path.is_file()}
        self.assertEqual(len(old_files),19)

    def publish_remote(self):
        self.f.previous_raw=(json.dumps(self.f.previous)+'\n').encode()
        self.f.sums=APP.DEPLOY.STAGE.installed_sums(self.f.previous['files']).encode()
        self.f.ftp=RecordingFTP({USB+'/'+name:data for name,data in self.f.old.items()}|self.f.mutable|{
            USB+'/psx-diagnostics/installed.json':self.f.previous_raw,
            USB+'/psx-diagnostics/USB-SHA256SUMS':self.f.sums})

    def run_update(self,apply=True,extra=()):
        return self.f.run_update(apply=apply,extra=('--power-test','--previous-source',str(self.old_source),*extra))

    def republish_source(self):
        metadata_path=self.f.source/'psx-diagnostics/boot-set.json'
        metadata=json.loads(metadata_path.read_text())
        metadata['initramfs_sha256']=sha((self.f.source/'initramfs.cpio.gz').read_bytes())
        metadata['rootfs_sha256']=sha((self.f.source/metadata['rootfs_file']).read_bytes())
        metadata_path.write_text(json.dumps(metadata)+'\n')
        (self.f.source/'SHA256SUMS').write_text(APP.DEPLOY.STAGE.installed_sums({
            str(path.relative_to(self.f.source)):sha(path.read_bytes())for path in self.f.source.rglob('*')
            if path.is_file()and path.name!='SHA256SUMS'}))

    def test_power_plan_is_read_only_and_flags_require_explicit_previous_source(self):
        original=dict(self.f.ftp.files)
        self.run_update(apply=False)
        self.assertEqual(self.f.ftp.files,original);self.assertEqual(self.f.ftp.mutations,[])
        self.assertFalse(self.f.backup.exists());self.f.assert_preserved()
        for extra in (('--power-test',),('--power-test','--verbose-uart'),
                      ('--previous-source',str(self.old_source))):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                self.f.run_update(extra=extra)

    def test_power_update_transfers_only_three_small_files_and_updates_profile_preserving_actual_arguments(self):
        self.run_update()
        marker=json.loads(self.f.ftp.files[USB+'/psx-diagnostics/installed.json'])
        self.assertEqual(marker['profile'],'ps4-baikal-diagnostix-alpha4-power-test')
        self.assertEqual(marker['image_version'],'alpha4-power-test')
        expected=self.f.old['bootargs.txt']
        for key in ('runtime','ready','poll_state','poff'):
            expected=expected.replace(('amdgpu.ps4_uvd_'+key+'=1').encode(),('amdgpu.ps4_uvd_'+key+'=0').encode())
        self.assertEqual(self.f.ftp.files[USB+'/bootargs.txt'],expected)
        self.assertEqual(marker['clock_seed_epoch'],1600000000)
        self.assertEqual(set(json.loads(self.f.receipt.read_text())['changed_files']),SMALL)
        self.assertEqual(self.f.ftp.files[USB+'/psx-diagnostics/boot-set.json'],self.new_files['psx-diagnostics/boot-set.json'])
        for name in SMALL:self.assertEqual((self.f.backup/name).read_bytes(),self.f.old[name])
        uploads={paths[0]for operation,*paths in self.f.ftp.mutations if operation=='store'}
        self.assertEqual(uploads,{USB+'/.psx-frontend-frontendfixture/new/'+name for name in SMALL}|{
            USB+'/psx-diagnostics/installed.json',USB+'/psx-diagnostics/USB-SHA256SUMS'})
        self.f.assert_preserved()

    def test_power_source_root_and_cpio_payload_drift_are_rejected_before_ftp_mutation(self):
        for kind in ('root','cpio'):
            for name,data in self.new_files.items():(self.f.source/name).write_bytes(data)
            if kind=='root':(self.f.source/'psx-diagnostics/rootfs.ext4').write_bytes(b'unrelated changed root')
            else:
                path=self.f.source/'initramfs.cpio.gz'
                entries,trailer=self.archive.parse_newc(gzip.decompress(path.read_bytes()))
                chunks=[]
                for entry in entries:
                    chunks.append(entry.raw if entry.name.decode()!='lib/firmware/amdgpu/liverpool_uvd.bin'else
                        self.archive.entry(999,entry.mode,entry.nlink,1793500000,entry.name.decode(),b'changed firmware'))
                path.write_bytes(gzip.compress(b''.join(chunks)+trailer,mtime=0))
            self.republish_source()
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.run_update()
            self.assertEqual(self.f.ftp.mutations,[]);self.assertFalse(self.f.backup.exists())
            self.f.assert_preserved()

    def test_power_remote_owner_payload_pins_and_exact_parent_metadata_cannot_drift(self):
        for name in ('psx-diagnostics/rootfs.ext4','psx-diagnostics/boot-set.json'):
            original=self.f.previous['files'][name]
            self.f.previous['files'][name]=sha(b'changed remote ownership pin');self.publish_remote()
            with self.subTest(name=name),self.assertRaises(ValueError):self.run_update()
            self.assertEqual(self.f.ftp.mutations,[]);self.assertFalse(self.f.backup.exists())
            self.f.assert_preserved()
            self.f.previous['files'][name]=original

    def test_power_generated_receipt_failure_restores_old_profile_and_all_three_payloads(self):
        self.f.ftp.fail_store=USB+'/psx-diagnostics/USB-SHA256SUMS'
        with self.assertRaises(OSError):self.run_update()
        self.f.assert_rolled_back()


if __name__ == "__main__":
    unittest.main()

"""Small synthetic roots and in-memory FTP only; no console or network access."""
import contextlib
import copy
import ftplib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
def load(name, relative):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT/relative))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module

HELPERS = load('psx_alpha_ftp_test_helpers', 'tests/test_ps4_diagnostics_ftp.py')
APP = load('psx_alpha_ftp_tests', 'scripts/update-ps4-diagnostics-alpha-ftp')
USB, SERIAL, CAPACITY, UUID = HELPERS.USB, HELPERS.SERIAL, HELPERS.CAPACITY, HELPERS.UUID
ROOT_PATH = USB+'/'+APP.ROOT_FILE
sha = HELPERS.sha


class TransportFTP(HELPERS.FakeFTP):
    def __init__(self, files):
        super().__init__(files)
        self.reads = []
        self.rest_mode = 'preserve'
        self.fail_root_offset = None
        self.fail_restore = False
        self.abort_on_root = False
        self.failed_root_once = False
        self.root_attempts = []
        self.root_marker_states = []
        self.read_before_root_moves = []
        self.journal_path = None
        self.corrupt_full_stage = False
        self.corrupt_root_read = False

    def retrbinary(self, command, callback, blocksize=8192):
        path = command.removeprefix('RETR ')
        self.reads.append(path)
        if path == ROOT_PATH and self.corrupt_root_read and self.root_attempts:
            self.corrupt_root_read = False
            data = self.files[path]
            self.files[path] = b'X'+data[1:]
        return super().retrbinary(command, callback, blocksize)

    def storbinary(self, command, stream, blocksize=8192, callback=None, rest=None):
        path = command.removeprefix('STOR ')
        if rest is None:
            result = super().storbinary(command, stream, blocksize, callback)
            if self.corrupt_full_stage and path.endswith(APP.ROOT_FILE) and '/new/' in path:
                self.files[path] = b'corrupt full staging'
            return result
        self.mutations.append(('rest_store', path, rest))
        if self.rest_mode == 'unsupported':
            raise ftplib.error_perm('502 REST unsupported')
        value = stream.read()
        old = self.files[path]
        if path == ROOT_PATH:
            self.root_attempts.append(rest)
            marker = json.loads(self.files[USB+'/psx-diagnostics/installed.json'])
            self.root_marker_states.append(marker.get('verified_readback'))
            if marker.get('verified_readback') is not False:
                raise AssertionError('Root write began without incomplete receipt')
            events = [json.loads(line) for line in self.journal_path.read_text().splitlines()]
            assert any(event['event'] in ('root_range_attempt','rollback_range_attempt')
                       and event['offset'] == rest for event in events), 'Range was not journaled before write'
            if self.abort_on_root:
                partial = max(1,len(value)//2)
                self.files[path] = old[:rest]+value[:partial]+old[rest+partial:]
                raise SystemExit('simulated process death during root write')
            if self.failed_root_once and self.fail_restore:
                raise OSError('injected recovery transport failure')
            if rest == self.fail_root_offset and not self.failed_root_once:
                self.failed_root_once = True
                partial = max(1, len(value)//2)
                self.files[path] = old[:rest]+value[:partial]+old[rest+partial:]
                raise OSError('injected partial root range')
        if self.rest_mode == 'truncate' or self.rest_mode == 'zero-truncates' and rest == 0:
            self.files[path] = old[:rest]+value
        elif self.rest_mode == 'ignore':
            self.files[path] = value
        else:
            self.files[path] = old[:rest]+value+old[rest+len(value):]
        return '226 synthetic resumed store'

    def rename(self, source, target):
        if source == ROOT_PATH:
            self.read_before_root_moves.append(list(self.reads))
            marker = json.loads(self.files[USB+'/psx-diagnostics/installed.json'])
            self.root_marker_states.append(marker.get('verified_readback'))
            assert marker.get('verified_readback') is False, 'Root rename preceded incomplete receipt'
        return super().rename(source,target)


class AlphaFTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-alpha-ftp-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old_source = self.root/'alpha2'; self.old_source.mkdir()
        self.source = self.root/'alpha3'; self.source.mkdir()
        self.backup = self.root/'backup'
        self.receipt = self.root/'result.json'
        self.patchers = [mock.patch.object(APP, 'ROOT_BYTES', 128*1024),
                         mock.patch.object(APP, 'CAP', 4*1024**2),
                         mock.patch.object(APP, 'MAX_RANGE', 16*1024)]
        for patcher in self.patchers:
            patcher.start(); self.addCleanup(patcher.stop)
        root = bytes((i*41+i//251) % 251 for i in range(APP.ROOT_BYTES))
        new_root = bytearray(root)
        for offset in (0, 4*APP.BLOCK, 24*APP.BLOCK):
            new_root[offset:offset+137] = b'Z'*137
        common = {'bzImage':b'same kernel', 'vram.txt':b'1024\n',
            'bootargs.template.txt':b'psx.diag_uuid=@USB_UUID@ '+b' '.join(value.encode() for value in APP.GATES)+b'\n',
            'psx-diagnostics/config':b'same config'}
        extras = ['README.txt','alpha-build-receipt.json','alpha-inputs/MODULES.json',
            'alpha-inputs/PACKAGE.json','alpha-inputs/RUNTIME-IDENTITY.json',
            'alpha-inputs/build-inputs/runtime/LINK-ATTEMPT-01.json',
            'alpha-inputs/build-inputs/runtime/SOURCE-RECEIPT.json', 'artifacts.json',
            'manifest.txt', 'modules-'+APP.RELEASE+'.tar.zst', 'source-pins.json','version.txt']
        common.update({'psx-diagnostics/'+name:('same '+name).encode() for name in extras})
        self.old = dict(common, **{APP.ROOT_FILE:root, 'initramfs.cpio.gz':b'old initramfs'})
        self.new = dict(common, **{APP.ROOT_FILE:bytes(new_root), 'initramfs.cpio.gz':b'new initramfs'})
        self.new['psx-diagnostics/README.txt'] = b'Alpha3: corrected firmware placement; no hardware acceptance.'
        self.new['psx-diagnostics/alpha-build-receipt.json'] = b'{"synthetic_alpha3_build":"verified fixture"}\n'
        base = {'kernel_release':APP.RELEASE, 'kernel_sha256':sha(common['bzImage']),
            'config_sha256':sha(common['psx-diagnostics/config']),
            'modules_sha256':sha(common['psx-diagnostics/modules-'+APP.RELEASE+'.tar.zst']),
            'module_count':41, 'source_commit':'12'*20, 'mesa_manifest_sha256':'34'*32,
            'snapshot_sums_sha256':'56'*32, 'poweroff_fix':{'uvd_shutdown_park_unchanged':True},
            'root_format':'ext4-ro-loop', 'rootfs_file':APP.ROOT_FILE, 'rootfs_image_bytes':APP.ROOT_BYTES,
            'uvd_runtime':True, 'required_boot_arguments':APP.GATES, 'build_status':'frozen',
            'frontend_overrides':{'fixed_fixture':True}, 'hardware_acceptance':False,
            'maximum_boot_set_bytes':APP.CAP, 'vram_mib':1024}
        self.old_meta = dict(copy.deepcopy(base), profile='ps4-baikal-diagnostics-uvd-alpha2', image_version='alpha2',
            rootfs_sha256=sha(root), initramfs_sha256=sha(self.old['initramfs.cpio.gz']))
        self.meta = dict(copy.deepcopy(base), profile='ps4-baikal-diagnostics-uvd-alpha3', image_version='alpha3',
            rootfs_sha256=sha(new_root), initramfs_sha256=sha(self.new['initramfs.cpio.gz']))
        self.publish_sources()
        self.bootargs = (b'psx.diag_uuid=1234-ABCD time=1600000000 loglevel=7 ignore_loglevel '
            b'pci=use_crs libata.force=1.00:disable psx_mts_net.autoarm=1 '+
            b' '.join(value.encode() for value in APP.GATES)+b'\n')
        remote_old = {name:data for name,data in self.old.items() if name != 'bootargs.template.txt'}
        remote_old['bootargs.txt'] = self.bootargs
        self.remote_old = remote_old
        self.previous = {'owner':'psx-diagnostics-usb-v1', 'verified_readback':True, 'hardware_acceptance':False,
            'profile':self.old_meta['profile'], 'kernel_release':APP.RELEASE,
            'target':{'serial':SERIAL,'bytes':CAPACITY,'uuid':UUID},
            'files':{name:sha(data) for name,data in remote_old.items()}}
        self.previous_raw = (json.dumps(self.previous)+'\n').encode()
        self.sums = APP.DEPLOY.STAGE.installed_sums(self.previous['files']).encode()
        self.mutable = {USB+'/psx-diagnostics/state/ssh/keys':b'private fixture key state',
            USB+'/psx-diagnostics/results/run/result.json':b'operator result',
            USB+'/psx-diagnostics/providers/uvd-alpha2/existing.ext4':b'existing optional pack'}
        self.ftp = TransportFTP({USB+'/'+name:data for name,data in remote_old.items()} | self.mutable | {
            USB+'/psx-diagnostics/installed.json':self.previous_raw,
            USB+'/psx-diagnostics/USB-SHA256SUMS':self.sums})
        self.ftp.journal_path = self.backup/'journal.jsonl'

    def publish_sources(self):
        for root, files, metadata in ((self.old_source,self.old,self.old_meta),(self.source,self.new,self.meta)):
            files[APP.METADATA] = (json.dumps(metadata)+'\n').encode()
            for name,data in files.items():
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            (root/'SHA256SUMS').write_text(APP.DEPLOY.STAGE.installed_sums({name:sha(data) for name,data in files.items()}))

    def args(self, apply=True, full=False):
        return SimpleNamespace(source=self.source, previous_source=self.old_source, host='fixture.invalid',port=2121,
            remote_root=USB,expect_serial=SERIAL,expect_bytes=CAPACITY,expect_uuid=UUID,
            backup=self.backup,receipt=self.receipt,apply=apply,full_upload=full)

    def run_update(self, apply=True, full=False):
        with mock.patch.object(APP.ftplib, 'FTP', return_value=self.ftp), \
             mock.patch.object(APP.uuid, 'uuid4', return_value=SimpleNamespace(hex='alphafixture')), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return APP.update(self.args(apply, full))

    def assert_preserved(self):
        for path,data in self.mutable.items():
            self.assertEqual(self.ftp.files[path], data)
        self.assertEqual(self.ftp.files[USB+'/bootargs.txt'], self.bootargs)
        for name,data in self.remote_old.items():
            if name not in APP.ALLOWED:
                self.assertEqual(self.ftp.files[USB+'/'+name], data)
                if name != 'bootargs.txt':
                    self.assertNotIn(USB+'/'+name, self.ftp.reads)

    def assert_rolled_back(self):
        for name,data in self.remote_old.items():
            self.assertEqual(self.ftp.files[USB+'/'+name], data, name)
        self.assertEqual(self.ftp.files[USB+'/psx-diagnostics/installed.json'], self.previous_raw)
        self.assertEqual(self.ftp.files[USB+'/psx-diagnostics/USB-SHA256SUMS'], self.sums)
        self.assertFalse(self.receipt.exists())
        self.assert_preserved()

    def test_default_plan_never_mutates_or_reads_the_large_root(self):
        before = dict(self.ftp.files)
        plan = self.run_update(apply=False)
        self.assertFalse(plan['apply'])
        self.assertGreater(plan['root_delta_ranges'], 1)
        self.assertEqual(self.ftp.files, before)
        self.assertEqual(self.ftp.mutations, [])
        self.assertNotIn(ROOT_PATH, self.ftp.reads)
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.receipt.exists())

    def test_delta_is_verified_once_with_exact_prefix_tail_length_and_preserved_arguments(self):
        result = self.run_update()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(self.ftp.files[ROOT_PATH], self.new[APP.ROOT_FILE])
        self.assertEqual(self.ftp.reads.count(ROOT_PATH), 1)
        self.assertTrue(any(offset > 0 for offset in self.ftp.root_attempts))
        self.assertIn(0, self.ftp.root_attempts)
        self.assertTrue(all(state is False for state in self.ftp.root_marker_states))
        self.assertEqual((self.backup/APP.ROOT_FILE).read_bytes(), self.old[APP.ROOT_FILE])
        self.assertEqual((self.backup/'bootargs.txt').read_bytes(), self.bootargs)
        marker = json.loads(self.ftp.files[USB+'/psx-diagnostics/installed.json'])
        self.assertTrue(marker['verified_readback'])
        self.assertEqual(marker['profile'], self.meta['profile'])
        self.assertEqual(marker['files'][APP.ROOT_FILE], self.meta['rootfs_sha256'])
        self.assertEqual(marker['files']['bootargs.txt'], sha(self.bootargs))
        self.assert_preserved()

    def test_destructive_ignored_unsupported_and_zero_only_truncating_rest_stop_before_root_write(self):
        initial = dict(self.ftp.files)
        for mode in ('truncate','ignore','unsupported','zero-truncates'):
            self.ftp = TransportFTP(initial)
            self.ftp.rest_mode = mode
            self.backup = self.root/('backup-'+mode)
            self.ftp.journal_path = self.backup/'journal.jsonl'
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'actual boot payload untouched.*--full-upload'):
                self.run_update()
            self.assertEqual(self.ftp.files[ROOT_PATH], self.old[APP.ROOT_FILE])
            self.assertEqual(self.ftp.files[USB+'/psx-diagnostics/installed.json'],self.previous_raw)
            self.assertEqual(self.ftp.root_attempts, [])
            self.assertNotIn(ROOT_PATH, self.ftp.reads)
            self.assertFalse(self.receipt.exists())

    def test_partial_root_write_rolls_back_the_failed_range_as_well_as_completed_ranges(self):
        ranges = APP.changed_ranges(self.old_source/APP.ROOT_FILE, self.source/APP.ROOT_FILE)
        self.ftp.fail_root_offset = ranges[1][0]
        with self.assertRaisesRegex(OSError, 'partial root'):
            self.run_update()
        events = [json.loads(line) for line in (self.backup/'journal.jsonl').read_text().splitlines()]
        attempted = [event['offset'] for event in events if event['event'] == 'root_range_attempt']
        restored = [event['offset'] for event in events if event['event'] == 'rollback_range_attempt']
        self.assertEqual(restored, list(reversed(attempted)))
        self.assertEqual(len(attempted),2)
        self.assertIn(self.ftp.fail_root_offset, restored)
        self.assertEqual(self.ftp.reads.count(ROOT_PATH),1)
        self.assert_rolled_back()

    def test_unrestorable_root_leaves_false_receipt_and_explicit_do_not_boot(self):
        ranges = APP.changed_ranges(self.old_source/APP.ROOT_FILE, self.source/APP.ROOT_FILE)
        self.ftp.fail_root_offset = ranges[1][0]
        self.ftp.fail_restore = True
        with self.assertRaisesRegex(RuntimeError, 'DO NOT BOOT'):
            self.run_update()
        marker = json.loads(self.ftp.files[USB+'/psx-diagnostics/installed.json'])
        self.assertIs(marker['verified_readback'],False)
        self.assertTrue(marker['recovery_required'])
        self.assertFalse(self.receipt.exists())
        self.assertEqual((self.backup/APP.ROOT_FILE).read_bytes(),self.old[APP.ROOT_FILE])

    def test_post_root_header_or_receipt_failure_restores_full_old_identity(self):
        self.ftp.fail_store = USB+'/psx-diagnostics/USB-SHA256SUMS'
        with self.assertRaises(OSError):
            self.run_update()
        self.assertEqual(self.ftp.reads.count(ROOT_PATH),2)
        self.assert_rolled_back()

    def test_full_upload_fallback_needs_no_rest_and_keeps_old_root_until_commit(self):
        self.ftp.rest_mode = 'unsupported'
        result = self.run_update(full=True)
        self.assertEqual(result['mode'],'full-upload')
        self.assertEqual(self.ftp.files[ROOT_PATH],self.new[APP.ROOT_FILE])
        root_reads = [path for path in self.ftp.reads if path.endswith('/'+APP.ROOT_FILE)]
        self.assertEqual(root_reads,[USB+'/.psx-alpha3-alphafixture/new/'+APP.ROOT_FILE])
        self.assertFalse(any(operation == 'rest_store' for operation,*_ in self.ftp.mutations))
        marker_stores = [index for index,item in enumerate(self.ftp.mutations)
                         if item == ('store',USB+'/psx-diagnostics/installed.json')]
        old_delete = self.ftp.mutations.index(('delete',USB+'/.psx-alpha3-alphafixture/old/'+APP.ROOT_FILE))
        self.assertGreater(old_delete,marker_stores[-1])
        self.assert_preserved()

    def test_full_upload_lost_rename_ack_restores_old_root_without_retrieving_it_twice(self):
        self.ftp.fail_rename = (ROOT_PATH,'after')
        with self.assertRaises(OSError):
            self.run_update(full=True)
        self.assertEqual(self.ftp.reads.count(ROOT_PATH),1)
        self.assert_rolled_back()

    def test_corrupt_full_staging_never_invalidates_or_touches_the_active_payload(self):
        self.ftp.corrupt_full_stage = True
        with self.assertRaises(ValueError):
            self.run_update(full=True)
        self.assertEqual(self.ftp.files[ROOT_PATH],self.old[APP.ROOT_FILE])
        self.assertEqual(self.ftp.files[USB+'/psx-diagnostics/installed.json'],self.previous_raw)
        self.assertFalse(self.receipt.exists())

    def test_source_kernel_config_modules_names_and_root_size_drift_fail_before_ftp(self):
        original = copy.deepcopy(self.new)
        for name in ('bzImage','psx-diagnostics/config','psx-diagnostics/modules-'+APP.RELEASE+'.tar.zst',
                     'psx-diagnostics/manifest.txt'):
            self.new = copy.deepcopy(original)
            self.new[name] = b'changed immutable payload'
            self.publish_sources()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.run_update()
            self.assertEqual(self.ftp.mutations,[])
            self.assertFalse(self.backup.exists())
        self.new = copy.deepcopy(original)
        self.new[APP.ROOT_FILE] += b'extra'
        self.meta['rootfs_sha256'] = sha(self.new[APP.ROOT_FILE])
        self.publish_sources()
        with self.assertRaisesRegex(ValueError, '1000 MiB'):
            self.run_update()
        self.assertEqual(self.ftp.mutations,[])

    def test_prepared_root_requires_full_mode_and_an_exact_selected_usb_path(self):
        invalid = ['/mnt/usb1/.psx-alpha3-preload-'+'a'*32+'/rootfs.ext4',
            USB+'/psx-diagnostics/rootfs.ext4', USB+'/.psx-alpha3-preload-short/rootfs.ext4',
            USB+'/.psx-alpha3-preload-'+'a'*32+'/../rootfs.ext4',
            USB+'/.psx-alpha3-preload-'+'a'*32+'/rootfs.ext4\n']
        for path in invalid:
            args = self.args(full=True); args.prepared_root = path
            with mock.patch.object(APP.ftplib,'FTP',side_effect=AssertionError('invalid path must precede network')):
                with self.assertRaises(ValueError):
                    APP.update(args)
        args = self.args(full=False)
        args.prepared_root = USB+'/.psx-alpha3-preload-'+'a'*32+'/rootfs.ext4'
        with self.assertRaisesRegex(ValueError,'only legal'):
            APP.update(args)
        self.assertEqual(self.ftp.mutations,[])

    def test_prepared_root_is_read_back_once_before_any_active_payload_move_without_reupload(self):
        path = USB+'/.psx-alpha3-preload-'+'a'*32+'/rootfs.ext4'
        self.ftp.files[path] = self.new[APP.ROOT_FILE]
        self.ftp.directories.add(str(Path(path).parent))
        args = self.args(full=True); args.prepared_root = path
        with mock.patch.object(APP.ftplib,'FTP',return_value=self.ftp), \
             mock.patch.object(APP.uuid,'uuid4',return_value=SimpleNamespace(hex='alphafixture')), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = APP.update(args)
        self.assertEqual(result['prepared_root'],path)
        self.assertEqual(self.ftp.reads.count(path),1)
        self.assertTrue(all(path in reads for reads in self.ftp.read_before_root_moves))
        self.assertFalse(any(operation == 'store' and paths[0].endswith('rootfs.ext4')
                             for operation,*paths in self.ftp.mutations))
        self.assertEqual(self.ftp.files[ROOT_PATH],self.new[APP.ROOT_FILE])
        self.assertNotIn(ROOT_PATH,self.ftp.reads)
        self.assert_preserved()

    def test_wrong_prepared_bytes_leave_the_installed_root_and_receipt_untouched(self):
        path = USB+'/.psx-alpha3-preload-'+'b'*32+'/rootfs.ext4'
        self.ftp.files[path] = b'wrong bytes'
        self.ftp.directories.add(str(Path(path).parent))
        args = self.args(full=True); args.prepared_root = path
        with mock.patch.object(APP.ftplib,'FTP',return_value=self.ftp), \
             mock.patch.object(APP.uuid,'uuid4',return_value=SimpleNamespace(hex='alphafixture')), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError,'Full root size/SHA256'):
                APP.update(args)
        self.assertEqual(self.ftp.files[ROOT_PATH],self.old[APP.ROOT_FILE])
        self.assertEqual(self.ftp.files[USB+'/psx-diagnostics/installed.json'],self.previous_raw)
        self.assertFalse(self.ftp.read_before_root_moves)
        self.assertFalse(self.receipt.exists())

    def test_kernel_receipt_metadata_and_a_sixth_file_are_not_permitted_corrections(self):
        original = copy.deepcopy(self.meta)
        for changes in ({'kernel':{'invented':'different kernel receipt'}},
                        {'alpha_inputs':{'legacy_package':{'bzImage':'different pin'}}}):
            self.meta = dict(copy.deepcopy(original),**changes)
            self.publish_sources()
            with mock.patch.object(APP.ftplib,'FTP',side_effect=AssertionError('must refuse before FTP')):
                with self.assertRaises(ValueError):
                    self.run_update()
            self.assertFalse(self.backup.exists())
        self.meta = original
        self.new['psx-diagnostics/manifest.txt'] = b'forbidden sixth correction'
        self.publish_sources()
        with self.assertRaises(ValueError):
            self.run_update()
        self.assertEqual(self.ftp.mutations,[])

    def test_partial_incomplete_marker_is_restored_without_reading_or_writing_root(self):
        self.ftp.fail_store = USB+'/psx-diagnostics/installed.json'
        with self.assertRaises(OSError):
            self.run_update()
        self.assertFalse(self.ftp.root_attempts)
        self.assertNotIn(ROOT_PATH,self.ftp.reads)
        self.assert_rolled_back()

    def test_process_death_leaves_incomplete_receipt_and_durable_attempt_without_success(self):
        self.ftp.abort_on_root = True
        with self.assertRaisesRegex(SystemExit,'process death'):
            self.run_update()
        marker = json.loads(self.ftp.files[USB+'/psx-diagnostics/installed.json'])
        self.assertIs(marker['verified_readback'],False)
        self.assertTrue(marker['recovery_required'])
        events = [json.loads(line) for line in (self.backup/'journal.jsonl').read_text().splitlines()]
        self.assertTrue(any(event['event']=='root_range_attempt' for event in events))
        self.assertFalse(any(event['event']=='alpha3_committed' for event in events))
        self.assertEqual((self.backup/APP.ROOT_FILE).read_bytes(),self.old[APP.ROOT_FILE])
        self.assertFalse(self.receipt.exists())

    def test_wrong_usb_unowned_file_pin_or_remote_metadata_refused_before_writes(self):
        original = dict(self.ftp.files)
        cases = []
        wrong = copy.deepcopy(self.previous); wrong['target']['serial'] = 'other'
        cases.append({USB+'/psx-diagnostics/installed.json':json.dumps(wrong).encode()})
        wrong = copy.deepcopy(self.previous); wrong['files']['bzImage'] = '12'*32
        cases.append({USB+'/psx-diagnostics/installed.json':json.dumps(wrong).encode(),
            USB+'/psx-diagnostics/USB-SHA256SUMS':APP.DEPLOY.STAGE.installed_sums(wrong['files']).encode()})
        cases.append({USB+'/'+APP.METADATA:b'{}'})
        for changes in cases:
            self.ftp.files = dict(original)|changes
            with self.assertRaises(ValueError):
                self.run_update()
            self.assertEqual(self.ftp.mutations,[])
            self.assertFalse(self.backup.exists())


if __name__ == '__main__':
    unittest.main()

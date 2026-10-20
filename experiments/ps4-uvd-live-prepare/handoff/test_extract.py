#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic saved-file integration; no device, UART, SSH or kernel build."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


e = module('tested_prepare_extractor', HERE / 'extract.py')
hf = module('synthetic_prepare_stage_fixture', HERE / 'test_handoff.py')
EVIDENCE_DIRECTORY = None
BOOT_ID = b'f65c99c1-5163-4fef-87cb-a83763f02e55\n'
BDF = '0000:00:01.0'
STATUS_PATH = '/sys/kernel/debug/dri/7/amdgpu_ps4_live_prepare'
NAME_PATH = '/sys/kernel/debug/dri/7/name'
NAME = b'amdgpu dev=0000:00:01.0 unique=0000:00:01.0\n'


def dumped(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else dumped(value))


def command(argv, stdout=''):
    return {'argv': argv, 'exit_code': 0, 'stdout': stdout, 'stderr': ''}


class Fixture:
    def __init__(self, root, stack):
        self.root = root
        stack.enter_context(patch.object(e, 'c', hf.c))
        self.base = hf.Fixture(root, stack)
        contract = hf.c.load_json(self.base.contract)
        contract.update(expected_driver='amdgpu', expected_pci_bdf=BDF, required_pid1_comm='systemd')
        paths = {role: HERE.parent / filename for role, (filename, _) in e.DEPENDENCIES.items()}
        paths['result_binding'] = HERE / 'extract.py'
        contract['tools'] = {role: {'path': str(path), 'sha256': e.sha(path.read_bytes())} for role, path in paths.items()}
        put(self.base.contract, contract)
        self.base.prepare()
        self.stage = self.base.stage
        self.plan = hf.c.load_json(self.stage / 'plan.json')
        p = self.plan
        self.plan_sha = hf.c.file_sha(self.stage / 'plan.json')
        self.review = {
            'status': 'APPROVED_ONE_BOUNDED_TEST', 'experiment': p['experiment'], 'target': hf.c.TARGET,
            'release': hf.c.RELEASE, 'plan_sha256': self.plan_sha, 'tools': p['tools'],
            'bundle_manifest_sha256': hf.c.FROZEN['bundle_manifest'], 'compiled_review_sha256': hf.c.FROZEN['compiled_review'],
            'source_review_sha256': hf.c.FROZEN['source_review'], 'build_receipt_sha256': hf.c.FROZEN['build'],
            'package_receipt_sha256': hf.c.FROZEN['package'], 'compatibility_status_sha256': hf.c.FROZEN['compatibility'],
            'observation_contract_sha256': p['observation_contract_sha256'], 'observation_tools': p['observation_tools'],
            'hardware_acceptance': False, 'full_poweroff_before_launch_is_separate_prerequisite': True,
            'prior_interrupted_reviews_remain_incomplete': True, 'SYNTHETIC_TEST_ONLY': True,
        }
        put(self.stage.parent / 'REVIEWED-HANDOFF.json', self.review)
        self.review_sha = hf.c.file_sha(self.stage.parent / 'REVIEWED-HANDOFF.json')
        self.runtime = self.stage.parent / 'runtime'
        self.runtime.mkdir()
        started = {'experiment': p['experiment'], 'mode': 'install', 'utc': '2099-01-01T00:00:01+00:00',
                   'plan_sha256': self.plan_sha, 'reviewed_handoff_sha256': self.review_sha,
                   'wrapper_sha256': p['tools']['run_test.py'], 'host_usb_identity_revalidated': True,
                   'operator_holds_linux_launch': True, 'host_usb_isolation_and_launch_hold_confirmed': True,
                   'physical_state_evidence': 'Synthetic host isolation only; no devices'}
        ready = {'status': '[+] Status:  READY', 'session': '[+] Session active: ' + p['experiment'] + '\n    Continuity: valid so far (synthetic)'}
        prefix = 'lib/modules/' + p['release'] + '/'
        indexes = sorted(name[len(prefix):] for name in p['module_tree'] if '/' not in name[len(prefix):] and name[len(prefix):].startswith('modules.'))
        installed = {'experiment': p['experiment'], 'mode': 'apply', 'committed': True, 'unmounted': True,
                     'modules_installed': True, 'usb_on_host_launch_held_confirmed': True, 'operator_holds_linux_launch': True,
                     'console_state': 'orbis-idle', 'release': p['release'], 'install_from_release': p['install_release'],
                     'reviewed_handoff_sha256': self.review_sha, 'serial': p['disk_serial'], 'native_restore_automatic': False,
                     'boot_before': p['install_boot_hashes'],
                     'boot_after': {**p['install_boot_hashes'], 'bzImage': p['kernel_sha256'],
                                    'initramfs.cpio.gz': p['initramfs_sha256'], 'bootargs.txt': p['new_bootargs_sha256']},
                     'module_files': 55, 'module_indexes': indexes,
                     'rollback_module_count': sum('.ko' in name for name in p['rollback_module_tree']),
                     'install_source_module_count': sum('.ko' in name for name in p['install_module_tree']),
                     'uart_before': ready, 'uart_after': ready,
                     'partitions': {role: {'path': '/dev/synthetic' + str(number), 'size': p[role + '_bytes'],
                                          'uuid': p[role + '_uuid'], 'fstype': fs, 'mounts': []}
                                    for role, number, fs in (('boot', 1, 'vfat'), ('root', 2, 'ext4'))}}
        complete = {'status': 'PASS_INSTALLED_UNMOUNTED', 'experiment': p['experiment'], 'plan_sha256': self.plan_sha,
                    'utc': '2099-01-01T00:00:02+00:00', 'both_read_only_filesystem_checks_passed_before_and_after': True,
                    'partitions': {role: installed['partitions'][role]['path'] for role in ('boot', 'root')},
                    'session_left_open_for_primary_agent': True}
        start_stdout = ('[+] Session started: ' + p['experiment'] + '\n    Source: /synthetic/uart.log\n'
                        '    Capture generation: ' + 'a' * 32 + '\n    Connection epoch: 1\n'
                        '    Offset: 100 bytes\n    Perform the action, then run: ./uart session stop\n')
        records = {'install-started.json': started, 'install-receipt.json': installed, 'install-complete.json': complete,
                   'session-start.json': command(['/synthetic/ps4-uart/uart', 'session', 'start', p['experiment']], start_stdout),
                   'install-command.json': command(['sudo', '-n', '/usr/bin/python3', '-B', '-I', str(HERE / 'install_usb.py'),
                       '--apply', str(self.stage), '--confirm-serial', p['disk_serial'], '--reviewed-plan-sha256', self.plan_sha,
                       '--reviewed-handoff-sha256', self.review_sha, '--confirm-usb-on-host-launch-held', '--console-state', 'orbis-idle'],
                       dumped(installed).decode())}
        for phase in ('before', 'after'):
            for role in ('boot', 'root'):
                part = installed['partitions'][role]['path']
                argv = ['sudo', '-n', '/usr/bin/fsck.fat', '-n', part] if role == 'boot' else ['sudo', '-n', '/usr/bin/e2fsck', '-f', '-n', part]
                records['install-' + phase + '-' + role + '-fsck.json'] = command(argv, 'Synthetic read-only check\n')
        for name, value in records.items():
            put(self.runtime / name, value)
        self.runtime_original = {path.name: path.read_bytes() for path in self.runtime.iterdir()}
        self.context = e._load_stage(self.stage, self.review_sha, self.plan_sha)
        self.args = self.context['bootargs']
        self.status = (f'schema=1 build={p["release"]} interface=prepare-only arm=absent\n'
                       'stage=PREPARED error=0 selected=1 attempted=1 retained=1\n'
                       'vmid=4 reservation_seen=1 allocator_alive_cached=1 early_fence_init=1\n'
                       'observed_generation=1 proof_generation=1 last_event=GART_ENABLE\n'
                       'historical_sdma_proof=1 proof_ns=8000000000 reset_counter_at_proof=0 copy_crc32=570ae4eb\n'
                       'pd_at_proof=0x100000 pd_bytes=0x20000 pt_bytes=0x2000 pages=812\n'
                       'r1_bytes=0x308000 rb_bytes=0x1000 ib_bytes=0x20000\n'
                       'observer=cached-only current_hardware_mapping=not-sampled UVD_setup=never\n').encode()
        self.map_message = 'amdgpu ' + BDF + ': PS4_MAP_CANARY MAP max_pfn=0x1000000 block=10 pd_bytes=0x20000 pde=0xc00 pt_bytes=0x2000 src_pte=0x140ad9067 dst_pte=0x140ada067 ib_pte=0x142fba067'
        self.prepared_message = 'amdgpu ' + BDF + ': PS4_LIVE_PREPARE PREPARED vmid=4 pages=812 copy_crc32=570ae4eb historical_sdma_proof=1 retained=1 UVD_setup=never arm=absent; continuing normal initialization'
        self.raw = (f'[    0.000000] Linux version {p["release"]} (synthetic@fixture) #1\n'
                    f'[    0.000000] Command line: {self.args.decode().strip()}\n'
                    f'[    0.100000] Kernel command line: {self.args.decode().strip()}\n'
                    f'[    7.000000] {self.map_message}\n[    8.001000] {self.prepared_message}\n'
                    '[    9.000000] Run /init as init process\n'
                    f'[   12.000000] EXT4-fs (different-device42): mounted filesystem {p["root_uuid"]} r/w with ordered data mode. Quota mode: none.\n'
                    '[   20.000000] systemd[1]: Reached target multi-user.target - Multi-User System.\n').encode()
        self.launch = {'schema': 1, 'kind': 'OPERATOR_LAUNCH_REPORT', 'experiment': p['experiment'],
                       'plan_sha256': self.plan_sha, 'recorded_at_utc': '2099-01-01T00:00:03+00:00',
                       'launch_count': 1, 'full_poweroff_since_retained_experiment': True,
                       'retained_experiment': self.context['retained_experiment'], 'reporter': 'operator', 'hardware_acceptance': False}

    def session(self, raw):
        return {'index_record': {'version': 2, 'label': self.plan['experiment'], 'state': 'completed',
                'continuity_errors': [], 'capture_generation': 'a' * 32, 'connection_epoch': 1, 'session_id': 'b' * 32,
                'source': '/synthetic/uart.log', 'start_offset': 100, 'end_offset': 100 + len(raw), 'bytes': len(raw),
                'started_at': '2099-01-01T06:00:00+06:00', 'stopped_at': '2099-01-01T06:01:00+06:00'},
                'artifacts': {'raw': {'sha256': e.sha(raw), 'bytes': len(raw)}}}

    def inner(self, status=None, args=None):
        a = e.ce._acquire
        files = {a.BOOT_ID: [BOOT_ID] * 3, a.UPTIME: [b'30.10 60.00\n', b'30.11 60.01\n'],
                 a.CMDLINE: [self.args if args is None else args], NAME_PATH: [NAME, NAME],
                 STATUS_PATH: [self.status if status is None else status]}
        names = [self.plan['release'].encode()] * 3
        mono, wall = [100, 200, 300, 400], [1000, 2000, 3000, 4000]
        return a.acquire(expected_release=self.plan['release'], expected_pci_bdf=BDF, status_path=STATUS_PATH,
                         reader=lambda path, limit: files[path].pop(0), uname=lambda: names.pop(0),
                         monotonic_ns=lambda: mono.pop(0), wall_time_ns=lambda: wall.pop(0))

    def journal(self):
        # The reviewed grep/lines query reverses journal order; preserve it.
        return b''.join(dumped({'MESSAGE': message, '_BOOT_ID': BOOT_ID.decode().strip().replace('-', ''),
                                '_TRANSPORT': 'kernel'}).replace(b'\n  ', b'  ').replace(b'\n}', b'}')
                        for message in (self.prepared_message, self.map_message))

    def bracket(self, *, inner=None, journal=None, pid1=b'systemd\n', ending_boot=BOOT_ID):
        inner = self.inner() if inner is None else inner
        journal = self.journal() if journal is None else journal
        files = {e.j.BOOT_ID: [BOOT_ID, ending_boot], e.j.UPTIME: [b'30.00 60.00\n', b'30.20 60.20\n'], e.j.PID1_COMM: [pid1]}
        names = [self.plan['release'].encode()] * 2
        mono, wall = [0, 50, 500, 600, 700, 800], [0, 50, 5000, 6000, 7000, 8000]
        return e.j.collect(expected_release=self.plan['release'], expected_pci_bdf=BDF, status_path=STATUS_PATH,
                           producer=lambda: inner, reader=lambda path, limit: files[path].pop(0), uname=lambda: names.pop(0),
                           monotonic_ns=lambda: mono.pop(0), wall_time_ns=lambda: wall.pop(0),
                           journal_runner=lambda argv, **kwargs: {'stdout': journal, 'stderr': b'', 'stdout_eof': True, 'stderr_eof': True,
                               'returncode': 0, 'spawned': True, 'timed_out': False, 'overflow_stream': None,
                               'signals_sent_to_own_child': [], 'errors': []}, include_pid1_comm=True,
                           source_pins={'acquire_sha256': self.plan['observation_tools']['acquisition'],
                                        'wrapper_sha256': self.plan['observation_tools']['journal_collector']})

    def transport(self, raw):
        p = self.plan
        return {'schema': 1, 'kind': 'SSH_PREPARATION_CAPTURE', 'experiment': p['experiment'],
                'plan_sha256': self.plan_sha, 'reviewed_handoff_sha256': self.review_sha,
                'observation_contract_sha256': p['observation_contract_sha256'], 'observation_tools': p['observation_tools'],
                'transport': 'ssh', 'ssh_target': 'synthetic-host',
                'remote_argv': e.remote_argv(p['release'], BDF, STATUS_PATH),
                'remote_program_sha256': e.sha(e.j.render_remote_program(self.context['sources']['acquisition'], self.context['sources']['journal_collector'])),
                'acquisition_source_sha256': p['observation_tools']['acquisition'], 'journal_source_sha256': p['observation_tools']['journal_collector'],
                'stdout_sha256': e.sha(raw), 'stdout_bytes': len(raw), 'stderr_sha256': e.sha(b''), 'stderr_bytes': 0,
                'returncode': 0, 'started_at_utc': '2099-01-01T00:00:30+00:00', 'ended_at_utc': '2099-01-01T00:00:31+00:00',
                'started_monotonic_ns': 10000, 'ended_monotonic_ns': 20000, 'expected_release': p['release'],
                'expected_pci_bdf': BDF, 'status_path': STATUS_PATH, 'hardware_acceptance': False}

    def analyze(self, *, context=None, raw=None, session=None, launch=None, transport=None, bracket=None):
        raw = self.raw if raw is None else raw
        bracket_raw = dumped(self.bracket() if bracket is None else bracket)
        return e._analyze(copy.deepcopy(self.context) if context is None else context, uart_raw=raw,
                          session=self.session(raw) if session is None else session,
                          launch=copy.deepcopy(self.launch) if launch is None else launch,
                          transport=self.transport(bracket_raw) if transport is None else transport,
                          bracket_raw=bracket_raw, transport_stderr=b'')


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = contextlib.ExitStack()
        cls.temp = cls.stack.enter_context(tempfile.TemporaryDirectory(prefix='synthetic-', dir=EVIDENCE_DIRECTORY))
        cls.f = Fixture(Path(cls.temp).resolve(), cls.stack)

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def no_positive(self, result):
        self.assertFalse(result['preparation_userspace_observed'], result)
        self.assertIs(result['hardware_acceptance'], False)
        self.assertIs(result['decode_demonstrated'], False)
        self.assertEqual(result['accepted'], 0)
        return result

    def test_positive_binds_all_saved_evidence_without_acceptance(self):
        result = self.f.analyze()
        self.assertEqual(result['status'], 'PREPARATION_USERSPACE_OBSERVED', result)
        self.assertTrue(result['preparation_userspace_observed'])
        self.assertFalse(result['hardware_acceptance']); self.assertFalse(result['decode_demonstrated']); self.assertEqual(result['accepted'], 0)
        self.assertTrue(result['outer_replay_matched'])
        self.assertEqual(result['pid1_comm'], 'systemd')
        self.assertFalse(result['journal_correspondence']['uart_order_equality_required'])
        self.assertEqual(result['journal_correspondence']['matches']['prepared']['journal_line'], 1)
        self.assertFalse(result['module_evidence']['post_depmod_index_hashes_attested'])

    def test_completed_continuity_and_byte_fences_required(self):
        for key, value in (('state', 'invalid'), ('version', 2.0), ('continuity_errors', ['reconnect']), ('end_offset', 1),
                           ('capture_generation', 'c' * 32), ('connection_epoch', 2), ('start_offset', 101), ('label', 'EXP-20990101-997')):
            with self.subTest(key=key):
                session = self.f.session(self.f.raw); session['index_record'][key] = value
                result = self.no_positive(self.f.analyze(session=session))
                self.assertEqual(result['status'], 'INVALID_CONTINUITY')

    def test_original_start_stdout_not_caller_label_is_checked(self):
        for old, new in (('Capture generation: ' + 'a' * 32, 'Capture generation: ' + 'c' * 32),
                         ('Offset: 100 bytes', 'Offset: 99 bytes'), ('Connection epoch: 1', 'Connection epoch: 2')):
            context = copy.deepcopy(self.f.context)
            row = context['runtime']['rows']['session-start.json']; row['stdout'] = row['stdout'].replace(old, new)
            result = self.no_positive(self.f.analyze(context=context))
            self.assertEqual(result['status'], 'INVALID_CONTINUITY')

    def test_fault_anywhere_in_full_raw_wins(self):
        raw = self.f.raw + b'irrelevant prose\n' * 5000 + b'[   40.000000] Kernel panic - synthetic\n'
        result = self.no_positive(self.f.analyze(raw=raw))
        self.assertEqual(result['status'], 'PREPARATION_PARKED_OR_FAILED')

    def test_missing_raw_line_cannot_be_replaced_by_journal(self):
        raw = b''.join(line for line in self.f.raw.splitlines(keepends=True) if b'PS4_MAP_CANARY MAP' not in line)
        result = self.no_positive(self.f.analyze(raw=raw))
        self.assertEqual(result['status'], 'UART_OBSERVATION_INCOMPLETE')

    def test_wrong_uart_release_or_bdf_is_distinct(self):
        for old, new in ((self.f.plan['release'].encode(), b'wrong-release'), (BDF.encode(), b'0000:00:02.0')):
            result = self.no_positive(self.f.analyze(raw=self.f.raw.replace(old, new)))
            self.assertEqual(result['status'], 'BOOT_IDENTITY_MISMATCH')

    def test_prepared_alone_does_not_establish_userspace(self):
        raw = b''.join(line for line in self.f.raw.splitlines(keepends=True) if b'Run /init' not in line and b'EXT4-fs' not in line and b'systemd[1]' not in line)
        result = self.no_positive(self.f.analyze(raw=raw))
        self.assertEqual(result['status'], 'NO_USERSPACE_MILESTONE_WITHIN_180_SECONDS')

    def test_each_userspace_prerequisite_is_positive(self):
        for marker in (b'Run /init', b'EXT4-fs', b'systemd[1]'):
            raw = b''.join(line for line in self.f.raw.splitlines(keepends=True) if marker not in line)
            self.assertEqual(self.no_positive(self.f.analyze(raw=raw))['status'], 'NO_USERSPACE_MILESTONE_WITHIN_180_SECONDS')

    def test_observed_description_only_targets_with_crlf(self):
        # Exact target wording/times observed in the retained rootfs's EXP-20261018-005.
        # The surrounding preparation records remain synthetic.
        prefix = b''.join(line for line in self.f.raw.splitlines(keepends=True)
                          if b'Run /init' not in line and b'EXT4-fs' not in line and b'systemd[1]' not in line)
        milestones = (
            b'[   11.004054] Run /init as init process\r\n'
            b'[   11.623628] EXT4-fs (sda2): mounted filesystem '
            + self.f.plan['root_uuid'].encode()
            + b' r/w with ordered data mode. Quota mode: disabled.\r\n')
        for target in (b'[   17.899400] systemd[1]: Reached target Multi-User System.\r\n',
                       b'[   18.284887] systemd[1]: Reached target Graphical Interface.\r\n'):
            with self.subTest(target=target):
                self.assertTrue(self.f.analyze(raw=prefix + milestones + target)['preparation_userspace_observed'])

    def test_other_description_only_targets_do_not_qualify(self):
        for description in (b'Basic System', b'Initrd Default Target', b'Multi-User System Extra', b'Graphical Interface Extra'):
            raw = self.f.raw.replace(b'multi-user.target - Multi-User System', description)
            self.assertEqual(self.no_positive(self.f.analyze(raw=raw))['status'], 'NO_USERSPACE_MILESTONE_WITHIN_180_SECONDS')

    def test_userspace_requires_init_then_mount_then_target(self):
        lines = self.f.raw.splitlines(keepends=True)
        init_index = next(i for i, line in enumerate(lines) if b'Run /init' in line)
        mount_index = next(i for i, line in enumerate(lines) if b'EXT4-fs' in line)
        lines[init_index], lines[mount_index] = lines[mount_index], lines[init_index]
        bad_order = b''.join(lines)
        bad_clock = self.f.raw.replace(b'[   12.000000]', b'[    8.500000]')
        for raw in (bad_order, bad_clock):
            self.assertEqual(self.no_positive(self.f.analyze(raw=raw))['status'], 'NO_USERSPACE_MILESTONE_WITHIN_180_SECONDS')

    def test_root_uuid_from_plan_not_device_spelling(self):
        good = self.f.raw.replace(b'different-device42', b'nvme99n1p8')
        self.assertTrue(self.f.analyze(raw=good)['preparation_userspace_observed'])
        bad = self.f.raw.replace(self.f.plan['root_uuid'].encode(), b'00000000-0000-0000-0000-000000000000')
        self.no_positive(self.f.analyze(raw=bad))

    def test_systemd_mount_wording_is_not_a_gate(self):
        raw = self.f.raw + b'[   15.000000] systemd[1]: A distribution-specific debugfs mount description\n'
        self.assertTrue(self.f.analyze(raw=raw)['preparation_userspace_observed'])

    def test_normal_target_deadline_is_kernel_progress_only(self):
        exact = self.f.raw.replace(b'[   20.000000]', b'[  180.000000]')
        late = self.f.raw.replace(b'[   20.000000]', b'[  180.000001]')
        self.assertTrue(self.f.analyze(raw=exact)['preparation_userspace_observed'])
        self.assertEqual(self.no_positive(self.f.analyze(raw=late))['status'], 'NO_USERSPACE_MILESTONE_WITHIN_180_SECONDS')
        initrd = self.f.raw.replace(b'multi-user.target - Multi-User System.', b'initrd.target - Initrd Default Target.')
        self.no_positive(self.f.analyze(raw=initrd))

    def test_operator_report_exact_schema_one_launch_and_poweroff(self):
        for key, value in (('launch_count', 2), ('launch_count', True), ('full_poweroff_since_retained_experiment', False),
                           ('reporter', 'agent agreement'), ('retained_experiment', 'EXP-20990101-997'),
                           ('plan_sha256', '0' * 64), ('hardware_acceptance', True)):
            launch = copy.deepcopy(self.f.launch); launch[key] = value
            self.assertEqual(self.no_positive(self.f.analyze(launch=launch))['status'], 'HOST_RECORD_BINDING_INVALID')
        launch = copy.deepcopy(self.f.launch); launch['extra'] = True
        self.no_positive(self.f.analyze(launch=launch))

    def test_recorded_launch_and_host_interval_aware_utc_order(self):
        for instant in ('2099-01-01T00:00:01+00:00', '2099-01-01T00:00:32+00:00', '2099-01-01T00:00:03', '2099-01-01T06:00:03+06:00'):
            launch = copy.deepcopy(self.f.launch); launch['recorded_at_utc'] = instant
            self.assertEqual(self.no_positive(self.f.analyze(launch=launch))['status'], 'HOST_RECORD_BINDING_INVALID')

    def test_capture_must_end_inside_completed_session(self):
        transport = self.f.transport(dumped(self.f.bracket())); transport['ended_at_utc'] = '2099-01-01T00:01:01+00:00'
        self.assertEqual(self.no_positive(self.f.analyze(transport=transport))['status'], 'HOST_RECORD_BINDING_INVALID')

    def test_transport_source_stream_and_plan_pins(self):
        fields = ('plan_sha256', 'reviewed_handoff_sha256', 'observation_contract_sha256', 'remote_program_sha256',
                  'acquisition_source_sha256', 'journal_source_sha256', 'stdout_sha256', 'stderr_sha256', 'expected_pci_bdf', 'expected_release')
        for key in fields:
            with self.subTest(key=key):
                transport = self.f.transport(dumped(self.f.bracket())); transport[key] = '0' * 64
                self.assertEqual(self.no_positive(self.f.analyze(transport=transport))['status'], 'HOST_RECORD_BINDING_INVALID')
        transport = self.f.transport(dumped(self.f.bracket())); transport['stdout_bytes'] = True
        self.no_positive(self.f.analyze(transport=transport))

    def test_remote_argv_requires_pid1_and_exact_renderer(self):
        transport = self.f.transport(dumped(self.f.bracket())); transport['remote_argv'].remove('--include-pid1-comm')
        self.no_positive(self.f.analyze(transport=transport))

    def test_failed_ssh_is_incomplete_not_pass(self):
        transport = self.f.transport(dumped(self.f.bracket())); transport['returncode'] = 255
        self.assertEqual(self.no_positive(self.f.analyze(transport=transport))['status'], 'INCOMPLETE_OBSERVATION')

    def test_monotonic_transport_order(self):
        transport = self.f.transport(dumped(self.f.bracket())); transport['started_monotonic_ns'] = 20001
        self.no_positive(self.f.analyze(transport=transport))

    def test_pid1_comm_required_without_assuming_prepared(self):
        result = self.no_positive(self.f.analyze(bracket=self.f.bracket(pid1=b'init\n')))
        self.assertEqual(result['status'], 'INCOMPLETE_OBSERVATION')

    def test_outer_identity_drift_and_lied_flags(self):
        bracket = self.f.bracket(ending_boot=b'ce1735b4-43b4-45ba-b8ad-71585eb55554\n')
        bracket.update(complete=True, identity_consistent=True, issues=[], status='CAPTURED_BOOT_CORRELATION_INPUTS')
        self.no_positive(self.f.analyze(bracket=bracket))

    def test_outer_digest_parse_order_and_reference_lies(self):
        for change in ('digest', 'parse', 'order', 'source'):
            bracket = self.f.bracket()
            if change == 'digest': bracket['observations']['boot_id_before']['raw']['sha256'] = '0' * 64
            elif change == 'parse': bracket['observations']['boot_id_after']['parsed'] = 'false parsed ID'
            elif change == 'order': bracket['events'].reverse()
            else: bracket['source_pins']['wrapper_sha256'] = '0' * 64
            self.assertEqual(self.no_positive(self.f.analyze(bracket=bracket))['status'], 'INCOMPLETE_OBSERVATION')

    def test_inner_acquisition_flags_are_replayed(self):
        bracket = self.f.bracket(); bracket['acquisition']['complete'] = 1
        result = self.no_positive(self.f.analyze(bracket=bracket))
        self.assertEqual(result['status'], 'INCOMPLETE_OBSERVATION')

    def test_inner_time_and_uptime_must_fit_outer_bracket(self):
        bracket = self.f.bracket(); bracket['timestamps']['before_acquisition']['monotonic_ns'] = 150
        self.no_positive(self.f.analyze(bracket=bracket))

    def test_changed_complete_boot_policy_is_wrong_identity(self):
        args = self.f.args.replace(b'debugfs=on', b'debugfs=off')
        result = self.no_positive(self.f.analyze(bracket=self.f.bracket(inner=self.f.inner(args=args))))
        self.assertEqual(result['status'], 'BOOT_IDENTITY_MISMATCH')

    def test_coherent_invalidation_and_contradiction_stay_distinct(self):
        invalid = self.f.status.replace(b'stage=PREPARED error=0', b'stage=INVALIDATED error=-116').replace(
            b'observed_generation=1', b'observed_generation=2').replace(b'last_event=GART_ENABLE', b'last_event=RESET')
        result = self.no_positive(self.f.analyze(bracket=self.f.bracket(inner=self.f.inner(status=invalid))))
        self.assertEqual(result['status'], 'PREPARATION_HISTORY_INVALIDATED')
        contradiction = self.f.status.replace(b'observed_generation=1', b'observed_generation=2')
        result = self.no_positive(self.f.analyze(bracket=self.f.bracket(inner=self.f.inner(status=contradiction))))
        self.assertEqual(result['status'], 'PREPARATION_STATE_CONTRADICTION')

    def test_uart_lifecycle_invalidation_is_not_ignored(self):
        raw = self.f.raw + b'[   29.000000] amdgpu 0000:00:01.0: GPU reset begin!.\n'
        self.assertEqual(self.no_positive(self.f.analyze(raw=raw))['status'], 'UART_PREPARATION_HISTORY_INVALIDATED')

    def test_journal_duplicate_keys_arrays_null_and_wrong_identity(self):
        valid = self.f.journal()
        mutations = (valid.replace(b'"_TRANSPORT": "kernel"', b'"_TRANSPORT": "kernel", "_TRANSPORT": "kernel"', 1),
                     valid.replace(b'"_TRANSPORT": "kernel"', b'"_TRANSPORT": ["kernel"]', 1),
                     valid.replace(b'"_TRANSPORT": "kernel"', b'"_TRANSPORT": null', 1),
                     valid.replace(b'"_TRANSPORT": "kernel"', b'"_TRANSPORT": "stdout"', 1),
                     valid.replace(BOOT_ID.decode().strip().replace('-', '').encode(), b'c' * 32))
        for index, value in enumerate(mutations):
            self.assertEqual(self.no_positive(self.f.analyze(bracket=self.f.bracket(journal=value)))['status'],
                             'INCOMPLETE_OBSERVATION' if index < 3 else 'BOOT_IDENTITY_MISMATCH')

    def test_journal_reaching64_is_potentially_truncated(self):
        extra = json.dumps({'MESSAGE': 'PS4_MAP_CANARY BEGIN synthetic', '_BOOT_ID': BOOT_ID.decode().strip().replace('-', ''), '_TRANSPORT': 'kernel'}).encode() + b'\n'
        for count in (64, 65):
            value = self.f.journal() + extra * (count - 2)
            self.no_positive(self.f.analyze(bracket=self.f.bracket(journal=value)))

    def test_json_numeric_overflow_cannot_reach_result_serialization(self):
        value = self.f.journal().replace(b'"_TRANSPORT": "kernel"', b'"_TRANSPORT": "kernel", "ignored": 1e309', 1)
        self.assertEqual(self.no_positive(self.f.analyze(bracket=self.f.bracket(journal=value)))['status'], 'INCOMPLETE_OBSERVATION')

    def test_journal_exact_uart_message_not_only_crc_or_ptes(self):
        value = self.f.journal().replace(b'src_pte=0x140ad9067', b'src_pte=0x140ad8067')
        self.no_positive(self.f.analyze(bracket=self.f.bracket(journal=value)))
        value = self.f.journal() + self.f.journal().splitlines(keepends=True)[0]
        self.no_positive(self.f.analyze(bracket=self.f.bracket(journal=value)))

    def test_journal_and_uart_transport_crlf_only(self):
        result = self.f.analyze(raw=self.f.raw.replace(b'\n', b'\r\n'),
                               bracket=self.f.bracket(journal=self.f.journal().replace(b'\n', b'\r\n')))
        self.assertTrue(result['preparation_userspace_observed'], result)

    def test_journal_signal_or_timeout_flags_cannot_be_hidden(self):
        for key, value in (('timed_out', True), ('signals_sent_to_own_child', ['SIGTERM']), ('returncode', 1), ('query_complete', 1)):
            bracket = self.f.bracket(); bracket['journal'][key] = value
            self.no_positive(self.f.analyze(bracket=bracket))

    def test_runtime_five_file_module_fsck_and_review_bindings(self):
        mutations = (('install-receipt.json', 'module_files', 54), ('install-receipt.json', 'modules_installed', False),
                     ('install-receipt.json', 'committed', False), ('install-receipt.json', 'unmounted', False),
                     ('install-receipt.json', 'boot_after', {}), ('install-receipt.json', 'module_indexes', []),
                     ('install-complete.json', 'both_read_only_filesystem_checks_passed_before_and_after', False),
                     ('install-started.json', 'reviewed_handoff_sha256', '0' * 64),
                     ('install-after-root-fsck.json', 'exit_code', 1))
        for name, key, value in mutations:
            with self.subTest(name=name, key=key):
                path = self.f.runtime / name; original = path.read_bytes()
                try:
                    row = json.loads(original); row[key] = value; put(path, row)
                    with self.assertRaises((e.BindingError, RuntimeError)):
                        e._load_stage(self.f.stage, self.f.review_sha, self.f.plan_sha)
                finally:
                    path.write_bytes(original)

    def test_runtime_console_state_must_be_reviewed_even_when_argv_matches(self):
        receipt_path = self.f.runtime / 'install-receipt.json'
        command_path = self.f.runtime / 'install-command.json'
        originals = {path: path.read_bytes() for path in (receipt_path, command_path)}
        try:
            for state in (*self.f.plan['installation_state']['console'], 'linux-launching', 'unknown', '', None):
                with self.subTest(state=state):
                    receipt = json.loads(originals[receipt_path])
                    command_row = json.loads(originals[command_path])
                    if state is None:
                        receipt.pop('console_state')
                    else:
                        receipt['console_state'] = state
                    command_row['argv'][-1] = state
                    command_row['stdout'] = dumped(receipt).decode()
                    put(receipt_path, receipt); put(command_path, command_row)
                    if state in self.f.plan['installation_state']['console']:
                        e._load_stage(self.f.stage, self.f.review_sha, self.f.plan_sha)
                    else:
                        with self.assertRaisesRegex(e.BindingError, 'console state outside reviewed plan'):
                            e._load_stage(self.f.stage, self.f.review_sha, self.f.plan_sha)
        finally:
            for path, raw in originals.items():
                path.write_bytes(raw)

    def test_public_api_calls_both_stage_validation_gates(self):
        with patch.object(e.c, 'validate_reviewed_handoff', side_effect=e.BindingError('review stop')) as reviewed, \
                patch.object(e.c, 'load_inputs', side_effect=AssertionError('must not load after invalid review')) as load:
            result = e.extract_saved(self.f.stage, reviewed_sha256=self.f.review_sha, plan_sha256=self.f.plan_sha,
                uart_raw_path='unused', session_meta_path='unused', launch_report_path='unused',
                transport_receipt_path='unused', bracket_stdout_path='unused', transport_stderr_path='unused')
            self.assertEqual(reviewed.call_count, 1); self.assertEqual(load.call_count, 0)
            self.no_positive(result)

    def test_stage_mutation_is_not_accepted_from_runtime_labels(self):
        path = self.f.stage / 'bundle/bootargs.new'; original = path.read_bytes()
        try:
            path.write_bytes(original + b'changed')
            with self.assertRaises((RuntimeError, e.BindingError)):
                e._load_stage(self.f.stage, self.f.review_sha, self.f.plan_sha)
        finally:
            path.write_bytes(original)

    def test_all_frozen_dependency_bytes_match(self):
        for role, (filename, digest) in e.DEPENDENCIES.items():
            self.assertEqual(e.sha((HERE.parent / filename).read_bytes()), digest, role)

    def test_saved_file_cli_has_no_live_path_or_overwrite(self):
        for path in ('/proc/cmdline', '/sys/kernel/debug/dri/0/name', '/dev/null'):
            with self.assertRaises((e.BindingError, OSError)):
                e.safe_path(path)
        output = self.f.root / 'exists.json'; put(output, b'owner bytes')
        with self.assertRaises(e.BindingError): e.safe_path(output, new=True)
        alias = self.f.root / 'alias'; alias.symlink_to(output)
        with self.assertRaises(e.BindingError): e.read_saved(alias)
        self.assertEqual(output.read_bytes(), b'owner bytes')

    def test_full_public_saved_file_path(self):
        folder = self.f.root / 'inputs'; folder.mkdir(exist_ok=True)
        bracket = dumped(self.f.bracket())
        values = {'uart': self.f.raw, 'session': self.f.session(self.f.raw), 'launch': self.f.launch,
                  'transport': self.f.transport(bracket), 'bracket': bracket, 'stderr': b''}
        for name, value in values.items(): put(folder / name, value)
        result = e.extract_saved(self.f.stage, reviewed_sha256=self.f.review_sha, plan_sha256=self.f.plan_sha,
            uart_raw_path=folder / 'uart', session_meta_path=folder / 'session', launch_report_path=folder / 'launch',
            transport_receipt_path=folder / 'transport', bracket_stdout_path=folder / 'bracket', transport_stderr_path=folder / 'stderr')
        self.assertTrue(result['preparation_userspace_observed'], result)
        self.assertEqual(result['saved_input_hashes']['uart_raw']['sha256'], e.sha(self.f.raw))


def main():
    global EVIDENCE_DIRECTORY
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-file', type=Path, required=True)
    args = parser.parse_args()
    output = args.report_file
    log_path = output.with_suffix('.log')
    if output.exists() or log_path.exists(): parser.error('Use fresh evidence paths')
    output.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIRECTORY = output.parent.resolve()
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    log = stream.getvalue()
    receipt = {'schema': 1, 'status': 'PASS_SYNTHETIC_SAVED_RESULT_BINDING' if result.wasSuccessful() else 'FAIL_SYNTHETIC_SAVED_RESULT_BINDING',
               'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'platform': sys.platform,
               'binder_sha256': e.sha((HERE / 'extract.py').read_bytes()), 'tests_sha256': e.sha(Path(__file__).read_bytes()),
               'dependency_pins': {role: digest for role, (_, digest) in e.DEPENDENCIES.items()}, 'log_sha256': e.sha(log.encode()),
               'hardware_acceptance': False, 'decode_demonstrated': False, 'accepted': 0, 'actual_acquisition_performed': False,
               'actual_experiment_or_approval_created': False,
               'scope': 'Ephemeral synthetic stage/review/runtime records, real stage validators and offline zstd, injected producer/collector, saved-file binder only'}
    with output.open('x', encoding='utf-8') as stream: json.dump(receipt, stream, indent=2, sort_keys=True); stream.write('\n')
    with log_path.open('x', encoding='utf-8') as stream: stream.write(log)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__': raise SystemExit(main())

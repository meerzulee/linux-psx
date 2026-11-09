# SPDX-License-Identifier: MIT
"""Support-report privacy and filesystem fixtures; no console/network access."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('psx_support_report', ROOT / 'diagnostics/ps4/support_report.py')
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class SupportReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='psx-support-report-', dir='/var/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.digest = 'ab12' * 16
        self.boot = '12345678-abcd-1234-abcd-123456789abc'
        self.identity = {'boot_id': self.boot, 'kernel_release': '7.2.11-psx-ps4-baikal-uvd-runtime-dev6',
                         'console_model': 'PS4 Slim CUH-2216B', 'board': 'Baikal B1',
                         'manifest_available': True, 'release_matches': True,
                         'declared_artifacts': {'kernel_sha256': self.digest, 'module_count': 41,
                                                'uvd_runtime': True, 'private_key': 'DO-NOT-EXPORT'},
                         'username': 'owner-alice', 'hostname': 'private-console'}
        self.inventory = {'cpu': {'models': ['AMD Jaguar'], 'listed_processors': 8, 'online': '0-7'},
                          'gpu': {'vendor': '0x1002', 'device': '0x9924', 'model': 'Liverpool', 'cu_count': 18},
                          'memory': {'MemTotal_kib': '7096000'},
                          'pci': [{'bdf': '0000:00:14.1', 'vendor': '0x104d', 'device': '0x90d8', 'serial': 'PRIVATE-SERIAL'}],
                          'usb': [{'idVendor': '0951', 'idProduct': '1666', 'product': 'DataTraveler', 'serial': 'USBSECRET'}],
                          'storage': {'devices': [{'name': 'sda', 'model': 'Example SSD', 'vendor': 'Example',
                                                  'sectors_512b': '12345678', 'read_only': '1', 'serial': 'DISKSECRET'}],
                                      'mountinfo': '/home/owner-alice/private-drive'},
                          'wifi': {'ssid': 'Hidden WiFi', 'password': 'wireless-secret'},
                          'hostname': 'private-console'}
        self.catalogue = {'suite_id': 'psx.ps4.diagnostics', 'suite_version': '0.1.0', 'tests': [
            {'id': 'inventory.readonly', 'title': 'Hardware inventory', 'kind': 'observation'},
            {'id': 'cpu.compute', 'title': 'CPU computation', 'kind': 'functional'},
            {'id': 'memory.pattern', 'title': 'Memory pattern', 'kind': 'functional'},
            {'id': 'audio.44100', 'title': 'Playback', 'kind': 'operator'},
        ]}
        self.records = [
            {'test_id': 'cpu.compute', 'run_id': 'one', 'identity': {'boot_id': self.boot},
             'status': 'PASS', 'started_at': '2026-11-07T15:00:00Z', 'message': 'Known computation passed.',
             'evidence': {'iterations': 1234, 'duration_seconds': 2, 'stdout': 'secret raw output'},
             'parameters': {'password': 'job-password'}, 'commands': [{'argv': ['echo', 'credential']}]},
            {'test_id': 'inventory.readonly', 'run_id': 'two', 'identity': {'boot_id': self.boot},
             'status': 'INCONCLUSIVE', 'message': 'Collected metadata only.', 'hardware_acceptance': True,
             'evidence': {'cpu': {'models': ['AMD Jaguar'], 'listed_processors': 8}}},
            {'test_id': 'memory.pattern', 'run_id': 'old', 'identity': {'boot_id': 'older-boot'},
             'status': 'PASS', 'message': 'Earlier run.'},
        ]

    def build(self, **kwargs):
        return REPORT.build_report(self.identity, self.inventory, self.records, self.catalogue, **kwargs)

    def export(self, root=None, **kwargs):
        return REPORT.export_report(root or self.root, self.identity, self.inventory, self.records, self.catalogue, **kwargs)

    def test_optional_startup_defaults_preserve_existing_report_and_markdown(self):
        with mock.patch.object(REPORT, 'datetime') as clock:
            clock.now.return_value.isoformat.return_value = '2026-11-07T15:00:00+00:00'
            original = self.build()
            explicit_none = self.build(startup=None)
        self.assertEqual(original, explicit_none)
        self.assertNotIn('startup', original)
        self.assertEqual(REPORT.render_markdown(original), REPORT.render_markdown(explicit_none))
        self.assertNotIn('## Startup checks', REPORT.render_markdown(original))

    def test_startup_availability_retains_reasons_without_changing_test_coverage(self):
        before = self.build()
        startup = {'ssh': {'available': False, 'message': 'SSH setup requires the selected diagnostic kernel.'},
                   'power': {'available': False, 'guard_stage': 'ram-proof',
                             'reason': 'Power unavailable: the RAM boot proof could not be verified.'}}
        report = self.build(startup=startup)
        self.assertEqual(report['startup'], startup)
        for key in ('coverage', 'results', 'summary'):
            self.assertEqual(report[key], before[key])
        self.assertFalse(report['hardware_acceptance'])
        markdown = REPORT.render_markdown(report)
        self.assertIn('## Startup checks', markdown)
        self.assertIn('| SSH setup | Unavailable | Not reported |', markdown)
        self.assertIn('| Power controls | Unavailable | ram-proof |', markdown)
        self.assertIn('Availability only; these checks are not functional test passes or hardware acceptance.', markdown)
        self.assertIn(startup['ssh']['message'], markdown)
        self.assertIn(startup['power']['reason'], markdown)
        available = self.build(startup={'ssh': {'available': True, 'message': 'SSH setup is available.'},
                                       'power': {'available': True, 'reason': 'Power controls are available.'}})
        self.assertEqual(available['coverage'], before['coverage'])
        self.assertIn('| SSH setup | Available | Not reported |', REPORT.render_markdown(available))
        self.assertNotIn('status', available['startup']['ssh'])

    def test_startup_text_is_redacted_and_unknown_nested_fields_never_leave_the_report(self):
        private = 'startup-only-credential'
        startup = {'ssh': {'available': False,
                          'message': 'Kernel mismatch; ' + private + ' for owner-alice at 192.168.50.98',
                          'password': private, 'raw': {'argv': ['/home/owner-alice/private-helper']}},
                   'power': {'available': False, 'guard_stage': 'ram-proof',
                             'reason': '<script>proof failed</script> | `detail`\npath /home/private-user/boot.json',
                             'hardware_acceptance': True, 'identity': {'boot_id': self.boot}},
                   'network': {'token': 'unused-private-token', 'state': ['uncontrolled nested data']}}
        report = self.build(startup=startup)
        self.assertEqual(set(report['startup']), {'ssh', 'power'})
        self.assertEqual(set(report['startup']['ssh']), {'available', 'message'})
        self.assertEqual(set(report['startup']['power']), {'available', 'reason', 'guard_stage'})
        self.assertIn('Kernel mismatch', report['startup']['ssh']['message'])
        public = json.dumps(report) + REPORT.render_markdown(report)
        for value in (private, 'owner-alice', '192.168.50.98', 'private-helper', 'private-user',
                      'unused-private-token', 'uncontrolled nested data', self.boot):
            self.assertNotIn(value, public)
        markdown = REPORT.render_markdown(report)
        self.assertNotIn('<script>', markdown)
        self.assertIn('&#124;', markdown)
        self.assertNotIn('`detail`', markdown)
        self.assertEqual(self.build(startup={'ignored': {'raw': ['unused']}})['startup'], {})

    def test_startup_rejects_invalid_types_unknown_stages_and_byte_budget_overflow(self):
        invalid = [[], False, 'raw', {'ssh': []}, {'power': 'raw'},
                   {'ssh': {'message': 'Missing flag'}}, {'ssh': {'available': True}},
                   {'power': {'available': False}},
                   *({'ssh': {'available': flag, 'message': 'Wrong flag'}} for flag in (1, 0, 'false', None)),
                   *({'ssh': {'available': False, 'message': detail}} for detail in ([], {}, 1, None)),
                   *({'power': {'available': False, 'reason': 'Unavailable', 'guard_stage': stage}}
                     for stage in (None, [], 1, 'RAM-PROOF', 'unknown', 'token=private-stage')),
                   {'power': {'available': False, 'reason': 'x' * (REPORT.MAX_STARTUP_TEXT_BYTES + 1)}},
                   {'ssh': {'available': False, 'message': '\u00e9' * (REPORT.MAX_STARTUP_TEXT_BYTES // 2 + 1)}}]
        for startup in invalid:
            with self.subTest(startup=str(startup)[:70]), self.assertRaises(REPORT.ReportError):
                self.build(startup=startup)
        exact = '\u00e9' * (REPORT.MAX_STARTUP_TEXT_BYTES // 2)
        self.assertEqual(self.build(startup={'ssh': {'available': True, 'message': exact}})['startup']['ssh']['message'], exact)
        with self.assertRaises(REPORT.ReportError):
            self.build(startup={'ssh': {'available': False, 'message': 'z ' * 250}}, sensitive_values=['z'])
        malformed = self.build()
        malformed['startup'] = {'power': {'available': 'yes', 'reason': 'Wrong type'}}
        with self.assertRaises(REPORT.ReportError): REPORT.render_markdown(malformed)
        result = self.export(startup={'power': {'available': 1, 'reason': 'credential=private-failure'}})
        self.assertEqual(result['status'], 'ERROR')
        self.assertNotIn('private-failure', json.dumps(result))
        self.assertFalse(list(self.root.iterdir()))

    def test_export_carries_only_startup_metadata_into_json_and_issue_summary(self):
        startup = {'ssh': {'available': False, 'message': 'SSH setup unavailable: kernel mismatch.'},
                   'power': {'available': False, 'guard_stage': 'ram-proof',
                             'reason': 'Power unavailable: the RAM boot proof could not be verified.',
                             'raw_mountinfo': 'private raw runtime mount data'}}
        result = self.export(startup=startup, kernel_log='')
        self.assertEqual(result['status'], 'READY')
        with zipfile.ZipFile(result['path']) as archive:
            report = json.loads(archive.read('report.json'))
            markdown = archive.read('issue.md').decode()
            self.assertEqual(report['startup']['ssh'], startup['ssh'])
            self.assertEqual(report['startup']['power']['guard_stage'], 'ram-proof')
            self.assertNotIn('raw_mountinfo', report['startup']['power'])
            self.assertNotIn('private raw runtime mount data', markdown)
            self.assertIn(startup['ssh']['message'], markdown)
            self.assertIn(startup['power']['reason'], markdown)
            self.assertFalse(report['hardware_acceptance'])
            self.assertEqual(archive.read('kernel.log'), b'')
        for stage in REPORT.POWER_GUARD_STAGES:
            report = self.build(startup={'power': {'available': False, 'reason': 'Unavailable', 'guard_stage': stage}})
            self.assertEqual(report['startup']['power']['guard_stage'], stage)

    def test_typed_hardware_hashes_and_boot_coverage(self):
        report = self.build(automatic={'status': 'PASS', 'completed': 3, 'total': 3, 'hardware_acceptance': True})
        self.assertFalse(report['hardware_acceptance'])
        self.assertFalse(report['automatic']['hardware_acceptance'])
        self.assertEqual(report['build']['declared_artifacts']['kernel_sha256'], self.digest)
        self.assertEqual(report['inventory']['cpu']['models'], ['AMD Jaguar'])
        self.assertEqual(report['inventory']['gpu']['cu_count'], 18)
        self.assertEqual(report['inventory']['memory']['MemTotal_kib'], 7096000)
        self.assertEqual(report['inventory']['pci'][0]['bdf'], '0000:00:14.1')
        self.assertEqual(report['build']['console_model'], 'PS4 Slim CUH-2216B')
        self.assertEqual(report['results'][0]['measurements'], {'iterations': 1234, 'duration_seconds': 2})
        rows = {row['test_id']: row for row in report['coverage']}
        self.assertEqual(rows['cpu.compute']['coverage'], 'functional_pass')
        self.assertEqual(rows['inventory.readonly']['coverage'], 'observed')
        self.assertEqual(rows['memory.pattern']['status'], 'NOT_RUN')
        self.assertEqual(rows['audio.44100']['coverage'], 'not_run')
        self.assertEqual(report['summary']['older_boot_records'], 1)
        serialized = json.dumps(report)
        for private in (self.boot, 'older-boot', 'DO-NOT-EXPORT', 'PRIVATE-SERIAL', 'USBSECRET', 'DISKSECRET',
                        'job-password', 'wireless-secret', 'secret raw output', '/home/owner-alice/private-drive'):
            self.assertNotIn(private, serialized)
        self.assertNotIn('commands', report['results'][0])
        self.assertNotIn('parameters', report['results'][0])

    def test_credential_network_identity_address_redaction(self):
        log = '\n'.join((
            '-----BEGIN OPENSSH PRIVATE KEY-----', 'a-super-secret-private-key-body', '-----END OPENSSH PRIVATE KEY-----',
            'password=hunter-two', '{"token":"an-arbitrary-small-token"}', 'Authorization: Bearer bearer-value',
            'hostname: private-console', 'SSID: Hidden WiFi', 'SerialNumber: PRIVATE-SERIAL',
            'owner-alice logged in from 192.168.50.98 and fe80::1234%eth0 or ::1',
            'hardware aa:bb:cc:dd:ee:ff and aa-bb-cc-dd-ee-ff',
            'ethernet aabb.ccdd.eeff',
            'Linux build (builder-name@private-build-machine)',
            'Kernel command line: foo=private-unlabelled-content',
            'psx_debug_password=compound-credential',
            'disk 12345678-abcd-1234-abcd-123456789abc path /run/media/alice/private/file',
            'Windows C:\\Users\\alice\\secret.txt root /home/alice/private',
            'build at /home/Private Person/Private Project/secret-source.c',
            'kernel rip 0xffffffff85f28000 [<ffffffff85f28000>] build ' + self.digest,
            'credential ghp_abcdefghijklmno service sk-abcdefghijklmno',
            'at https://owner:secret@example.org/private?token=value',
            'Known private-network-display-name and wireless-secret',
        ))
        report = self.build(kernel_log=log, sensitive_values=['private-network-display-name'])
        output = json.dumps(report)
        for value in ('a-super-secret-private-key-body', 'hunter-two', 'an-arbitrary-small-token', 'bearer-value',
                      'private-console', 'Hidden WiFi', 'PRIVATE-SERIAL', 'owner-alice', '192.168.50.98',
                      'fe80::1234', '::1', 'aa:bb:cc:dd:ee:ff', 'aa-bb-cc-dd-ee-ff',
                      'aabb.ccdd.eeff', 'builder-name', 'private-build-machine',
                      'private-unlabelled-content', 'compound-credential',
                      self.boot, 'secret.txt', '/home/alice', 'ffffffff85f28000',
                      'Private Person', 'Private Project', 'secret-source.c',
                      'ghp_abcdefghijklmno', 'sk-abcdefghijklmno', 'example.org',
                      'private-network-display-name', 'wireless-secret'):
            self.assertNotIn(value, output, value)
        self.assertIn(self.digest, report['kernel_log'])
        self.assertIn('[redacted', output)

    def test_observation_needs_collected_public_evidence(self):
        for status in ('INCONCLUSIVE', 'PASS', 'DEGRADED'):
            for evidence in (None, {}, {'stdout': 'private raw log'},
                             {'cpu': {}, 'pci': [], 'usb': [{}]},
                             {'model': 'private-console'}):
                with self.subTest(status=status, evidence=evidence):
                    row = self.records[1]
                    row['status'] = status
                    row['message'] = 'Diagnostic collection failed.'
                    row.pop('evidence', None)
                    if evidence is not None:
                        row['evidence'] = evidence
                    report = self.build()
                    coverage = report['coverage'][0]
                    self.assertEqual(coverage['coverage'], 'observation_unavailable')
                    self.assertEqual(coverage['status'], 'INCONCLUSIVE')
                    self.assertEqual(report['results'][1]['status'], status)
                    self.assertEqual(report['results'][1]['observations'], {})

    def test_stop_required_overrides_pass_coverage_but_preserves_receipt(self):
        for index in (0, 1):
            with self.subTest(kind=self.catalogue['tests'][1 - index]['kind']):
                records = copy.deepcopy(self.records)
                records[index].update(status='PASS', stop_required=True)
                report = REPORT.build_report(self.identity, self.inventory, records, self.catalogue)
                coverage = next(row for row in report['coverage'] if row['test_id'] == records[index]['test_id'])
                self.assertEqual(coverage['status'], 'BLOCKED')
                self.assertEqual(coverage['coverage'], 'inconclusive')
                self.assertEqual(report['results'][index]['status'], 'PASS')
                self.assertTrue(report['results'][index]['stop_required'])
                self.assertNotIn('functional_pass', coverage.values())
        self.records[2]['stop_required'] = True
        report = self.build()
        self.assertEqual(report['coverage'][2]['status'], 'NOT_RUN')

    def test_standard_observations_preserve_only_collected_facts(self):
        examples = (
            ('storage.inventory', {'devices': [{'name': 'sda', 'sectors_512b': '4096', 'serial': 'secret'}]},
             {'storage': {'devices': [{'name': 'sda', 'sectors_512b': 4096}]}}),
            ('ethernet.status', {'present': False, 'carrier': '0', 'operstate': 'down',
                                 'mtu': '1500', 'counters': {'rx_bytes': '0'}, 'route_table': 'private route'},
             {'present': False, 'carrier': 0, 'operstate': 'down', 'mtu': 1500, 'counters': {'rx_bytes': 0}}),
            ('audio.info', {'cards': ' 0 [PCH ]: HDA-Intel - HDA Intel PCH\n',
                            'pcm': '00-00: HDMI : playback 1\n', 'supported_output_rate': 44100},
             {'cards_entries': 1, 'pcm_entries': 1}),
            ('audio.info', {'cards': '--- no soundcards ---'}, {'cards_entries': 0}),
        )
        for test_id, evidence, expected in examples:
            with self.subTest(test_id=test_id, evidence=evidence):
                catalogue = {'tests': [{'id': test_id, 'kind': 'observation'}]}
                records = [{'test_id': test_id, 'identity': {'boot_id': self.boot},
                            'status': 'INCONCLUSIVE', 'evidence': evidence}]
                report = REPORT.build_report(self.identity, self.inventory, records, catalogue)
                self.assertEqual(report['results'][0]['observations'], expected)
                self.assertEqual(report['coverage'][0]['coverage'], 'observed')
        for evidence in ({'supported_output_rate': 44100}, {'cards': '[redacted]', 'pcm': 'private text'}):
            records[0].update(test_id='audio.info', evidence=evidence)
            report = REPORT.build_report(self.identity, self.inventory, records, catalogue)
            self.assertEqual(report['coverage'][0]['coverage'], 'observation_unavailable')

    def test_unterminated_private_key_and_controls(self):
        report = self.build(kernel_log='normal\x1b[31m\u202e\n-----BEGIN RSA PRIVATE KEY-----\nSECRET UNTIL EOF')
        self.assertNotIn('SECRET UNTIL EOF', report['kernel_log'])
        self.assertNotIn('\x1b', report['kernel_log'])
        self.assertNotIn('\u202e', report['kernel_log'])

    def test_dedup_latest_result_and_unknown_boot(self):
        self.records.append(copy.deepcopy(self.records[0]))
        self.records.append({'test_id': 'cpu.compute', 'run_id': 'later', 'identity': {'boot_id': self.boot},
                             'status': 'FAIL', 'started_at': '2026-11-07T15:01:00Z', 'message': 'Mismatch'})
        self.records.append({'test_id': 'audio.44100', 'status': 'PASS', 'message': 'No boot ID'})
        report = self.build()
        self.assertEqual(len(report['results']), 5)
        self.assertEqual(report['coverage'][1]['status'], 'FAIL')
        self.assertEqual(report['coverage'][3]['status'], 'NOT_RUN')
        self.assertEqual(report['summary']['unknown_boot_records'], 1)

    def test_markdown_is_ready_and_escaped(self):
        self.records[0]['message'] = '<script>alert(1)</script> | injected `code`\nrow'
        markdown = REPORT.render_markdown(self.build())
        self.assertIn('## Reproduction', markdown)
        self.assertIn('## Attachments', markdown)
        self.assertIn('[add your observation]', markdown)
        self.assertIn('older-boot', markdown.lower())
        self.assertNotIn('<script>', markdown)
        self.assertIn('&#124;', markdown)
        self.assertNotIn('injected `code`', markdown)

    def test_caps_and_malformed_types_fail_before_export(self):
        with self.assertRaises(REPORT.ReportError):
            self.build(kernel_log='x' * (REPORT.MAX_KERNEL_BYTES + 1))
        with self.assertRaises(REPORT.ReportError):
            REPORT.build_report(self.identity, self.inventory, [{}] * 201, self.catalogue)
        with self.assertRaises(REPORT.ReportError):
            self.build(sensitive_values=['x'] * 257)
        for mutation in ({'message': ['bad']}, {'evidence': 'bad'}, {'status': 'made-up'},
                         {'test_id': []}, {'status': []},
                         {'stop_required': 1}, {'evidence': {'iterations': float('nan')}},
                         {'message': 'x' * 4097}):
            records = copy.deepcopy(self.records)
            records[0].update(mutation)
            with self.subTest(mutation=str(mutation)[:60]), self.assertRaises(REPORT.ReportError):
                REPORT.build_report(self.identity, self.inventory, records, self.catalogue)
        cyclic = {}; cyclic['self'] = cyclic
        with self.assertRaises(REPORT.ReportError):
            REPORT.build_report(cyclic, self.inventory, [], self.catalogue)
        oversized = {'ignored': ['x' * 65536] * 33}
        with self.assertRaises(REPORT.ReportError):
            REPORT.build_report(oversized, {}, [], self.catalogue)
        result = self.export(kernel_log='x' * (REPORT.MAX_KERNEL_BYTES + 1))
        self.assertEqual(result['status'], 'ERROR')
        self.assertFalse(list(self.root.iterdir()))

    def test_fixed_archive_members_and_immutable_metadata(self):
        result = self.export(kernel_log='A bounded kernel log\n')
        self.assertEqual(result['status'], 'READY')
        self.assertRegex(result['report_id'], r'^[0-9a-f]{32}$')
        path = Path(result['path']); data = path.read_bytes()
        self.assertEqual(path.name, result['filename'])
        self.assertEqual(result['bytes'], len(data))
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertLessEqual(len(data), REPORT.MAX_ZIP_BYTES)
        info = path.stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o400)
        self.assertEqual(info.st_nlink, 1)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), ['issue.md', 'report.json', 'kernel.log'])
            report = json.loads(archive.read('report.json'))
            self.assertFalse(report['hardware_acceptance'])
            self.assertEqual(archive.read('kernel.log'), b'A bounded kernel log\n')
            self.assertNotIn(str(self.root), archive.read('issue.md').decode())
            self.assertNotIn(str(self.root), archive.read('report.json').decode())
        self.assertEqual(len(list(self.root.iterdir())), 1)
        second = self.export()
        self.assertEqual(second['status'], 'READY')
        self.assertNotEqual(second['report_id'], result['report_id'])

    def test_export_refuses_leaf_and_parent_symlinks(self):
        real = self.root / 'real'; real.mkdir(mode=0o700)
        child = real / 'child'; child.mkdir(mode=0o700)
        alias = self.root / 'alias'; alias.symlink_to(real, target_is_directory=True)
        for root in (alias, alias / 'child'):
            self.assertEqual(self.export(root)['status'], 'ERROR')
        self.assertFalse(list(child.iterdir()))
        self.assertEqual(list(real.iterdir()), [child])

    def test_export_refuses_missing_shared_or_file_root(self):
        missing = self.root / 'missing'
        self.assertEqual(self.export(missing)['status'], 'ERROR')
        self.assertFalse(missing.exists())
        regular = self.root / 'regular'; regular.write_text('keep')
        self.assertEqual(self.export(regular)['status'], 'ERROR')
        shared = self.root / 'shared'; shared.mkdir(mode=0o777); shared.chmod(0o777)
        self.assertEqual(self.export(shared)['status'], 'ERROR')
        self.assertEqual(regular.read_text(), 'keep')

    def test_no_overwrite_existing_file_symlink_or_hardlink(self):
        ident = 'a' * 32
        final = self.root / ('diagnostix-report-' + ident + '.zip')
        protected = self.root / 'protected'; protected.write_text('private original')
        for kind in ('file', 'symlink', 'hardlink', 'temporary'):
            temporary = self.root / ('.' + final.name + '.tmp')
            with self.subTest(kind=kind), mock.patch.object(REPORT.secrets, 'token_hex', return_value=ident):
                if kind == 'file': final.write_text('existing report')
                elif kind == 'symlink': final.symlink_to(protected)
                elif kind == 'hardlink': os.link(protected, final)
                else: temporary.symlink_to(protected)
                self.assertEqual(self.export()['status'], 'ERROR')
                self.assertEqual(protected.read_text(), 'private original')
                if kind == 'file': self.assertEqual(final.read_text(), 'existing report')
                if kind == 'temporary': self.assertTrue(temporary.is_symlink()); temporary.unlink()
                else: final.unlink(); self.assertFalse(temporary.exists())

    def test_partial_write_fsync_failure_and_nonprivate_errors(self):
        original_write = REPORT.os.write
        with mock.patch.object(REPORT.os, 'write', side_effect=lambda fd, data: original_write(fd, data[:17])):
            self.assertEqual(self.export()['status'], 'READY')
        before = set(self.root.iterdir())
        with mock.patch.object(REPORT.os, 'fsync', side_effect=OSError('password=private-failure /home/secret')):
            result = self.export()
        self.assertEqual(result['status'], 'ERROR')
        self.assertNotIn('private-failure', json.dumps(result))
        self.assertEqual(set(self.root.iterdir()), before)

    def test_catalogue_strings_are_redacted_and_typed(self):
        secret = 'ghp_privatecataloguesecret'
        self.catalogue['tests'].append({'id': secret, 'title': secret, 'kind': 'functional'})
        self.records.append({'test_id': secret, 'identity': {'boot_id': self.boot}, 'status': 'PASS'})
        report = self.build()
        self.assertNotIn(secret, json.dumps(report))
        self.assertEqual(report['coverage'][-1]['status'], 'PASS')
        self.catalogue['tests'][-1]['kind'] = []
        with self.assertRaises(REPORT.ReportError): self.build()

    def test_archive_budget_failure_leaves_no_artifacts(self):
        with mock.patch.object(REPORT, 'MAX_ZIP_BYTES', 4097):
            self.assertEqual(self.export()['status'], 'ERROR')
        self.assertFalse(list(self.root.iterdir()))


if __name__ == '__main__':
    unittest.main()

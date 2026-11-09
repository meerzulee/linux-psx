# SPDX-License-Identifier: MIT
"""Bounded, redacted support artifacts. No probing, commands or network access.

Inputs are in-memory diagnostic values, never paths to evidence files. The
caller supplies an existing private export directory. Export metadata is local;
its path and ID are not copied into the public report. No issue is submitted.
"""
from datetime import datetime, timezone
import hashlib
import html
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import zipfile

MAX_RECORDS = 200
MAX_KERNEL_BYTES = 512 * 1024
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_MARKDOWN_BYTES = 1024 * 1024
MAX_ZIP_BYTES = 4 * 1024 * 1024
MAX_STARTUP_TEXT_BYTES = 512
POWER_GUARD_STAGES = frozenset(('identity', 'profile', 'contract', 'ram-proof',
                               'root-proof', 'kernel-gates', 'usb-receipt', 'prior-request'))
STATUSES = frozenset(('PASS', 'FAIL', 'DEGRADED', 'NOT_RUN', 'UNSUPPORTED',
                      'DEFERRED', 'BLOCKED', 'INCONCLUSIVE'))
KINDS = frozenset(('observation', 'functional', 'operator', 'unavailable', 'deferred', 'blocked'))
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
HASH = re.compile(r'[a-fA-F0-9]{64}\Z')
SENSITIVE_KEY = re.compile(r'password|passwd|passphrase|secret|token|credential|private.?key|'
                           r'api.?key|ssid|serial|uuid|boot_id|mac(?:_address)?$|host(?:name)?$|user(?:name)?$|login$', re.I)
SECRET_LINE = re.compile(r'\b(?:password|passwd|passphrase|pwd|secret|token|authorization|'
                         r'api[_ -]?key|private[_ -]?key|ssid|bssid|serial(?:number|[_ -]number)?|'
                         r'uuid|mac(?:[_ ]address)?|hostname|username|login|user|host)\b\s*["\']?\s*(?:[:=]|set to\b)', re.I)
COMPOSITE_SECRET = re.compile(r'\b[\w.-]*(?:password|passwd|passphrase|secret|token|credential|api_key|private_key)[\w.-]*["\']?\s*[:=]', re.I)
CMDLINE = re.compile(r'\b(?:kernel\s+)?command\s+line\s*:', re.I)
PRIVATE_PATH_LINE = re.compile(r'(?:/(?:home|root|Users|run/media|mnt|data|user|var/tmp|tmp)/|'
                               r'\b[A-Za-z]:[\\/](?:Users|Documents)[\\/])', re.I)
PEM = re.compile(r'-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?(?:-----END [^-\r\n]*PRIVATE KEY-----|\Z)', re.S)
MAC = re.compile(r'(?<![\w])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![\w])', re.I)
DOTTED_MAC = re.compile(r'(?<![\w])(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}(?![\w])', re.I)
UUID = re.compile(r'(?<![\w])[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}(?![\w])', re.I)
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
IPV6 = re.compile(r'(?<![\w:])[0-9a-f:]*:[0-9a-f:.]*(?:%[A-Za-z0-9_.-]+)?(?![\w:])', re.I)
ADDRESS = re.compile(r'(?<![\w])(?:0x)?ffff[0-9a-f]{12}(?![\w])', re.I)
URL = re.compile(r'\b(?:https?|ftp|ssh)://[^\s<>"\']+', re.I)
USER_HOST = re.compile(r'(?<![\w])[A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+')
PATH = re.compile(r'(?<![\w])(?:[A-Za-z]:[\\/]|/|~[/\\])[^\s<>"\']+')
LONG_TOKEN = re.compile(r'(?<![\w])[A-Za-z0-9_=-]{32,}(?![\w])')
NAMED_TOKEN = re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|'
                         r'sk-[A-Za-z0-9_-]{10,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)')


class ReportError(ValueError):
    """Invalid input or an exceeded fixed report budget; messages contain no input."""


def _bytes(value):
    try:
        return value.encode('utf-8')
    except (UnicodeError, AttributeError) as exc:
        raise ReportError('Report text must be valid UTF-8.') from exc


def _inspect(values):
    """Bound the whole input before serialization/redaction, collecting labelled secrets."""
    remaining, nodes, found, active = MAX_INPUT_BYTES, 0, set(), set()

    def visit(value, depth=0, sensitive=False):
        nonlocal remaining, nodes
        nodes += 1
        if nodes > 50000 or depth > 16:
            raise ReportError('Report input exceeds structural limits.')
        if isinstance(value, str):
            if len(value) > MAX_KERNEL_BYTES:
                raise ReportError('Report text exceeds its limit.')
            remaining -= len(_bytes(value))
            if sensitive and value and len(value) <= 1024:
                found.add(value)
        elif value is None or type(value) in (bool, int):
            if type(value) is int and abs(value) > 2**64:
                raise ReportError('Report number exceeds its limit.')
            remaining -= 16
        elif type(value) is float:
            if not math.isfinite(value):
                raise ReportError('Report numbers must be finite.')
            remaining -= 24
        elif type(value) in (dict, list, tuple):
            if id(value) in active or len(value) > 4096:
                raise ReportError('Report input is cyclic or oversized.')
            active.add(id(value))
            if type(value) is dict:
                for key, item in value.items():
                    if not isinstance(key, str) or len(key) > 128:
                        raise ReportError('Invalid report field name.')
                    visit(key, depth + 1)
                    visit(item, depth + 1, sensitive or bool(SENSITIVE_KEY.search(key)))
            else:
                for item in value:
                    visit(item, depth + 1, sensitive)
            active.remove(id(value))
        else:
            raise ReportError('Report inputs must contain JSON values only.')
        if remaining < 0 or len(found) > 256:
            raise ReportError('Report input exceeds its byte or privacy-value budget.')
    visit(values)
    return found


class _Redactor:
    def __init__(self, values):
        self.values = sorted(values, key=len, reverse=True)

    def __call__(self, value, limit=4096):
        if not isinstance(value, str) or len(_bytes(value)) > limit:
            raise ReportError('Public text field exceeds its limit or has the wrong type.')
        value = PEM.sub('[redacted private key]', value)
        value = '\n'.join('[redacted sensitive line]' if SECRET_LINE.search(line) or
                          COMPOSITE_SECRET.search(line) or CMDLINE.search(line) or
                          PRIVATE_PATH_LINE.search(line) else line
                          for line in value.split('\n'))
        for sensitive in self.values:
            if len(sensitive) < 4:
                value = re.sub(r'(?<!\w)' + re.escape(sensitive) + r'(?!\w)', '[redacted]', value)
            else:
                value = value.replace(sensitive, '[redacted]')
            if len(value) > MAX_JSON_BYTES:
                raise ReportError('Redacted text exceeds its limit.')
        value = NAMED_TOKEN.sub('[redacted token]', value)
        value = MAC.sub('[redacted MAC]', value)
        value = DOTTED_MAC.sub('[redacted MAC]', value)
        value = UUID.sub('[redacted UUID]', value)

        def ip(match):
            try:
                ipaddress.ip_address(match.group().split('%', 1)[0])
            except ValueError:
                return match.group()
            return '[redacted IP]'
        value = IPV4.sub(ip, value)
        value = IPV6.sub(ip, value)
        value = ADDRESS.sub('[redacted kernel address]', value)
        value = URL.sub('[redacted URL]', value)
        value = USER_HOST.sub('[redacted identity]', value)
        value = PATH.sub(lambda m: '/init' if m.group() == '/init' else '[redacted path]', value)
        value = LONG_TOKEN.sub(lambda m: m.group() if HASH.fullmatch(m.group()) else '[redacted token]', value)
        # Escape/control bytes cannot inject terminal commands or conceal log text.
        value = ''.join(c if c in '\n\t' or (ord(c) >= 32 and ord(c) != 127 and
                         not 0x202a <= ord(c) <= 0x202e and not 0x2066 <= ord(c) <= 0x2069)
                        else '?' for c in value)
        if len(_bytes(value)) > max(limit * 4, 4096):
            raise ReportError('Redacted text exceeds its output limit.')
        return value


def _text_fields(source, names, redact):
    result = {}
    for name in names:
        value = source.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ReportError('Public text field has the wrong type.')
        result[name] = redact(value)
    return result


def _facts(source, text, numbers, booleans, redact):
    if type(source) is not dict:
        raise ReportError('Public facts must be an object.')
    result = _text_fields(source, text, redact)
    for name in numbers:
        value = source.get(name)
        if value is None:
            continue
        # Sysfs values arrive as decimal strings; export typed numbers.
        if isinstance(value, str) and re.fullmatch(r'-?\d{1,20}', value):
            value = int(value)
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 2**63:
            raise ReportError('Public measurement has the wrong type or range.')
        result[name] = value
    for name in booleans:
        if name in source:
            if type(source[name]) is not bool:
                raise ReportError('Public flag must be boolean.')
            result[name] = source[name]
    return result


def _identity(source, redact):
    result = _facts(source, ('kernel_release', 'expected_kernel_release', 'declared_kernel_release',
        'platform', 'console_model', 'model', 'board', 'southbridge', 'firmware_version',
        'appliance_version', 'build_id'), (), ('release_matches', 'manifest_available'), redact)
    hashes = ('manifest_sha256', 'runner_sha256', 'catalogue_sha256', 'kernel_sha256',
              'config_sha256', 'modules_sha256', 'initramfs_sha256', 'rootfs_sha256')
    for key in hashes:
        value = source.get(key)
        if value is not None:
            if not isinstance(value, str) or not HASH.fullmatch(value):
                raise ReportError('Build digest must be SHA-256.')
            result[key] = value.lower()
    if 'declared_artifacts' in source:
        declared = source['declared_artifacts']
        result['declared_artifacts'] = _facts(declared, ('kernel_release',), ('module_count',), ('uvd_runtime',), redact)
        for key in hashes:
            value = declared.get(key)
            if value is not None:
                if not isinstance(value, str) or not HASH.fullmatch(value):
                    raise ReportError('Build digest must be SHA-256.')
                result['declared_artifacts'][key] = value.lower()
    result['scope'] = 'Declared build identities and observed release; not verification of the running kernel bytes.'
    return result


def _inventory(source, redact):
    if type(source) is not dict:
        raise ReportError('Inventory must be an object.')
    out = _text_fields(source, ('console_model', 'model', 'board', 'southbridge', 'firmware_version'), redact)
    specifications = {
        'cpu': (('online',), ('listed_processors', 'cores', 'threads'), ()),
        'memory': ((), ('MemTotal_kib', 'MemAvailable_kib', 'MemFree_kib', 'SwapTotal_kib', 'SwapFree_kib'), ()),
        'gpu': (('vendor', 'device', 'model', 'driver'), ('compute_units', 'cu_count', 'vram_mib'), ()),
        'config': ((), (), ('proc_config_gz_present', 'module_directory_present')),
    }
    for name, (text, numbers, booleans) in specifications.items():
        if name in source:
            out[name] = _facts(source[name], text, numbers, booleans, redact)
    if type(source.get('cpu')) is dict and 'models' in source['cpu']:
        values = source['cpu']['models']
        if type(values) is not list or len(values) > 32:
            raise ReportError('CPU model list exceeds its limit.')
        out['cpu']['models'] = [redact(value, 512) for value in values]
    arrays = {
        'pci': (('bdf', 'vendor', 'device', 'class', 'revision', 'driver', 'model'), ()),
        'usb': (('sysfs_name', 'idVendor', 'idProduct', 'product', 'speed'), ()),
        'thermal': (('zone', 'type'), ('temperature_millicelsius',)),
    }
    for name, (text, numbers) in arrays.items():
        if name in source:
            values = source[name]
            if type(values) is not list or len(values) > 256:
                raise ReportError('Hardware list exceeds its limit.')
            out[name] = [_facts(item, text, numbers, (), redact) for item in values]
    if 'storage' in source:
        storage = source['storage']
        if type(storage) is not dict or type(storage.get('devices', [])) is not list or len(storage.get('devices', [])) > 64:
            raise ReportError('Storage inventory exceeds its limit.')
        out['storage'] = {'devices': [_facts(item, ('name', 'model', 'vendor'),
            ('sectors_512b', 'read_only', 'rotational'), ('device_node_present',), redact)
            for item in storage.get('devices', [])]}
    out['scope'] = 'Inventory is observation only; device presence is not a functional pass or hardware acceptance.'
    return out


def _catalogue(catalogue, redact):
    if type(catalogue) is not dict or type(catalogue.get('tests')) is not list or len(catalogue['tests']) > 200:
        raise ReportError('Catalogue must contain at most 200 tests.')
    tests = {}
    for test in catalogue['tests']:
        if type(test) is not dict or not isinstance(test.get('id'), str) or not IDENTIFIER.fullmatch(test['id']):
            raise ReportError('Invalid catalogue test identity.')
        if test['id'] in tests or not isinstance(test.get('kind'), str) or test['kind'] not in KINDS:
            raise ReportError('Duplicate test or unknown catalogue kind.')
        tests[test['id']] = {'test_id': redact(test['id'], 128), 'title': redact(test.get('title', test['id']), 256),
                            'kind': test['kind']}
    return tests


def _observations(test_id, evidence, redact):
    """Public collected facts only; policy constants and raw logs do not count."""
    if test_id in ('inventory.readonly', 'storage.inventory'):
        facts = _inventory(evidence if test_id == 'inventory.readonly' else {'storage': evidence}, redact)
        facts.pop('scope', None)
        # Empty containers and redaction placeholders are not observations.
        def prune(value):
            if isinstance(value, dict):
                return {key: item for key, child in value.items()
                        if (item := prune(child)) is not None} or None
            if isinstance(value, list):
                return [item for child in value if (item := prune(child)) is not None] or None
            if value is None or value == '' or isinstance(value, str) and '[redacted' in value:
                return None
            return value
        return prune(facts) or {}
    if test_id == 'ethernet.status':
        facts = _facts(evidence, (), ('carrier', 'mtu'), ('present',), redact)
        if evidence.get('operstate') in ('unknown', 'notpresent', 'down', 'lowerlayerdown', 'testing', 'dormant', 'up'):
            facts['operstate'] = evidence['operstate']
        counters = _facts(evidence.get('counters', {}), (),
                         ('rx_bytes', 'tx_bytes', 'rx_errors', 'tx_errors', 'rx_dropped', 'tx_dropped'), (), redact)
        if counters:
            facts['counters'] = counters
        return facts
    if test_id == 'audio.info':
        facts = {}
        for key, pattern in (('cards', r'^\s*\d+\s+\[[^\]\r\n]+\]\s*:'), ('pcm', r'^\s*\d+-\d+:')):
            raw = evidence.get(key)
            if isinstance(raw, str):
                count = len(re.findall(pattern, raw, re.M))
                if count:
                    facts[key + '_entries'] = count
                elif key == 'cards' and raw.strip() == '--- no soundcards ---':
                    facts['cards_entries'] = 0
        return facts
    return {}


def _encoded(value):
    data = _bytes(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    if len(data) > MAX_JSON_BYTES:
        raise ReportError('Serialized report exceeds its limit.')
    return data


def _startup(source, redact):
    """Allowlisted availability only; never interpret startup checks as test results."""
    if type(source) is not dict:
        raise ReportError('Startup checks must be an object.')
    result = {}
    for name, detail in (('ssh', 'message'), ('power', 'reason')):
        if name not in source:
            continue
        check = source[name]
        if type(check) is not dict or type(check.get('available')) is not bool:
            raise ReportError('Startup availability must be boolean.')
        value = redact(check.get(detail), MAX_STARTUP_TEXT_BYTES)
        if len(_bytes(value)) > MAX_STARTUP_TEXT_BYTES:
            raise ReportError('Redacted startup detail exceeds its limit.')
        result[name] = {'available': check['available'], detail: value}
        if name == 'power' and 'guard_stage' in check:
            stage = check['guard_stage']
            if type(stage) is not str or stage not in POWER_GUARD_STAGES:
                raise ReportError('Unknown startup power guard stage.')
            result[name]['guard_stage'] = stage
    return result


def build_report(identity, inventory, records, catalogue, *, automatic=None, kernel_log='', sensitive_values=(), startup=None):
    """Build public JSON or raise ReportError. Raw logs/parameters are never copied.

    Optional startup SSH/Power availability uses fixed fields and guard stages;
    its detail strings are limited to 512 UTF-8 bytes before and after redaction.
    """
    if type(identity) is not dict or type(inventory) is not dict or type(records) not in (list, tuple):
        raise ReportError('Invalid report input types.')
    if len(records) > MAX_RECORDS or type(sensitive_values) not in (list, tuple, set) or len(sensitive_values) > 256:
        raise ReportError('Too many records or privacy values.')
    if not isinstance(kernel_log, str) or len(_bytes(kernel_log)) > MAX_KERNEL_BYTES:
        raise ReportError('Kernel log exceeds its 512 KiB text budget.')
    if any(not isinstance(value, str) or len(value) > 1024 for value in sensitive_values):
        raise ReportError('Invalid privacy value.')
    inputs = [identity, inventory, records, catalogue, automatic, kernel_log, list(sensitive_values)]
    if startup is not None:
        inputs.append(startup)
    hidden = _inspect(inputs)
    hidden.update(value for value in sensitive_values if value)
    if len(hidden) > 256:
        raise ReportError('Too many privacy values.')
    redact = _Redactor(hidden)
    tests = _catalogue(catalogue, redact)
    public_records, seen, current_results = [], set(), {}
    current_boot = identity.get('boot_id')
    if current_boot is not None and not isinstance(current_boot, str):
        raise ReportError('Invalid boot identity.')
    for wrapper in records:
        if type(wrapper) is not dict:
            raise ReportError('Each record must be an object.')
        record = wrapper.get('result') if type(wrapper.get('result')) is dict else wrapper
        test_id = record.get('test_id', wrapper.get('test_id'))
        if not isinstance(test_id, str):
            raise ReportError('Invalid record test identity.')
        if test_id not in tests:
            continue  # No uncontrolled labels or free-form job identity.
        record_identity = record.get('identity', {})
        if type(record_identity) is not dict:
            raise ReportError('Invalid record identity.')
        boot = record_identity.get('boot_id', wrapper.get('boot_id'))
        if boot is not None and not isinstance(boot, str):
            raise ReportError('Invalid record boot identity.')
        scope = 'unknown_boot' if not boot or not current_boot else 'current_boot' if boot == current_boot else 'older_boot'
        run = record.get('run_id', wrapper.get('run_id'))
        if run is not None and not isinstance(run, str):
            raise ReportError('Invalid record identity.')
        key = (test_id, boot, run)
        if run and key in seen:
            continue
        seen.add(key)
        status = record.get('status', 'INCONCLUSIVE')
        if not isinstance(status, str) or status not in STATUSES:
            raise ReportError('Unknown result status.')
        row = {**tests[test_id], 'boot_scope': scope, 'status': status,
               'hardware_acceptance': False}
        row.update(_text_fields(record, ('message', 'started_at', 'completed_at', 'phase'), redact))
        for field in ('stop_required', 'observation_required'):
            if field in record:
                if type(record[field]) is not bool:
                    raise ReportError('Record flags must be boolean.')
                row[field] = record[field]
        if record_identity:
            row['build'] = _identity(record_identity, redact)
        # Explicit numeric facts only. No commands, argv, stdout, paths or arbitrary evidence.
        evidence = record.get('evidence', {})
        if type(evidence) is not dict:
            raise ReportError('Evidence must be an object.')
        row['measurements'] = _facts(evidence, (), ('iterations', 'duration_seconds', 'elapsed_seconds',
            'memory_mib', 'verified_bytes', 'error_count', 'bytes_checked', 'frames'), (), redact)
        if row['kind'] == 'observation':
            row['observations'] = _observations(test_id, evidence, redact)
        public_records.append(row)
        if scope == 'current_boot':
            current_results.setdefault(test_id, []).append(row)
    coverage = []
    for test_id, test in tests.items():
        matches = current_results.get(test_id, [])
        latest = max(matches, key=lambda row: row.get('started_at', ''), default=None)
        status = latest['status'] if latest else 'NOT_RUN'
        if latest and latest.get('stop_required'):
            status, classification = 'BLOCKED', 'inconclusive'
        elif status in ('NOT_RUN', 'BLOCKED', 'UNSUPPORTED', 'DEFERRED'):
            classification = 'not_run'
        elif test['kind'] == 'observation':
            if status in ('PASS', 'DEGRADED', 'INCONCLUSIVE'):
                collected = bool(latest['measurements'] or latest['observations'])
                classification = 'observed' if collected else 'observation_unavailable'
                if not collected:
                    status = 'INCONCLUSIVE'
            else:
                classification = 'observation_failed'
        elif test['kind'] == 'functional':
            classification = 'functional_pass' if status == 'PASS' else 'functional_failure' if status == 'FAIL' else 'inconclusive'
        elif test['kind'] == 'operator':
            classification = 'operator_observation'
        else:
            classification = 'inconclusive'
        coverage.append({**test, 'status': status, 'coverage': classification,
                         'message': latest.get('message', '') if latest else 'No result from this boot.'})
    auto = None
    if automatic is not None:
        auto = _facts(automatic, ('status', 'phase', 'message', 'started_at', 'completed_at'),
                      ('completed', 'total'), ('stop_required',), redact)
        auto['hardware_acceptance'] = False
    report = {'schema_version': 1, 'product': 'psX Diagnostix',
              'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'hardware_acceptance': False, 'build': _identity(identity, redact),
              'inventory': _inventory(inventory, redact), 'automatic': auto,
              'results': public_records, 'coverage': coverage,
              'summary': {'current_boot_records': sum(row['boot_scope'] == 'current_boot' for row in public_records),
                          'older_boot_records': sum(row['boot_scope'] == 'older_boot' for row in public_records),
                          'unknown_boot_records': sum(row['boot_scope'] == 'unknown_boot' for row in public_records),
                          'tests_not_run': sum(row['coverage'] == 'not_run' for row in coverage)},
              'kernel_log': redact(kernel_log, MAX_KERNEL_BYTES),
              'sharing': 'Automatically redacted, allowlisted report. Review all three files before attaching; no issue has been submitted.'}
    if startup is not None:
        report['startup'] = _startup(startup, redact)
    _encoded(report)
    return report


def render_markdown(report):
    """Render issue-ready text from a public report; no logs or raw JSON embedded."""
    _inspect(report)
    if type(report) is not dict or type(report.get('schema_version')) is not int or report['schema_version'] != 1 or report.get('product') != 'psX Diagnostix':
        raise ReportError('Unknown public report format.')
    redact = _Redactor(())

    def cell(value):
        if value is None:
            return 'Unknown'
        if type(value) not in (str, int, float, bool):
            raise ReportError('Invalid Markdown field.')
        return html.escape(redact(str(value)), quote=False).replace('|', '&#124;').replace('`', '&#96;').replace('\n', ' ')
    build = report.get('build', {})
    rows = report.get('coverage', [])
    inventory = report.get('inventory', {})
    summary = report.get('summary', {})
    if type(build) is not dict or type(inventory) is not dict or type(summary) is not dict or type(rows) is not list or len(rows) > 200:
        raise ReportError('Invalid Markdown report structure.')
    lines = ['# Diagnostix support report', '', '## Summary', '',
             'Describe the problem and what you expected: **[add your observation]**', '',
             'Current-boot records: ' + cell(summary.get('current_boot_records', 0)) +
             '; older-boot records: ' + cell(summary.get('older_boot_records', 0)) +
             '; tests without current coverage: ' + cell(summary.get('tests_not_run', 0)) + '.', '',
             '## Build and kernel', '',
             '- Kernel: ' + cell(build.get('kernel_release')),
             '- Console/model: ' + cell(build.get('console_model', inventory.get('model'))),
             '- Created: ' + cell(report.get('created_at')), '',
             'Build hashes identify declared inputs; they do not prove the running kernel bytes.', '']
    if 'startup' in report:
        startup = _startup(report['startup'], redact)
        lines += ['## Startup checks', '',
                  'Availability only; these checks are not functional test passes or hardware acceptance.', '']
        if startup:
            lines += ['| Service | Availability | Guard stage | Detail |',
                      '| --- | --- | --- | --- |']
            for name, title, detail in (('ssh', 'SSH setup', 'message'), ('power', 'Power controls', 'reason')):
                if name in startup:
                    check = startup[name]
                    lines.append('| ' + ' | '.join((title,
                        'Available' if check['available'] else 'Unavailable',
                        cell(check.get('guard_stage', 'Not reported')), cell(check[detail]))) + ' |')
        else:
            lines.append('No startup availability was supplied.')
        lines.append('')
    lines += ['## Reproduction', '', '1. [Describe the selected Automatic or Manual menu action.]',
             '2. [Describe the test and visible result.]', '3. [Add whether the issue repeats and your expected result.]', '',
             '## Results from this boot', '', '| Test | Kind | Status | Coverage | Detail |',
             '| --- | --- | --- | --- | --- |']
    for row in rows:
        if type(row) is not dict:
            raise ReportError('Invalid result row.')
        lines.append('| ' + ' | '.join(cell(row.get(key, '')) for key in ('title', 'kind', 'status', 'coverage', 'message')) + ' |')
    lines += ['', 'Older-boot and unknown-boot results are retained separately in report.json; they do not count as current-boot coverage.', '',
              'Inventory is observation, not a functional pass. No result here automatically establishes hardware acceptance.', '',
              '## Attachments', '', 'Review issue.md, report.json and kernel.log before sharing. Attach the downloaded ZIP to your GitHub issue and paste this summary into its description.',
              'Do not add passwords, keys, firmware or private raw logs. No issue has been opened or sent automatically.', '']
    rendered = '\n'.join(lines)
    if len(_bytes(rendered)) > MAX_MARKDOWN_BYTES:
        raise ReportError('Markdown report exceeds its limit.')
    return rendered


def _open_directory(directory):
    """Open each lexical parent without following symlinks, including the leaf."""
    supplied = os.fspath(directory)
    if not isinstance(supplied, str) or '\x00' in supplied or '..' in Path(supplied).parts:
        raise ReportError('Invalid export directory.')
    path = Path(os.path.abspath(supplied))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ReportError('Export directory must be private and owned by this user.')
        return descriptor, path
    except BaseException:
        os.close(descriptor)
        raise


def export_report(directory, identity, inventory, records, catalogue, *, automatic=None, kernel_log='', sensitive_values=(), startup=None):
    """Return READY immutable-file metadata or ERROR with a non-private message.

    Requires an existing owned directory with no group/world write permission.
    Every ancestor is opened with O_NOFOLLOW. Atomic link publication refuses an
    existing destination; the temporary link is removed, leaving nlink==1.
    """
    result = {'status': 'ERROR', 'report_id': '', 'filename': '', 'path': '',
              'sha256': '', 'bytes': 0, 'message': 'Support report could not be exported.'}
    root = descriptor = None
    temporary = filename = None
    created = published = False
    try:
        report = build_report(identity, inventory, records, catalogue, automatic=automatic,
                              kernel_log=kernel_log, sensitive_values=sensitive_values, startup=startup)
        payloads = {'issue.md': _bytes(render_markdown(report)), 'report.json': _encoded(report),
                    'kernel.log': _bytes(report['kernel_log'])}
        if sum(map(len, payloads.values())) > MAX_ZIP_BYTES - 4096:
            raise ReportError('Report archive exceeds its limit.')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
            for name, data in payloads.items():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                stream.writestr(info, data)
        data = archive.getvalue()
        if len(data) > MAX_ZIP_BYTES:
            raise ReportError('Report archive exceeds its limit.')
        report_id = secrets.token_hex(16)
        if not re.fullmatch(r'[0-9a-f]{32}', report_id):
            raise ReportError('Invalid generated report identity.')
        filename = 'diagnostix-report-' + report_id + '.zip'
        temporary = '.' + filename + '.tmp'
        root, path = _open_directory(directory)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=root)
        created = True
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise ReportError('Export file is not private regular storage.')
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ReportError('Export write did not complete.')
            view = view[written:]
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        named = os.stat(temporary, dir_fd=root, follow_symlinks=False)
        if (named.st_dev, named.st_ino, named.st_nlink) != (initial.st_dev, initial.st_ino, 1):
            raise ReportError('Export file identity changed.')
        os.link(temporary, filename, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
        published = True
        os.unlink(temporary, dir_fd=root)
        created = False
        final = os.stat(filename, dir_fd=root, follow_symlinks=False)
        if (final.st_dev, final.st_ino, final.st_nlink, final.st_size) != (initial.st_dev, initial.st_ino, 1, len(data)):
            raise ReportError('Published export identity changed.')
        os.fsync(root)
        result.update(status='READY', report_id=report_id, filename=filename, path=str(path / filename),
                      sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                      message='Report ready. Review it before attaching to a GitHub issue.')
    except (OSError, ValueError, TypeError):
        if root is not None:
            for name, owned in ((filename, published), (temporary, created)):
                if owned:
                    try:
                        os.unlink(name, dir_fd=root)
                    except OSError:
                        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if root is not None:
            os.close(root)
    return result

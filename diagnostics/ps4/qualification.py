#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""PS4 checklist review and explicit controller-driven hardware smoke.

Default use is read-only. Only --smoke --run submits fixed diagnostic actions;
power/reset/network-service changes are never submitted by this command.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import socket
import sys
import textwrap
import time
import uuid
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
PLAN = HERE / 'qualification.json'
STATUSES = {'PASS', 'FAIL', 'DEGRADED', 'NOT_RUN', 'UNSUPPORTED', 'DEFERRED', 'BLOCKED', 'INCONCLUSIVE'}
MODES = {'automated', 'assisted', 'manual', 'unimplemented', 'deferred'}
SCENARIO_FIELDS = {'id', 'title', 'availability', 'existing_test_id', 'fixture', 'preparation', 'sequence', 'evidence', 'limits'}
SCENARIO_LABELS = {'available-alpha': 'Available alpha action; not a result',
                   'unimplemented': 'Unimplemented; no runnable fixture/harness'}
EXTRA_IDS = {'controller': {'gpu.opengl.render', 'gpu.vulkan.render'},
             'browser': {'browser.capabilities', 'browser.canvas2d', 'browser.webgl', 'browser.animation', 'browser.video'}}
HASH_FIELDS = ('kernel_sha256', 'config_sha256', 'modules_sha256', 'initramfs_sha256', 'rootfs_sha256')
SCOPE_FIELDS = {'console_model', 'southbridge', 'boot_chain', 'kernel_release', 'uvd_runtime',
                'mesa_manifest_sha256', 'firmware_manifest_sha256', *HASH_FIELDS}
IDENTITY_PINS = ('boot_id', 'kernel_release', 'manifest_sha256', 'runner_sha256', 'catalogue_sha256')
CONTROL = Path('/run/psx-diagnostics/control.sock')
RESULTS = Path('/run/psx-media/psx-diagnostics/results')
RUN_ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}')
SMOKE_IDS = ('cpu.compute', 'memory.pattern', 'inventory.readonly', 'ethernet.status', 'storage.inventory',
             'audio.info', 'ethernet.https', 'gpu.opengl.info', 'gpu.vulkan.info', 'gpu.opengl.render',
             'gpu.vulkan.render', 'browser.firefox', 'browser.chromium', 'wayland.integration', 'audio.44100', 'uvd.avc')
TERMINAL_SCENARIOS = {
    'canonical-120': {'test_id': 'uvd.avc', 'title': 'Final canonical UVD reference decode', 'timeout_seconds': 150},
    'native720-900': {'test_id': 'uvd.native720', 'title': 'Final native 720p 900-frame UVD decode', 'timeout_seconds': 240},
    'native1080-900': {'test_id': 'uvd.native1080', 'title': 'Final native 1080p 900-frame UVD decode', 'timeout_seconds': 240},
}


def smoke_steps(plan, terminal_scenario):
    require(isinstance(terminal_scenario, str) and terminal_scenario in TERMINAL_SCENARIOS,
            'Choose one fixed terminal UVD scenario')
    steps = [dict(step) for step in plan['smoke']['steps']]
    selected = TERMINAL_SCENARIOS[terminal_scenario]
    steps[-1].update(title=selected['title'], action='uvd.scenario',
                     parameters={'scenario': terminal_scenario}, timeout_seconds=selected['timeout_seconds'])
    return steps


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    path = Path(path)
    resolved = path.resolve(strict=True)
    require(not any(resolved.is_relative_to(root) for root in (Path('/proc'), Path('/sys'), Path('/dev'))), 'JSON input must not be a kernel/device pseudo-file')
    require(stat.S_ISREG(path.lstat().st_mode), 'JSON input must be a regular file, not a device or symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_size <= 1024 * 1024, 'JSON input must be a bounded regular file')
        raw = stream.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024, 'JSON input exceeds 1 MiB')
    return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))


def numeric(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def ordered(plan):
    remaining = {item['id']: item for item in plan['checks']}
    result, done = [], set()
    while remaining:
        ready = [key for key, item in remaining.items() if set(item['depends_on']) <= done]
        require(ready, 'Qualification dependency cycle')
        for key in ready:
            result.append(remaining.pop(key)); done.add(key)
    return result


def validate(plan):
    require(isinstance(plan, dict) and plan.get('schema_version') == 1, 'Invalid checklist schema')
    catalogue = read_json(HERE / 'catalogue.json')
    require(set(catalogue['statuses']) == STATUSES, 'Runner status conventions changed')
    runnable = {item['id'] for item in catalogue['tests'] if item['kind'] not in ('unavailable', 'deferred', 'blocked')}
    checks = plan.get('checks')
    require(isinstance(checks, list) and checks and all(isinstance(item, dict) for item in checks), 'Empty or invalid checklist')
    ids = [item.get('id') for item in checks]
    require(all(isinstance(key, str) and re.fullmatch(r'[a-z][a-z0-9_.-]+', key) for key in ids), 'Invalid check ID')
    require(len(ids) == len(set(ids)), 'Duplicate check ID')
    scenario_ids = set()
    for item in checks:
        require(all(isinstance(item.get(key), str) and item[key] for key in ('title', 'area', 'scope', 'basis', 'source', 'evidence')), 'Missing check explanation')
        require(item.get('mode') in MODES, 'Invalid execution mode')
        dependencies = item.get('depends_on')
        require(isinstance(dependencies, list) and len(dependencies) == len(set(dependencies))
                and set(dependencies) <= set(ids) and item['id'] not in dependencies, 'Invalid dependencies')
        bindings = item.get('automation')
        require(isinstance(bindings, list), 'Automation must be a list')
        require(item['mode'] != 'automated' or bindings, 'Automated check lacks an executable binding')
        require(item['mode'] not in ('manual', 'unimplemented', 'deferred') or not bindings, 'Nonexecutable check advertises automation')
        for binding in bindings:
            require(isinstance(binding, dict) and set(binding) == {'api', 'test_id'}, 'Only typed test bindings are allowed')
            available = runnable if binding['api'] == 'runner' else EXTRA_IDS.get(binding['api'], set())
            require(binding['test_id'] in available, 'Unknown or nonexecutable diagnostic binding')
        acceptance = item.get('acceptance', {})
        require(set(acceptance) == {'runs', 'cold_boots', 'seconds_per_run', 'metrics_minimum', 'metrics_maximum', 'oracles'}, 'Invalid acceptance fields')
        require(type(acceptance['runs']) is int and acceptance['runs'] >= 1
                and type(acceptance['cold_boots']) is int and 0 <= acceptance['cold_boots'] <= acceptance['runs']
                and numeric(acceptance['seconds_per_run']), 'Acceptance needs finite positive repetition/boot counts')
        require(isinstance(acceptance['metrics_minimum'], dict) and all(numeric(v) for v in acceptance['metrics_minimum'].values()), 'Invalid metric thresholds')
        require(isinstance(acceptance['metrics_maximum'], dict) and all(numeric(v) for v in acceptance['metrics_maximum'].values()), 'Invalid metric ceilings')
        require(isinstance(acceptance['oracles'], list) and acceptance['oracles']
                and all(isinstance(key, str) and key for key in acceptance['oracles']), 'Missing acceptance oracles')
        if 'scenarios' in item:
            scenarios = item['scenarios']
            require(item['area'] == 'UVD' and item.get('requires_uvd') is True
                    and isinstance(scenarios, list) and 1 <= len(scenarios) <= 8,
                    'Scenario details require an experimental UVD card')
            for scenario in scenarios:
                require(isinstance(scenario, dict) and set(scenario) == SCENARIO_FIELDS, 'Invalid scenario fields; executable commands are forbidden')
                require(all(isinstance(scenario[key], str) and 0 < len(scenario[key]) <= 4096
                            for key in SCENARIO_FIELDS - {'existing_test_id'}), 'Missing or oversized scenario explanation')
                key = scenario['id']
                require(re.fullmatch(r'uvd\.scenario\.[a-z0-9_.-]+', key) and key not in scenario_ids
                        and key not in ids, 'Invalid or duplicate scenario ID')
                scenario_ids.add(key)
                require(scenario['availability'] in SCENARIO_LABELS, 'Invalid scenario availability')
                if scenario['availability'] == 'available-alpha':
                    require(key == 'uvd.scenario.baseline128' and item['id'] == 'uvd.avc.exact'
                            and scenario['existing_test_id'] == 'uvd.avc',
                            'Only the existing alpha baseline action is available')
                else:
                    require(scenario['existing_test_id'] is None, 'An unimplemented scenario cannot advertise an executable test')
    ordered(plan)
    profiles = plan.get('profiles', {})
    require(isinstance(profiles, dict) and profiles, 'Missing qualification profiles')
    for profile in profiles.values():
        require('uvd_runtime' not in profile or type(profile['uvd_runtime']) is bool, 'Invalid profile applicability gate')
        required = profile.get('required', [])
        require(required and len(required) == len(set(required)) and set(required) <= set(ids), 'Invalid mandatory checklist')
        for item in checks:
            if item['id'] in required:
                require(set(item['depends_on']) <= set(required), 'Mandatory profile omits a dependency')
    smoke = plan.get('smoke')
    if smoke is not None:
        require(isinstance(smoke, dict) and smoke.get('schema_version') == 1
                and isinstance(smoke.get('steps'), list) and [step.get('id') for step in smoke['steps']] == list(SMOKE_IDS),
                'Smoke must keep the fixed foundation-to-terminal order')
        require(smoke.get('terminal_scenarios') == {'default': 'canonical-120', 'choices': list(TERMINAL_SCENARIOS)},
                'Smoke terminal choices must retain the fixed reviewed allowlist')
        for step in smoke['steps']:
            require(set(step) == {'id', 'title', 'kind', 'action', 'parameters', 'checks', 'timeout_seconds'}
                    and step['kind'] in ('foundation', 'observation', 'functional', 'browser', 'terminal')
                    and isinstance(step['parameters'], dict) and isinstance(step['title'], str)
                    and isinstance(step['checks'], list) and set(step['checks']) <= set(ids)
                    and type(step['timeout_seconds']) is int and 1 <= step['timeout_seconds'] <= 180,
                    'Invalid bounded smoke step')
            expected_kind = ('foundation' if step['id'] in ('cpu.compute', 'memory.pattern') else
                'browser' if step['id'].startswith('browser.') else 'terminal' if step['id'] == 'uvd.avc' else
                'observation' if step['id'] in ('inventory.readonly', 'ethernet.status', 'storage.inventory',
                    'audio.info', 'gpu.opengl.info', 'gpu.vulkan.info') else 'functional')
            require(step['kind'] == expected_kind, 'Smoke step cannot change its observation/browser/terminal behavior')
            if step['id'].startswith('browser.'):
                require(step['action'] == 'graphics.launch' and step['parameters'] == {
                    'app': 'browser' if step['id'] == 'browser.firefox' else 'chromium'}, 'Invalid fixed browser smoke action')
            elif step['id'] == 'wayland.integration':
                require(step['action'] == 'graphics.wayland' and step['parameters'] == {}, 'Invalid fixed Wayland action')
            elif step['kind'] == 'terminal':
                require(step['action'] == 'uvd.scenario' and step['parameters'] == {'scenario': 'canonical-120'}
                        and step['timeout_seconds'] == 150, 'Invalid default terminal UVD scenario')
            else:
                require(step['action'] == 'test.run' and set(step['parameters']) == {'test_id', 'parameters'}
                        and step['parameters']['test_id'] == step['id'] and isinstance(step['parameters']['parameters'], dict),
                        'Smoke only permits fixed typed catalogue/reference tests')
        require([step['id'] for step in smoke['steps'] if step['kind'] == 'foundation'] == ['cpu.compute', 'memory.pattern']
                and [step['id'] for step in smoke['steps'] if step['kind'] == 'terminal'] == ['uvd.avc'],
                'Smoke requires CPU/RAM first and one final UVD attempt')
    return plan


def scope_id(scope):
    return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_scope(scope):
    require(isinstance(scope, dict) and set(scope) == SCOPE_FIELDS, 'Supply the complete model/build scope')
    require(all(isinstance(scope[key], str) and scope[key].strip() for key in ('console_model', 'southbridge', 'boot_chain', 'kernel_release')), 'Missing model/boot/build identity')
    require(scope['southbridge'] == 'Baikal', 'This alpha checklist is scoped to Baikal; other families need their own plan')
    require(type(scope['uvd_runtime']) is bool, 'uvd_runtime must be an explicit boolean')
    for key in (*HASH_FIELDS, 'mesa_manifest_sha256', 'firmware_manifest_sha256'):
        value = scope[key]
        require((key not in HASH_FIELDS and value is None) or isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value), 'Invalid artifact hash: ' + key)


def evidence_records(document, ids):
    require(isinstance(document, dict) and set(document) == {'schema_version', 'scope', 'checks'} and document['schema_version'] == 1, 'Invalid evidence summary schema')
    validate_scope(document['scope'])
    require(isinstance(document['checks'], list), 'Evidence checks must be a list')
    records, run_ids = {}, set()
    for record in document['checks']:
        require(isinstance(record, dict) and set(record) == {'id', 'status', 'reviewed_by', 'evidence', 'passed_oracles', 'runs'}, 'Expected a reviewed qualification summary, not a raw runner report')
        require(record['id'] in ids and record['id'] not in records and record['status'] in STATUSES, 'Unknown, duplicate or invalid evidence status')
        require(isinstance(record['reviewed_by'], str) and isinstance(record['evidence'], list)
                and all(isinstance(ref, str) and ref.strip() for ref in record['evidence'])
                and isinstance(record['passed_oracles'], list) and all(isinstance(key, str) for key in record['passed_oracles']), 'Invalid review/evidence fields')
        require(isinstance(record['runs'], list), 'Runs must be a list')
        for run in record['runs']:
            require(isinstance(run, dict) and set(run) == {'run_id', 'boot_id', 'cold_boot', 'scope_id', 'status', 'seconds', 'uart_continuity', 'metrics'}, 'Invalid run summary')
            require(all(isinstance(run[key], str) and run[key] for key in ('run_id', 'boot_id', 'scope_id')), 'Missing run identity')
            require(run['run_id'] not in run_ids, 'Duplicate run identity cannot count as repetition')
            run_ids.add(run['run_id'])
            require(type(run['cold_boot']) is bool and run['status'] in STATUSES and numeric(run['seconds'])
                    and run['uart_continuity'] in ('completed', 'invalid', 'not_recorded')
                    and isinstance(run['metrics'], dict) and all(numeric(value) for value in run['metrics'].values()), 'Invalid run observations')
        records[record['id']] = record
    return records


def evaluate(plan, profile_id, document=None):
    require(profile_id in plan['profiles'], 'Unknown qualification profile')
    records = evidence_records(document, {item['id'] for item in plan['checks']}) if document is not None else {}
    scope = document['scope'] if document else None
    fingerprint = scope_id(scope) if scope else None
    required = set(plan['profiles'][profile_id]['required'])
    scope_blockers = []
    expected_uvd = plan['profiles'][profile_id].get('uvd_runtime')
    if scope and expected_uvd is not None and scope['uvd_runtime'] != expected_uvd:
        scope_blockers.append('Experimental retained-DMA UVD scope cannot qualify an ordinary profile.' if not expected_uvd
                              else 'This profile requires the separate UVD scope; ordinary-core decoding is unsupported, not failed.')
    results = {}
    for item in ordered(plan):
        record = records.get(item['id'])
        status = 'DEFERRED' if item['mode'] == 'deferred' else 'BLOCKED' if item['mode'] == 'unimplemented' else 'NOT_RUN'
        reasons = []
        if item.get('requires_uvd') and scope and not scope['uvd_runtime']:
            status = 'UNSUPPORTED'; reasons.append('Ordinary core UVD flag is false; decoding was not tested, not failed.')
        elif record:
            status = record['status']
            if status == 'PASS':
                acceptance = item['acceptance']
                good = [run for run in record['runs'] if run['status'] == 'PASS' and run['scope_id'] == fingerprint
                        and run['uart_continuity'] == 'completed' and run['seconds'] >= acceptance['seconds_per_run']
                        and all(run['metrics'].get(key, -1) >= value for key, value in acceptance['metrics_minimum'].items())
                        and all(run['metrics'].get(key, float('inf')) <= value for key, value in acceptance['metrics_maximum'].items())]
                cold = {run['boot_id'] for run in good if run['cold_boot']}
                if item['mode'] in ('unimplemented', 'deferred'):
                    reasons.append('This checklist marks the operation unimplemented/deferred; a PASS cannot enable it.')
                if not record['reviewed_by'].strip() or not record['evidence']:
                    reasons.append('Human reviewer and evidence references are required.')
                if not set(acceptance['oracles']) <= set(record['passed_oracles']):
                    reasons.append('Required oracles were not reviewed.')
                if len(good) < acceptance['runs'] or len(cold) < acceptance['cold_boots']:
                    reasons.append('Insufficient matching completed-UART runs, cold boots, duration or metrics.')
                if item.get('requires_uvd') and not scope['firmware_manifest_sha256']:
                    reasons.append('The experimental UVD firmware manifest is not pinned.')
                if item.get('requires_mesa') and not scope['mesa_manifest_sha256']:
                    reasons.append('The actual graphics stack manifest is not pinned.')
                if reasons:
                    status = 'BLOCKED'
        missing = [key for key in item['depends_on'] if results[key]['status'] != 'PASS']
        if status == 'PASS' and missing:
            status = 'BLOCKED'; reasons.append('Dependencies have not met their gates: ' + ', '.join(missing))
        results[item['id']] = {'id': item['id'], 'title': item['title'], 'area': item['area'], 'mode': item['mode'],
                               'required': item['id'] in required, 'status': status, 'reasons': reasons}
    blockers = [key for key in required if results[key]['status'] != 'PASS']
    if scope_blockers:
        blockers.append('profile.scope')
    return {'schema_version': 1, 'profile': profile_id, 'scope': scope, 'scope_id': fingerprint,
            'status': 'BLOCKED' if blockers else 'INCONCLUSIVE', 'ready_for_human_review': not blockers,
            'hardware_acceptance': False, 'stability_claim': False, 'mandatory_blockers': sorted(blockers),
            'profile_blockers': scope_blockers, 'checks': list(results.values()), 'limitations': plan['limitations']}


def render(plan, report):
    profile = plan['profiles'][report['profile']]
    lines = ['PS4 qualification checklist (alpha)', profile['title'],
             'Read-only plan/evidence review. No tests or hardware actions are executed.',
             'Counts beyond the cited finish plan are proposals; no automatic STABLE verdict.', '']
    lines.append('Availability: ' + ', '.join(f"{sum(item['mode'] == mode for item in plan['checks'])} {mode}" for mode in sorted(MODES)) + '.')
    lines.extend('Profile scope blocker: ' + reason for reason in report['profile_blockers'])
    definitions = {item['id']: item for item in plan['checks']}
    for item in report['checks']:
        definition = definitions[item['id']]; gate = definition['acceptance']
        lines.append(f"[{item['status']}] {'REQUIRED' if item['required'] else 'optional'} {item['id']} — {item['title']}")
        details = [f"{item['mode']}; {definition['scope']}",
                   f"Gate: {gate['runs']} runs, {gate['cold_boots']} cold boots, {gate['seconds_per_run']} s/run; {definition['basis']}.",
                   'Oracles: ' + ', '.join(gate['oracles']), 'Evidence: ' + definition['evidence']]
        if gate['metrics_minimum']: details.append('Minimum per-run metrics: ' + ', '.join(f'{key}={value}' for key, value in gate['metrics_minimum'].items()))
        if gate['metrics_maximum']: details.append('Maximum per-run metrics: ' + ', '.join(f'{key}={value}' for key, value in gate['metrics_maximum'].items()))
        if definition['automation']: details.append('Existing tools (not invoked): ' + ', '.join(binding['api'] + ':' + binding['test_id'] for binding in definition['automation']))
        if definition['depends_on']: details.append('After: ' + ', '.join(definition['depends_on']))
        details.extend(item['reasons'])
        for detail in details: lines.extend(textwrap.wrap(detail, width=96, initial_indent='  ', subsequent_indent='  '))
        for scenario in definition.get('scenarios', []):
            lines.append('  Scenario: ' + scenario['title'])
            values = [('Availability', SCENARIO_LABELS[scenario['availability']])]
            if scenario['existing_test_id']:
                values.append(('Existing action', scenario['existing_test_id'] + ' (not invoked)'))
            values.extend((label, scenario[key]) for key, label in (
                ('fixture', 'Fixture'), ('preparation', 'Prepare'), ('sequence', 'Sequence'),
                ('evidence', 'Evidence'), ('limits', 'Limits')))
            for label, value in values:
                lines.extend(textwrap.wrap(label+': '+value, width=96, initial_indent='    ', subsequent_indent='    '))
        lines.append('')
    lines.append(f"Mandatory blockers: {len(report['mandatory_blockers'])}. " + ('Evidence is ready for human review; no stability claim.' if report['ready_for_human_review'] else 'Qualification is incomplete.'))
    lines.extend('Scope limit: ' + limit for limit in plan['limitations'])
    return '\n'.join(lines)


class ControllerIPC:
    """One bounded local request; no TCP, shell or credentials API."""
    def __init__(self, path=CONTROL):
        self.path, self.identifier = path, 0

    def __call__(self, action, params=None):
        require(action in ('state', 'results.list', 'job.get', 'test.run', 'graphics.launch', 'graphics.wayland', 'uvd.scenario'),
                'Action is outside the smoke allowlist')
        self.identifier += 1
        request = json.dumps({'id': self.identifier, 'action': action, 'params': params or {}}, allow_nan=False).encode()+b'\n'
        require(len(request) <= 32768, 'IPC request exceeds its bound')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(8)
            connection.connect(str(self.path)); connection.sendall(request)
            with connection.makefile('rb') as stream:
                raw = stream.readline(1024*1024+1)
        require(len(raw) <= 1024*1024 and raw.endswith(b'\n'), 'IPC response is incomplete or oversized')
        response = json.loads(raw)
        require(isinstance(response, dict) and response.get('id') == self.identifier, 'IPC response identity differs')
        require(response.get('ok') is True, str(response.get('error', 'Controller refused the action'))[:1000])
        return response.get('data')


def saved_report(run_id):
    require(isinstance(run_id, str) and RUN_ID.fullmatch(run_id), 'Invalid saved report identity')
    path = RESULTS/run_id/'result.json'
    require(path.resolve() == path, 'Saved report path contains a symlink')
    return read_json(path)


def same_identity(record, identity):
    return isinstance(record, dict) and isinstance(record.get('identity'), dict) and all(
        record['identity'].get(key) == identity.get(key) for key in IDENTITY_PINS)


def foundation_pass(record, identity, versions):
    if not isinstance(record, dict): return False
    version = versions.get(record.get('test_id'))
    if (record.get('test_id') not in ('cpu.compute', 'memory.pattern') or not same_identity(record, identity)
            or record.get('phase') != 'completed' or record.get('status') != 'PASS'
            or record.get('stop_required') or record.get('hardware_acceptance') is not False
            or type(version) is not int or version < 1 or record.get('test_version') != version):
        return False
    parameters, evidence, commands = record.get('parameters', {}), record.get('evidence', {}), record.get('commands', [])
    if not isinstance(parameters, dict) or not isinstance(evidence, dict) or not isinstance(commands, list): return False
    if record.get('test_id') == 'cpu.compute':
        oracle = (set(parameters) == {'seconds'} and type(parameters['seconds']) is int and 1 <= parameters['seconds'] <= 10
            and evidence.get('digest') == '3951d9a412ce4c815eada76de213f560647cd60d4c37896cbcdcb899b645f197'
            and type(evidence.get('iterations')) is int and evidence['iterations'] > 0)
    else:
        oracle = (set(parameters) == {'memory_mib'} and type(parameters['memory_mib']) is int
            and 1 <= parameters['memory_mib'] <= 128 and evidence.get('verified_mib') == parameters['memory_mib']
            and evidence.get('patterns') == [0, 85, 170, 255])
    return bool(oracle and len(commands) == 1 and all(isinstance(command, dict) and command.get('returncode') == 0
        and command.get('signal') is None and all(command.get(key) is False for key in
            ('timed_out', 'interrupted', 'output_limited')) for command in commands))


def chromium_binding(identity):
    """Inspect the installed fixed helper, never import/execute it to test a pin."""
    paths = (Path('/run/psx-frontend/prepare-chromium-provider'), Path('/usr/lib/psx-diagnostics/prepare-chromium-provider'))
    source = next((path for path in paths if path.is_file()), None)
    if source is None: return 'Chromium preparation helper is not installed.'
    try:
        require(source.resolve() == source and source.stat().st_size <= 128*1024, 'Provider helper is unsafe or oversized')
        tree = ast.parse(source.read_text())
        expected = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'EXPECTED' for target in node.targets))
        declared = identity.get('declared_artifacts', {})
        require(isinstance(expected, dict) and expected.get('kernel_release') == identity['kernel_release']
                and expected.get('base_rootfs_sha256') == declared.get('rootfs_sha256'),
                'Installed Chromium provider is bound to another kernel/root; prepare matching alpha4 helpers/provider.')
    except (OSError, ValueError, KeyError, StopIteration, SyntaxError) as error:
        return str(error)[:1000]
    return None


def smoke_coverage(plan, steps):
    results = {step['id']: step for step in steps}
    coverage = []
    for card in plan['checks']:
        ids = [step['id'] for step in plan['smoke']['steps'] if card['id'] in step['checks']]
        attached = [results[key] for key in ids]
        status = ('DEFERRED' if card['mode'] == 'deferred' else 'BLOCKED' if card['mode'] == 'unimplemented' else 'NOT_RUN')
        if attached:
            status = next((value for value in ('FAIL', 'BLOCKED', 'UNSUPPORTED', 'NOT_RUN', 'INCONCLUSIVE', 'DEGRADED')
                           if any(item['status'] == value for item in attached)), 'PASS')
        coverage.append({'id': card['id'], 'title': card['title'], 'area': card['area'], 'mode': card['mode'],
            'execution_status': status, 'qualification_status': 'NOT_EVALUATED', 'smoke_steps': ids,
            'run_ids': [item['run_id'] for item in attached if item.get('run_id')],
            'reason': ('One smoke component only; repetition, duration and human qualification oracles remain unchanged.' if attached
                       else card['scope'])})
    return coverage


def run_smoke(plan, rpc, load_report=saved_report, *, https_url=None, interface='psx0', audio_fixture=None,
              audio_device='hw:0,3', terminal_scenario='canonical-120', provider_check=chromium_binding, save=lambda _: None,
              clock=time.monotonic, sleep=time.sleep):
    """Sequential finite actions. Any foundation failure or stop latch halts active work."""
    steps = [dict(step, status='NOT_RUN', executed=False, message='Not reached') for step in smoke_steps(plan, terminal_scenario)]
    report = {'schema_version': 1, 'suite_id': plan['smoke']['suite_id'], 'run_id': uuid.uuid4().hex,
        'phase': 'running', 'status': 'INCONCLUSIVE', 'hardware_acceptance': False, 'stability_claim': False,
        'terminal_scenario': terminal_scenario, 'steps': steps, 'coverage_gaps': plan['smoke']['coverage_gaps'],
        'started_at': datetime.now(timezone.utc).isoformat(), 'halt_reason': None}
    def checkpoint():
        report['coverage'] = smoke_coverage(plan, steps)
        report['terminal_options'] = [{'scenario': name, 'test_id': value['test_id'], 'selected': name == terminal_scenario,
            'status': steps[-1]['status'] if name == terminal_scenario else 'NOT_RUN',
            'reason': steps[-1]['message'] if name == terminal_scenario else 'Not selected; no additional decoder attempt'}
            for name, value in TERMINAL_SCENARIOS.items()]
        save(report)
    def wait_job(action, params, timeout):
        job = rpc(action, params)
        require(isinstance(job, dict) and isinstance(job.get('id'), str)
                and re.fullmatch(r'[a-f0-9]{32}', job['id']), 'Controller did not return a typed job ID')
        identifier, deadline = job['id'], clock()+timeout
        while clock() < deadline:
            current = rpc('job.get', {'id': identifier})
            require(isinstance(current, dict) and current.get('id') == identifier, 'Job identity changed')
            if current.get('state') == 'failed': raise ValueError(str(current.get('error', 'Job failed'))[:1000])
            if current.get('state') == 'completed':
                require(isinstance(current.get('result'), dict), 'Completed job has no result')
                return current['result']
            require(current.get('state') == 'running', 'Unknown job state')
            sleep(.25)
        raise ValueError('Job deadline expired; it may still be active. No cancel/reset/retry was attempted.')
    def wait_browser(app, before, timeout):
        deadline = clock()+timeout
        while clock() < deadline:
            candidates = [value for value in rpc('results.list', {}) if value.get('test_id') == 'browser.batch'
                          and value.get('run_id') not in before and value.get('boot_id') == identity['boot_id']]
            matching = []
            for candidate in candidates:
                value = load_report(candidate['run_id'])
                require(isinstance(value, dict) and value.get('run_id') == candidate['run_id'],
                        'Browser batch report path/identity differs')
                if value.get('browser_app') == app: matching.append(value)
            require(len(matching) <= 1, 'Ambiguous automatic browser batch; no other action was started')
            if matching and matching[0].get('phase') == 'completed':
                batch = matching[0]
                require(same_identity(batch, identity) and batch.get('automatic') is True
                        and not batch.get('usb_save_failed'),
                        'Browser batch is not durably saved for this boot')
                require(not batch.get('close_error'), 'Browser service did not confirm closure: ' + str(batch.get('close_error'))[:1000])
                if batch.get('closed') is not True:
                    # Controller saves completed before stopping the service,
                    # then persists closed=True. Poll the same batch only.
                    require(batch.get('closed') is False, 'Browser batch has no valid closure state')
                    sleep(.25)
                    continue
                child_ids = batch.get('run_ids', [])
                require(isinstance(child_ids, list) and len(child_ids) <= 5 and len(child_ids) == len(set(child_ids)),
                        'Browser child identities are invalid or duplicated')
                children = [load_report(key) for key in child_ids]
                require(all(isinstance(value, dict) for value in children), 'Browser child report is not an object')
                expected = ['browser.capabilities', 'browser.canvas2d', 'browser.webgl', 'browser.animation', 'browser.video']
                require(len(children) <= 5 and [value.get('test_id') for value in children] == expected[:len(children)]
                    and all(value.get('run_id') == key and same_identity(value, identity)
                            and value.get('batch_id') == batch['run_id'] and value.get('phase') == 'completed'
                            for key, value in zip(child_ids, children)), 'Saved browser child sequence/identity differs')
                if batch.get('status') == 'PASS':
                    require(len(children) == 5 and batch.get('completed_count') == batch.get('expected_count') == 5
                            and all(value.get('status') == 'PASS' for value in children), 'Browser PASS lacks five durable child results')
                webgl = next((value for value in children if value.get('test_id') == 'browser.webgl'), {})
                batch = dict(batch, webgl_renderer=webgl.get('renderer', {}).get('classification', 'unknown'),
                             video_hardware_decode_proven=False)
                return batch
            sleep(.25)
        raise ValueError('Automatic browser completion deadline expired; controller watchdog/markers remain authoritative.')
    checkpoint()
    try:
        state = rpc('state', {})
        identity = state.get('identity', {})
        report['identity'] = identity
        require(identity.get('release_matches') is True and identity.get('manifest_available') is True
                and all(isinstance(identity.get(key), str) and identity[key] for key in IDENTITY_PINS),
                'Verified same-boot diagnostic identity is missing')
        require(not any(job.get('state') == 'running' for job in state.get('jobs', [])), 'Another diagnostic job is already active')
        history = rpc('results.list', {})
        require(isinstance(history, list) and len(history) <= 200, 'Result history is invalid or oversized')
        require(not any(value.get('boot_id') == identity['boot_id'] and value.get('stop_required') for value in history),
                'A current-boot stop/UVD intent is already recorded; no active smoke work is allowed')
        versions = {value['id']: value.get('version') for value in state.get('tests', [])}
        for step in steps:
            if report['halt_reason']:
                step.update(status='BLOCKED', message=report['halt_reason']); continue
            try:
                current = rpc('state', {}).get('identity', {})
                require(all(current.get(key) == identity[key] for key in IDENTITY_PINS), 'Boot/artifact identity changed')
                step.update(message='Preflight', phase='preflight'); checkpoint()
                if step['kind'] == 'foundation':
                    prior = sorted((value for value in history if value.get('test_id') == step['id']
                                    and value.get('boot_id') == identity['boot_id'] and not value.get('prerequisite_blocked')),
                                   key=lambda value: value.get('started_at', ''), reverse=True)
                    if prior:
                        value = load_report(prior[0]['run_id'])
                        require(isinstance(value, dict) and value.get('test_id') == step['id']
                                and value.get('run_id') == prior[0]['run_id']
                                and foundation_pass(value, identity, versions),
                                'Existing core receipt needs review; no automatic retry: '+step['id'])
                        step.update(status='PASS', run_id=value['run_id'], reused=True, message='Validated matching same-boot core receipt reused')
                        checkpoint(); continue
                if step['id'] == 'ethernet.https' and not https_url:
                    step.update(status='NOT_RUN', message='Supply --https-url for an explicit interface-bound transfer; no connection/reset was attempted')
                    checkpoint(); continue
                if step['id'] == 'browser.chromium':
                    reason = provider_check(identity)
                    if reason:
                        step.update(status='BLOCKED', message=reason); checkpoint(); continue
                if step['kind'] == 'terminal' and identity.get('declared_artifacts', {}).get('uvd_runtime') is not True:
                    step.update(status='UNSUPPORTED', message='Ordinary UVD flag is false; no decode was attempted or failed')
                    checkpoint(); continue
                params = json.loads(json.dumps(step['parameters']))
                if step['id'].startswith('ethernet.'):
                    params['parameters']['interface'] = interface
                    if step['id'] == 'ethernet.https': params['parameters']['url'] = https_url
                if step['id'] == 'audio.44100':
                    params['parameters']['audio_device'] = audio_device
                    if audio_fixture: params['parameters']['fixture'] = audio_fixture
                before = {value.get('run_id') for value in rpc('results.list', {})} if step['kind'] == 'browser' else set()
                step.update(executed=True, phase='submitted', message='Waiting for the fixed controller action'); checkpoint()
                value = wait_job(step['action'], params, step['timeout_seconds'])
                if step['kind'] == 'browser':
                    require(value.get('automatic_suite') is True,
                            'Automatic browser launch was not confirmed; no later action was started')
                    value = wait_browser(params['app'], before, step['timeout_seconds'])
                if value.get('run_id') and not isinstance(value.get('identity'), dict): value = load_report(value['run_id'])
                require(isinstance(value.get('run_id'), str) and RUN_ID.fullmatch(value['run_id'])
                        and value.get('phase') == 'completed' and same_identity(value, identity),
                        'Completed result does not match the selected boot/artifacts')
                require(value.get('status') in STATUSES, 'Action returned an invalid result status')
                if step['kind'] == 'terminal':
                    require(value.get('test_id') == TERMINAL_SCENARIOS[terminal_scenario]['test_id'],
                            'Terminal result does not match the selected fixed scenario')
                step.update(status=value['status'], phase='completed', message=str(value.get('message', 'Result saved'))[:1000],
                            run_id=value.get('run_id'), stop_required=bool(value.get('stop_required')),
                            observation_required=value.get('observation_required'),
                            webgl_renderer=value.get('webgl_renderer'), video_hardware_decode_proven=False)
                if step['kind'] == 'foundation' and (value.get('test_id') != step['id'] or not foundation_pass(value, identity, versions)):
                    if value['status'] == 'PASS': step['status'] = 'BLOCKED'
                    report['halt_reason'] = 'Core computation/memory oracle did not PASS; downstream actions stopped: '+step['id']
                    checkpoint(); continue
                if step['kind'] == 'browser' and value.get('webgl_renderer') != 'hardware':
                    step['message'] += '; WebGL renderer '+str(value.get('webgl_renderer', 'unknown'))+'; no hardware/UVD acceptance'
                if value.get('stop_required') or value['status'] == 'FAIL':
                    report['halt_reason'] = 'Final UVD attempt completed; only evidence collection/separate authorized power remains' if step['kind'] == 'terminal' else 'Stop required after '+step['id']+': '+step['message']
            except (OSError, ValueError, KeyError, TypeError) as error:
                step.update(status='BLOCKED', phase='stopped', message=str(error)[:1000])
                report['halt_reason'] = step['message']
            checkpoint()
    except (OSError, ValueError, KeyError, TypeError) as error:
        report['halt_reason'] = str(error)[:1000]
        for step in steps:
            if not step['executed']: step.update(status='BLOCKED', message=report['halt_reason'])
    report.update(phase='completed', completed_at=datetime.now(timezone.utc).isoformat(),
                  status='FAIL' if any(step['status'] == 'FAIL' for step in steps) else 'BLOCKED' if any(
                      step['status'] == 'BLOCKED' for step in steps) else 'INCONCLUSIVE')
    checkpoint()
    return report


class SmokeWriter:
    def __init__(self, path):
        self.path = Path(path).absolute()
        require(self.path.parent.resolve() == self.path.parent and self.path.parent.is_dir()
                and not any(self.path.is_relative_to(root) for root in (Path('/dev'), Path('/proc'), Path('/sys'))),
                'Smoke output needs an existing canonical ordinary-file directory')
        self.fd = os.open(self.path, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
        os.close(self.fd)

    def __call__(self, report):
        raw = (json.dumps(report, indent=2, allow_nan=False)+'\n').encode()
        require(len(raw) <= 1024*1024, 'Smoke summary exceeds its 1 MiB bound')
        temporary = self.path.with_name('.smoke-'+uuid.uuid4().hex+'.tmp')
        fd = os.open(temporary, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        fd = os.open(self.path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)


def render_smoke(report):
    lines = ['Diagnostix hardware smoke — one boot; qualification gates remain separate.']
    for step in report['steps']:
        lines.append('['+step['status']+'] '+step['id']+' — '+step['message'])
    lines.extend('Coverage gap: '+value['id']+' — '+value['reason'] for value in report['coverage_gaps'])
    lines.append('Overall: '+report['status']+'. No stability or hardware-acceptance promotion.')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='kernel-baseline', help='kernel-baseline (default), desktop-integration or uvd-experimental')
    parser.add_argument('--evidence', type=Path, help='Read a reviewed summary JSON; never run referenced tests')
    parser.add_argument('--json', action='store_true', help='Print checklist and assessment as JSON')
    parser.add_argument('--validate', action='store_true', help='Validate source schema, bindings and dependency order only')
    parser.add_argument('--smoke', action='store_true', help='Plan the ordered fixed-action hardware smoke; add --run to execute')
    parser.add_argument('--run', action='store_true', help='Explicitly submit the smoke actions to the local root controller')
    parser.add_argument('--output', type=Path, help='New private JSON file, required for --smoke --run; saved before every action')
    parser.add_argument('--https-url', help='Explicit HTTPS transfer target; without it that step reports NOT_RUN')
    parser.add_argument('--interface', default='psx0')
    parser.add_argument('--audio-fixture', help='Optional installed 44.1 kHz PCM WAV; otherwise use the manifest fixture')
    parser.add_argument('--audio-device', default='hw:0,3')
    parser.add_argument('--terminal-scenario', choices=tuple(TERMINAL_SCENARIOS), default='canonical-120',
                        help='Replace the one final UVD step with this fixed scenario; never runs an additional decoder')
    args = parser.parse_args(argv)
    if args.run and (not args.smoke or args.output is None): parser.error('--run requires --smoke and a new --output file')
    if args.smoke and args.evidence: parser.error('Smoke execution and reviewed qualification evidence are separate modes')
    if not args.smoke and args.terminal_scenario != 'canonical-120': parser.error('--terminal-scenario requires --smoke')
    try:
        plan = validate(read_json(PLAN))
        if args.validate:
            print(json.dumps({'valid': True, 'checks': len(plan['checks']), 'profiles': list(plan['profiles'])}) if args.json else f"Valid checklist: {len(plan['checks'])} checks; dependency graph and existing test bindings verified.")
            return 0
        if args.smoke:
            require('smoke' in plan, 'No fixed smoke definition is installed')
            if args.https_url:
                parsed = urlsplit(args.https_url)
                require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                        and not parsed.fragment and len(args.https_url) <= 2048, 'Use an explicit bounded HTTPS URL without credentials/fragment')
            require(re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', args.interface), 'Invalid interface name')
            if args.run:
                require(os.geteuid() == 0, 'Hardware smoke requires the root Unix controller; no TCP fallback')
                report = run_smoke(plan, ControllerIPC(), https_url=args.https_url, interface=args.interface,
                    audio_fixture=args.audio_fixture, audio_device=args.audio_device,
                    terminal_scenario=args.terminal_scenario, save=SmokeWriter(args.output))
            else:
                steps = [dict(step, status='NOT_RUN', message='Plan only; --run required') for step in smoke_steps(plan, args.terminal_scenario)]
                report = {'schema_version': 1, 'status': 'NOT_RUN', 'phase': 'plan', 'steps': steps,
                    'terminal_scenario': args.terminal_scenario,
                    'hardware_acceptance': False, 'stability_claim': False, 'coverage': smoke_coverage(plan, steps),
                    'coverage_gaps': plan['smoke']['coverage_gaps']}
            print(json.dumps(report, indent=2, allow_nan=False) if args.json else render_smoke(report))
            return 0 if not args.run else 2 if report['status'] in ('FAIL', 'BLOCKED') else 0
        report = evaluate(plan, args.profile, read_json(args.evidence) if args.evidence else None)
        print(json.dumps({'checklist': plan, 'assessment': report}, indent=2, allow_nan=False) if args.json else render(plan, report))
        return 2 if args.evidence and report['mandatory_blockers'] else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('qualification: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

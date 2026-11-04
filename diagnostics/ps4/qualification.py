#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read-only PS4 qualification checklist. Never runs tests or operates hardware."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import textwrap

HERE = Path(__file__).resolve().parent
PLAN = HERE / 'qualification.json'
STATUSES = {'PASS', 'FAIL', 'DEGRADED', 'NOT_RUN', 'UNSUPPORTED', 'DEFERRED', 'BLOCKED', 'INCONCLUSIVE'}
MODES = {'automated', 'assisted', 'manual', 'unimplemented', 'deferred'}
EXTRA_IDS = {'controller': {'gpu.opengl.render', 'gpu.vulkan.render'},
             'browser': {'browser.capabilities', 'browser.canvas2d', 'browser.webgl', 'browser.animation', 'browser.video'}}
HASH_FIELDS = ('kernel_sha256', 'config_sha256', 'modules_sha256', 'initramfs_sha256', 'rootfs_sha256')
SCOPE_FIELDS = {'console_model', 'southbridge', 'boot_chain', 'kernel_release', 'uvd_runtime',
                'mesa_manifest_sha256', 'firmware_manifest_sha256', *HASH_FIELDS}


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
        lines.append('')
    lines.append(f"Mandatory blockers: {len(report['mandatory_blockers'])}. " + ('Evidence is ready for human review; no stability claim.' if report['ready_for_human_review'] else 'Qualification is incomplete.'))
    lines.extend('Scope limit: ' + limit for limit in plan['limitations'])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='kernel-baseline', help='kernel-baseline (default), desktop-integration or uvd-experimental')
    parser.add_argument('--evidence', type=Path, help='Read a reviewed summary JSON; never run referenced tests')
    parser.add_argument('--json', action='store_true', help='Print checklist and assessment as JSON')
    parser.add_argument('--validate', action='store_true', help='Validate source schema, bindings and dependency order only')
    args = parser.parse_args(argv)
    try:
        plan = validate(read_json(PLAN))
        if args.validate:
            print(json.dumps({'valid': True, 'checks': len(plan['checks']), 'profiles': list(plan['profiles'])}) if args.json else f"Valid checklist: {len(plan['checks'])} checks; dependency graph and existing test bindings verified.")
            return 0
        report = evaluate(plan, args.profile, read_json(args.evidence) if args.evidence else None)
        print(json.dumps({'checklist': plan, 'assessment': report}, indent=2, allow_nan=False) if args.json else render(plan, report))
        return 2 if args.evidence and report['mandatory_blockers'] else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('qualification: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

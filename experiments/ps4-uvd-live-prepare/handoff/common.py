#!/usr/bin/env python3
"""Pinned host-file validation for preparation handoff; no device discovery."""
import importlib.util
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[3]
TARGET = '7.2-ps4-baikal-uvd-live-prepare-dev1'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1'
PATCH = '9cc51da26d81342d2fd3cc674a4495cbdc4298350059cdeb0eeba9d744693456'
BUNDLE_TOOL_SHA = '0d8b80d479c2eeda8c498a3ed450354bac6c8710c1ae0962712d6b0da8dbd69b'
FROZEN = {
    'bundle_manifest': 'bc47184fabd1cca0a3fb5fdc6204dcaceabbddff67902a9eb39290409659de05',
    'compiled_review': '13e4837544a6ea7faade1bf932d7da2f739c1909df1fb3cdb3f88adf649c09b4',
    'source_review': '9d65b2adcae90b3da3c9702c3d87c7f44c315ef3220dd781b2691904f2e82c3d',
    'build': '99ffabe51420204296b1cd3fa6a16877e05bf5bd082db5cc23e49934aa017c16',
    'package': '7a3fbd35e488c378703bafdee72080b8d679039f1aa403c825b570aa2e3297db',
    'kernel': 'e7abc9a8f4c5d4d19ce12dbab2df9f113a137c61f67497ab5c00ccb31a74cb4f',
    'config': '16f041910342b089da805e6b390430f7b577438b18c713d9cb6de496044ca3a1',
    'bootargs': '3f6381594c3f3b080728aa894a8c2aabd28651dcfb5541aee83aff27bcdcded1',
    'compatibility': '8e7c3b89bd59be8a9eeb9f30564433c551a6ed8211efd5be6ba05d546be507e7',
}
TOOL_NAMES = ('common.py', 'transaction.py', 'install_usb.py', 'prepare_stage.py', 'run_test.py', 'rollback.json', 'install-source.json')
ISOLATION = {'usb': 'attached-to-Omarchynator', 'console': ['not-observed', 'orbis-idle', 'fully-off'],
             'console_power_observation_required': False, 'operator_holds_linux_launch': True,
             'fresh_full_poweroff_required_for_post_test_rollback': True}
OBSERVATION = {'launch_count': 1, 'launch_timeout_seconds': 180, 'full_poweroff_before_launch': True,
               'direct_userspace_evidence_required': True, 'completed_uart_continuity_required': True,
               'cached_status_only': True, 'uvd_arm_implemented': False, 'hardware_acceptance': False}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


t = module('preparation_transaction', HERE / 'transaction.py')
t.require(t.sha(t.regular_bytes(HERE.parent / 'bundle.py')) == BUNDLE_TOOL_SHA, 'Frozen bundle validator changed')
b = module('preparation_bundle_validator', HERE.parent / 'bundle.py')
require, raw, sha = t.require, b.read, t.sha
load_json, file_sha, digest, write, save = b.load, b.sha, b.digest, b.write_new, b.save


def experiment(value):
    require(isinstance(value, str) and re.fullmatch(r'EXP-\d{8}-[0-9]{3}', value) and not value.endswith('-000'), 'Invalid experiment identifier')
    import datetime
    datetime.datetime.strptime(value[4:12], '%Y%m%d')
    return value


def tool_pins():
    return {**{name: file_sha(HERE / name) for name in TOOL_NAMES}, '../bundle.py': file_sha(HERE.parent / 'bundle.py')}


def contract_tools(contract):
    require(contract.get('schema') == 1 and contract.get('status') == 'DATA_ONLY_OBSERVATION_CONTRACT', 'Observation contract missing/not data-only')
    require(contract.get('release') == RELEASE and contract.get('bootargs_sha256') == FROZEN['bootargs'], 'Observation identity differs')
    require(contract.get('scope') == OBSERVATION, 'Observation scope differs')
    rows = contract.get('tools', {})
    require({'acquisition', 'evaluator', 'result_binding'} <= set(rows), 'Acquisition/evaluator/result-binding tool pins required')
    paths = {}
    for role, row in rows.items():
        require(re.fullmatch('[a-z][a-z0-9_]{0,63}', role) and isinstance(row, dict) and set(row) == {'path', 'sha256'} and digest(row['sha256']), 'Invalid explicit observation tool pin')
        path = Path(row['path'])
        if not path.is_absolute():
            require(all(x not in ('', '.', '..') for x in row['path'].split('/')), 'Unsafe observation tool reference')
            path = WORKSPACE / path
        path = b.safe_path(path)
        require(file_sha(path) == row['sha256'], 'Observation tool changed: ' + role)
        paths[role] = path
    return paths


def validate_bundle(directory, manifest_sha, compiled_sha, source_sha):
    directory = b.safe_path(directory, 'dir')
    require((manifest_sha, compiled_sha, source_sha) == (FROZEN['bundle_manifest'], FROZEN['compiled_review'], FROZEN['source_review']), 'Caller pins differ from frozen preparation basis')
    require(file_sha(directory / 'MANIFEST.json') == manifest_sha, 'Offline bundle manifest changed')
    m = load_json(directory / 'MANIFEST.json'); status = load_json(directory / 'STATUS.json')
    require(m.get('schema') == 1 and m.get('status') == b.STATUS and m.get('target') == TARGET and m.get('release') == RELEASE and m.get('patch_sha256') == PATCH, 'Offline bundle identity differs')
    require(all(m.get(k) is False for k in ('hardware_acceptance', 'executable_install', 'uvd_arm_implemented')), 'Offline bundle claims unexpected authority')
    require(status == {'status': b.STATUS, 'manifest_sha256': manifest_sha, 'hardware_acceptance': False, 'executable_install': False}, 'Offline bundle incomplete')
    expected = {'bzImage', 'config', 'modules.tar.zst', 'initramfs.cpio.gz', 'provenance/compiled-review.json', 'provenance/initramfs-preparation.json', 'provenance/retained-ib-plan.json', 'provenance/retained-ib-bootargs.txt', 'provenance/native/artifacts.json', 'provenance/native/manifest.json'} | {'provenance/native/' + n for n in b.RECEIPTS}
    require(set(m.get('copied_files', {})) == expected, 'Offline bundle file set differs')
    paths = {name: b.child(directory, name) for name in expected}
    for name, path in paths.items():
        row = m['copied_files'][name]
        require(b.measured(path) == {k: row[k] for k in ('sha256', 'bytes')}, 'Offline bundle bytes differ: ' + name)
    args = raw(directory / 'bootargs.new')
    require(sha(args) == m.get('bootargs_sha256') == FROZEN['bootargs'] and args == b.bootargs(raw(paths['provenance/retained-ib-bootargs.txt']))[0], 'Preparation arguments differ')
    require(file_sha(paths['bzImage']) == FROZEN['kernel'] and raw(paths['bzImage'])[0x202:0x206] == b'HdrS' and file_sha(paths['config']) == FROZEN['config'], 'Reviewed image/config differs')
    b.configuration(raw(paths['config']))
    require(file_sha(paths['initramfs.cpio.gz']) == b.INITRAMFS and file_sha(paths['provenance/initramfs-preparation.json']) == b.INIT_RECEIPT, 'Opaque initramfs changed')
    t.validate_initramfs_receipt(raw(paths['initramfs.cpio.gz']), load_json(paths['provenance/initramfs-preparation.json']))
    r = {n: load_json(paths['provenance/native/' + n]) for n in b.RECEIPTS}
    hashes = {n: m['copied_files']['provenance/native/' + n]['sha256'] for n in b.RECEIPTS}
    require((hashes['review.json'], hashes['build.json'], hashes['package.json']) == (source_sha, FROZEN['build'], FROZEN['package']), 'Frozen native receipt chain changed')
    require(m.get('compiled_review_sha256') == compiled_sha == file_sha(paths['provenance/compiled-review.json']) and m.get('source_review_sha256') == source_sha and m.get('build_receipt_sha256') == FROZEN['build'] and m.get('package_receipt_sha256') == FROZEN['package'], 'Bundle review declarations differ')
    compiled = load_json(paths['provenance/compiled-review.json'])
    require(compiled.get('status') == 'PASS_SCOPED_COMPILED_PREPARATION_REVIEW' and compiled.get('must_fix') == [] and compiled.get('release') == RELEASE and compiled.get('patch_sha256') == PATCH, 'Compiled review scope differs')
    require(all(compiled.get(k) is False for k in ('hardware_acceptance', 'hardware_tested', 'usb_staged', 'uvd_arm_implemented')), 'Compiled review authority differs')
    for key, expected_hash in (('build_receipt_sha256', FROZEN['build']), ('package_receipt_sha256', FROZEN['package']), ('source_review_sha256', source_sha), ('kernel_sha256', FROZEN['kernel']), ('config_sha256', FROZEN['config'])):
        require(compiled.get(key) == expected_hash, 'Compiled binding differs: ' + key)
    require(r['review.json'].get('status') == 'PASS_SCOPED_SOURCE_REVIEW_FOR_NATIVE_BUILD' and r['review.json'].get('must_fix') == [] and r['review.json'].get('patch_sha256') == PATCH and r['review.json'].get('uvd_arm_implemented') is False, 'Source review scope differs')
    require(r['identity.json'].get('target') == TARGET and r['identity.json'].get('release') == RELEASE and r['identity.json'].get('review_sha256') == source_sha, 'Native identity differs')
    previous = None
    for name, state in (('prepare-base.json', 'PASS_PREPARE_BASE'), ('prepare.json', 'PASS_PREPARE'), ('build.json', 'PASS_BUILD'), ('package.json', 'PASS_PACKAGE')):
        row = r[name]
        require(row.get('status') == state and row.get('target') == TARGET and row.get('release') == RELEASE and row.get('review_sha256') == source_sha and row.get('identity_sha256') == hashes['identity.json'] and row.get('baseline_preserved') is True and row.get('hardware_acceptance') is False, 'Native phase incomplete: ' + name)
        require(row.get('previous_phase_receipt_sha256') == (hashes[previous] if previous else None), 'Native phase link changed')
        previous = name
    build, package = r['build.json'], r['package.json']
    require(len(r['prepare.json']['patches']) == 73 and r['prepare.json']['patches'][-1]['sha256'] == PATCH and build.get('module_count') == 41, 'Native scope differs')
    for key, name in (('module_tree_sha256', 'module-tree.json'), ('modules_sha256', 'modules.json'), ('compiler_sha256', 'compiler.json'), ('source_inventory_sha256', 'built-source.json')):
        require(build.get(key) == hashes[name], 'Native inventory binding differs')
    artifacts = load_json(paths['provenance/native/artifacts.json'])
    require(package.get('artifacts') == artifacts and artifacts.get('kernel_release') == RELEASE and artifacts.get('module_count') == 41 and artifacts.get('active_patch_count') == 73 and artifacts.get('embedded_firmware') is False and artifacts.get('hardware_acceptance') == 'none', 'Packaged scope differs')
    for staged, packaged in (('bzImage', 'bzImage'), ('config', 'config'), ('modules.tar.zst', 'modules-' + RELEASE + '.tar.zst'), ('provenance/native/manifest.json', 'manifest.json'), ('provenance/native/artifacts.json', 'artifacts.json')):
        require(package['artifact_sha256'].get(packaged) == m['copied_files'][staged]['sha256'], 'Packaged bytes differ')
    external = m.get('external_archives', {})
    require(set(external) == {'inputs.tar.zst', 'source-' + RELEASE + '.tar.zst'}, 'Corresponding archive references missing')
    for name, row in external.items():
        require(row.get('copied') is False and row.get('requires_same_bytes_if_relocated') is True and package['artifact_sha256'].get(name) == row.get('sha256') and b.measured(b.safe_path(row['path'])) == {k: row[k] for k in ('sha256', 'bytes')}, 'External corresponding archive differs')
    b.validate_modules(paths['modules.tar.zst'], r['module-tree.json'], r['modules.json'])
    paths.update({'MANIFEST.json': directory / 'MANIFEST.json', 'STATUS.json': directory / 'STATUS.json', 'bootargs.new': directory / 'bootargs.new'})
    return m, paths, r['module-tree.json']


def validate_compatibility(status_path, disposition_path, expected_sha):
    require(expected_sha == FROZEN['compatibility'] == file_sha(status_path), 'Compatibility status pin differs')
    r = load_json(status_path)
    require(r.get('status') == 'PASS_SCOPED_STATIC_BOOT_COMPATIBILITY_WITH_DEBUGFS_PREREQUISITE' and r.get('hardware_acceptance') is False and r.get('kernel_rebuild_required') is False, 'Compatibility disposition incomplete')
    require(r.get('bootargs_sha256') == FROZEN['bootargs'] and r.get('chosen_initramfs_sha256') == b.INITRAMFS and r.get('primary_disposition_sha256') == file_sha(disposition_path), 'Compatibility input/disposition differs')


def validate_prior(plan_path, receipt_path, complete_path):
    source = load_json(HERE / 'install-source.json'); rollback = load_json(HERE / 'rollback.json')
    for path, key in ((plan_path, 'plan_sha256'), (receipt_path, 'install_receipt_sha256'), (complete_path, 'install_complete_sha256')):
        require(file_sha(path) == source['pins'][key], 'Retained IB provenance changed')
    plan, receipt, complete = (load_json(p) for p in (plan_path, receipt_path, complete_path))
    require(plan.get('experiment') == receipt.get('experiment') == complete.get('experiment') == source['experiment'], 'Retained IB experiment differs')
    require(plan.get('release') == source['release'] and plan.get('module_tree') == source['module_tree'], 'Retained IB modules differ')
    require(receipt.get('committed') is True and receipt.get('unmounted') is True and receipt.get('boot_after') == source['boot_hashes'] and not receipt.get('error') and not receipt.get('cleanup_errors'), 'Retained IB installation incomplete')
    require(complete.get('status') == 'PASS_INSTALLED_UNMOUNTED' and complete.get('plan_sha256') == source['pins']['plan_sha256'] and complete.get('both_read_only_filesystem_checks_passed_before_and_after') is True, 'Retained IB completion differs')
    require(plan.get('old_boot_hashes') == rollback['old_boot_hashes'] and plan.get('rollback_module_tree') == rollback['rollback_module_tree'], 'Native recovery provenance differs')
    return source


def validate_plan(p):
    require(p.get('schema') == 1 and p.get('status') == 'HOST_STAGED_PENDING_FINAL_REVIEW', 'Stage incomplete')
    experiment(p.get('experiment'))
    require(p.get('target') == TARGET and p.get('release') == RELEASE and p.get('patch_sha256') == PATCH and p.get('hardware_acceptance') is False and p.get('executable_install') is False, 'Preparation plan scope differs')
    require(p.get('launch_count') == 1 and p.get('launch_timeout_seconds') == 180 and p.get('installation_state') == ISOLATION and p.get('observation') == OBSERVATION and p.get('native_restore_automatic') is False, 'Bounded workflow differs')
    require(p.get('tools') == tool_pins(), 'Handoff tools changed')
    source, rollback = load_json(HERE / 'install-source.json'), load_json(HERE / 'rollback.json')
    require(p.get('install_boot_hashes') == source['boot_hashes'] and p.get('install_release') == source['release'] and p.get('install_module_tree') == source['module_tree'], 'Retained installation source must be exact IB-DATA')
    require(all(p.get(k) == v for k, v in rollback.items()), 'Native recovery/media constraints changed')
    require(p['old_boot_hashes'] == t.OLD_NATIVE_BOOT_HASHES and sha(json.dumps(p['rollback_module_tree'], sort_keys=True, separators=(',', ':')).encode()) == t.NATIVE_MODULE_MANIFEST_SHA, 'Native recovery identities changed')
    require(p.get('basis') == FROZEN, 'Frozen artifact basis changed')
    files = p.get('files', {})
    required = {'bundle/' + n for n in ({'bzImage', 'config', 'modules.tar.zst', 'initramfs.cpio.gz', 'bootargs.new', 'MANIFEST.json', 'STATUS.json', 'provenance/compiled-review.json', 'provenance/initramfs-preparation.json', 'provenance/retained-ib-plan.json', 'provenance/retained-ib-bootargs.txt', 'provenance/native/artifacts.json', 'provenance/native/manifest.json'} | {'provenance/native/' + n for n in b.RECEIPTS})}
    required |= {'compatibility-status.json', 'compatibility-disposition.md', 'prior-plan.json', 'prior-install-receipt.json', 'prior-install-complete.json', 'observation-contract.json'}
    tools = p.get('observation_tools', {})
    require({'acquisition', 'evaluator', 'result_binding'} <= set(tools) and all(re.fullmatch('[a-z][a-z0-9_]{0,63}', n) and digest(h) for n, h in tools.items()), 'Observation pins missing')
    required |= {'observation-tools/' + n + '.source' for n in tools}
    require(set(files) == required and all(digest(h) for h in files.values()), 'Stage file bindings incomplete')
    for key, filename in (('kernel_sha256', 'bundle/bzImage'), ('config_sha256', 'bundle/config'), ('modules_archive_sha256', 'bundle/modules.tar.zst'), ('new_bootargs_sha256', 'bundle/bootargs.new'), ('initramfs_sha256', 'bundle/initramfs.cpio.gz'), ('initramfs_preparation_receipt_sha256', 'bundle/provenance/initramfs-preparation.json'), ('observation_contract_sha256', 'observation-contract.json')):
        require(p.get(key) == files[filename], 'Plan artifact binding differs: ' + key)
    require(p['kernel_sha256'] == FROZEN['kernel'] and p['new_bootargs_sha256'] == FROZEN['bootargs'], 'Preparation image/args differ')
    prefix = 'lib/modules/' + RELEASE + '/'; tree = p.get('module_tree', {})
    require(len(tree) == 55 and sum(n.endswith('.ko') for n in tree) == 41 and all(n.startswith(prefix) and all(x not in ('', '.', '..') for x in n.split('/')) and digest(h) for n, h in tree.items()), 'Whole matching module set required')
    return p


def load_inputs(stage, rollback=False):
    stage = b.safe_path(stage, 'dir'); p = validate_plan(load_json(stage / 'plan.json'))
    old = {n: raw(stage / 'old-boot' / n) for n in t.BOOT_FILES}
    require({n: sha(v) for n, v in old.items()} == p['old_boot_hashes'], 'Native recovery bytes changed')
    args = raw(stage / 'bundle/bootargs.new')
    require(sha(args) == p['new_bootargs_sha256'], 'Reviewed arguments changed')
    if rollback:
        return p, None, None, {}, old, args
    for name, h in p['files'].items():
        require(file_sha(b.child(stage, name)) == h, 'Stage bytes changed: ' + name)
    source = {n: raw(stage / 'install-from' / n) for n in t.BOOT_FILES}
    require({n: sha(v) for n, v in source.items()} == p['install_boot_hashes'], 'Retained IB host bytes changed')
    m, paths, tree = validate_bundle(stage / 'bundle', FROZEN['bundle_manifest'], FROZEN['compiled_review'], FROZEN['source_review'])
    require({n: row['sha256'] for n, row in tree.items()} == p['module_tree'] and p.get('external_archives') == m['external_archives'], 'Module/corresponding archive plan differs')
    validate_prior(stage / 'prior-plan.json', stage / 'prior-install-receipt.json', stage / 'prior-install-complete.json')
    require(file_sha(stage / 'prior-plan.json') == file_sha(paths['provenance/retained-ib-plan.json']), 'Bundle and retained IB basis differ')
    validate_compatibility(stage / 'compatibility-status.json', stage / 'compatibility-disposition.md', FROZEN['compatibility'])
    contract = load_json(stage / 'observation-contract.json'); tools = contract_tools(contract)
    require({n: file_sha(path) for n, path in tools.items()} == p['observation_tools'], 'Observation tool set differs')
    require(all(file_sha(stage / 'observation-tools' / (n + '.source')) == contract['tools'][n]['sha256'] for n in tools), 'Saved observation tool differs')
    files = t.validate_archive(t.run('zstd', '-d', '-c', '-M256MB', str(paths['modules.tar.zst']), binary=True), p)
    return p, raw(paths['bzImage']), raw(paths['initramfs.cpio.gz']), files, old, args


def validate_reviewed_handoff(stage, reviewed_sha, plan_sha):
    require(digest(reviewed_sha) and digest(plan_sha), 'Final plan/review pins absent')
    require(file_sha(stage / 'plan.json') == plan_sha and file_sha(stage.parent / 'REVIEWED-HANDOFF.json') == reviewed_sha, 'Final plan/review changed')
    p = validate_plan(load_json(stage / 'plan.json')); r = load_json(stage.parent / 'REVIEWED-HANDOFF.json')
    expected = {'status': 'APPROVED_ONE_BOUNDED_TEST', 'experiment': p['experiment'], 'target': TARGET, 'release': RELEASE,
                'plan_sha256': plan_sha, 'tools': p['tools'], 'bundle_manifest_sha256': FROZEN['bundle_manifest'],
                'compiled_review_sha256': FROZEN['compiled_review'], 'source_review_sha256': FROZEN['source_review'],
                'build_receipt_sha256': FROZEN['build'], 'package_receipt_sha256': FROZEN['package'],
                'compatibility_status_sha256': FROZEN['compatibility'], 'observation_contract_sha256': p['observation_contract_sha256'],
                'observation_tools': p['observation_tools'], 'hardware_acceptance': False,
                'full_poweroff_before_launch_is_separate_prerequisite': True, 'prior_interrupted_reviews_remain_incomplete': True}
    require(all(r.get(k) == v for k, v in expected.items()), 'Final preparation handoff review differs')
    return r


def configure(p):
    t.EXP = experiment(p['experiment'])
    t.load_inputs = load_inputs
    t.validate_reviewed_handoff = validate_reviewed_handoff

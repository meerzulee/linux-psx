#!/usr/bin/env python3
"""IB-DATA host evidence validation; never discovers a device or calls UART."""
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[3]
LAB = WORKSPACE / 'lab/uvd-2026-10-20/ib-data/handoff'
TARGET = '7.2-ps4-baikal-uvd-ib-data-dev1'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-ib-data-dev1'
CANDIDATE = 'cf2482e51798a21a2c3b6cbe0ac7ecb270ae0d20cd109c3d29583cbcb4f2201e'
PATCH = '3224518cb9b6b47e3e9d251848c0478841c8099d4208b17fadf0dbcdfe14912b'
PREFIX = 'drivers/gpu/drm/amd/amdgpu/'
SOURCE_PINS = {PREFIX+'amdgpu_ps4_uvd_ready.c':CANDIDATE,
    PREFIX+'amdgpu_ps4_map_canary.c':'e7283b838eb1f96cbc0bd865716320dd87538a768bc38b215e98b7f6a29abab8',
    PREFIX+'amdgpu_ps4_uvd_ready.h':'59a8941710bd4d18dbb1ad877d4f83baf17b6371a287b9ceb5e2ef5f2cf1d7de',
    PREFIX+'amdgpu_ps4_uvd_ib_data_plan.h':'ef7fe8ed33507c93d07c62347b57add08daa8dd43727259440321c9f06fdf0e6'}
IRQ = 'ccf618c976b9a7b80045ffaeb81b7ede0707fa98bef8aca4bc7a7653a3de5480'
TOOL_NAMES = ('common.py', 'transaction.py', 'install_usb.py', 'prepare_stage.py', 'run_test.py', 'extract.py', 'rollback.json', 'install-source.json')
GATE = b'amdgpu.ps4_uvd_ready=1 amdgpu.ps4_uvd_poll_state=1 amdgpu.ps4_uvd_token=1 amdgpu.ps4_uvd_ib_data=1'
spec = importlib.util.spec_from_file_location('stobs_transaction', HERE / 'transaction.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
require, raw, sha = t.require, t.regular_bytes, t.sha


def load_json(path):
    return json.loads(raw(path, 16 * 1024 * 1024))


def file_sha(path):
    # Stream large preserved source archives; reject symlink input.
    import hashlib
    require(path.is_file() and not path.is_symlink(), 'Not a regular input: ' + str(path))
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def write(path, content):
    t.write_new(path, content)


def save(path, value):
    write(path, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())


def experiment(value):
    require(isinstance(value, str) and re.fullmatch(r'EXP-\d{8}-[0-9]{3}', value) and not value.endswith('-000'), 'Invalid experiment identifier')
    import datetime
    datetime.datetime.strptime(value[4:12], '%Y%m%d')
    return value


def new_bootargs(old):
    require(old.endswith(b'\n') and old.count(b'\n') == 1 and b'\r' not in old and b'\0' not in old, 'Single LF bootargs required')
    for token in old.split():
        key = token.split(b'=', 1)[0].replace(b'-', b'_')
        require(not key.startswith((b'amdgpu.ps4_uvd_', b'ps4_uvd_')) and key not in (b'amdgpu.ps4_map_canary', b'ps4_map_canary'), 'Existing experimental gate forbidden')
    return old[:-1] + b' ' + GATE + b'\n'


def tool_pins():
    return {name: file_sha(HERE / name) for name in TOOL_NAMES}


def validate_plan(p):
    require(p.get('schema') == 1 and p.get('status') == 'HOST_STAGED_PENDING_FINAL_REVIEW', 'Draft stage cannot execute')
    experiment(p.get('experiment'))
    require(p.get('target') == TARGET and p.get('release') == RELEASE, 'Wrong candidate release')
    require(p.get('candidate_sha256') == CANDIDATE and p.get('patch_sha256') == PATCH, 'Wrong IB-DATA source')
    require(p.get('hardware_acceptance') is False and p.get('launch_count') == 1 and p.get('launch_timeout_seconds') == 180, 'Wrong bounded scope')
    require(p.get('installation_state') == {'usb': 'attached-to-Omarchynator', 'console': ['not-observed', 'orbis-idle', 'fully-off'], 'console_power_observation_required': False, 'operator_holds_linux_launch': True, 'fresh_full_poweroff_required_for_post_test_rollback': True}, 'Installation isolation/state contract differs')
    require(p.get('observation') == {'max_records':40,'deadline_ns':250000000,'publication_count':1,'confirmation_count':2,'ring_crc32':'e5b6afa6','ib_crc32':'e847443a','data_baseline':'43504442','data_result':'55564442','context_baseline':'43504342','accepted':0}, 'Wrong observation contract')
    source = load_json(HERE / 'install-source.json')
    require(p.get('install_boot_hashes') == source['boot_hashes'] and p.get('install_release') == source['release'] and p.get('install_module_tree') == source['module_tree'], 'Installation source must be exact retained Revision C')
    require(p.get('native_restore_automatic') is False, 'Automatic restoration is not authorized')
    rollback = load_json(HERE / 'rollback.json')
    require(all(p.get(k) == v for k, v in rollback.items()), 'Native rollback or media constraints changed')
    require(p['old_boot_hashes'] == t.OLD_NATIVE_BOOT_HASHES, 'Native boot identities changed')
    require(sha(json.dumps(p['rollback_module_tree'], sort_keys=True, separators=(',', ':')).encode()) == t.NATIVE_MODULE_MANIFEST_SHA, 'Native module manifest changed')
    require(p.get('tools') == tool_pins(), 'Reviewed handoff tool bytes changed')
    bindings = p.get('files', {})
    required = {'bzImage', 'config', 'modules.tar.zst', 'bootargs.new', 'initramfs.cpio.gz', 'initramfs-preparation.json',
                'primary-review.json', 'build.json', 'package.json', 'prepare.json', 'prepare-base.json', 'identity.json',
                'artifacts.json', 'manifest.json', 'module-tree.json', 'project-inputs.json', 'inputs.tar.zst', 'compiled-review.json',
                'prior-plan.json','prior-install-receipt.json','prior-install-complete.json','host-mac.json','host-linux.json'}
    require(set(bindings) == required and all(digest(v) for v in bindings.values()), 'Incomplete stage bindings')
    for name, key in (('bzImage', 'kernel_sha256'), ('config', 'config_sha256'), ('modules.tar.zst', 'modules_archive_sha256'), ('bootargs.new', 'new_bootargs_sha256'), ('initramfs.cpio.gz', 'initramfs_sha256'), ('initramfs-preparation.json', 'initramfs_preparation_receipt_sha256')):
        require(p.get(key) == bindings[name], 'Inconsistent artifact binding: ' + name)
    prefix = 'lib/modules/' + RELEASE + '/'
    tree = p.get('module_tree', {})
    require(len(tree) == 55 and sum(n.endswith('.ko') for n in tree) == 41, 'Whole 55-file/41-module set required')
    require(all(n.startswith(prefix) and all(x not in ('', '.', '..') for x in n.split('/')) and digest(h) for n, h in tree.items()), 'Unsafe/incomplete module tree')
    return p


def validate_chain(p, stage):
    b, pack = load_json(stage / 'build.json'), load_json(stage / 'package.json')
    prep, base, identity = (load_json(stage / n) for n in ('prepare.json', 'prepare-base.json', 'identity.json'))
    primary, manifest, artifacts = (load_json(stage / n) for n in ('primary-review.json', 'manifest.json', 'artifacts.json'))
    review_sha = p['files']['primary-review.json']
    for report, expected in ((base, 'PASS_PREPARE_BASE'), (prep, 'PASS_PREPARE'), (b, 'PASS_BUILD'), (pack, 'PASS_PACKAGE')):
        require(report.get('status') == expected and report.get('baseline_preserved') is True and report.get('hardware_acceptance') is False, 'Native phase failed/incomplete: ' + expected)
        require(report.get('target') == TARGET and report.get('release') == RELEASE and report.get('review_sha256') == review_sha, 'Native phase identity differs')
        require(report.get('identity_sha256') == p['files']['identity.json'], 'Native phase identity receipt differs')
    for report, previous in ((prep, 'prepare-base.json'), (b, 'prepare.json'), (pack, 'build.json')):
        require(report.get('previous_phase_receipt_sha256') == p['files'][previous], 'Native chain link differs')
    require(identity.get('review_sha256') == review_sha, 'Native identity review differs')
    require(primary.get('final_primary_review') is True and primary.get('verdict') == 'APPROVED_FOR_NATIVE_BUILD_ONLY' and primary.get('unresolved_findings') == [], 'Primary build review incomplete')
    require(primary.get('target') == TARGET and primary.get('release') == RELEASE and type(primary.get('host_cases')) is int and primary['host_cases'] > 0, 'Host review scope differs')
    require({n:r['sha256'] for n,r in primary.get('source_pins',{}).items()} == SOURCE_PINS and primary['pins']['patch']['sha256'] == PATCH, 'Primary review source scope differs')
    validate_host_receipts(p, stage, primary)
    require(prep.get('changed_sources') == sorted(SOURCE_PINS) and len(prep.get('patches',[])) == 72, 'Prepared source/patch scope differs')
    require(manifest.get('target') == TARGET and manifest.get('release') == RELEASE and manifest.get('active_patch_count') == 72 and manifest.get('review_sha256') == review_sha and manifest.get('patch_sha256') == PATCH and manifest.get('irq_source_sha256') == IRQ, 'Manifest candidate differs')
    require(manifest.get('hardware_acceptance') is False and manifest.get('project_base_commit') == '35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2', 'Manifest scope/base differs')
    require(manifest.get('native_tool_sha256') == primary['pins']['native_tool']['sha256'], 'Native tool identity differs')
    require(manifest.get('project_inputs_sha256') == p['files']['project-inputs.json'], 'Project input inventory differs')
    require(pack.get('artifacts') == artifacts and artifacts.get('kernel_release') == RELEASE and artifacts.get('hardware_acceptance') == 'none' and artifacts.get('embedded_firmware') is False and artifacts.get('module_count') == 41 and artifacts.get('active_patch_count') == 72, 'Packaged metadata differs')
    require(artifacts.get('review_sha256') == review_sha, 'Package review differs')
    for staged, original in (('bzImage', 'bzImage'), ('config', 'config'), ('modules.tar.zst', 'modules-' + RELEASE + '.tar.zst'), ('inputs.tar.zst', 'inputs.tar.zst'), ('manifest.json', 'manifest.json'), ('artifacts.json', 'artifacts.json')):
        require(pack['artifact_sha256'].get(original) == p['files'][staged], 'Package artifact differs: ' + staged)
    for name in ('bzImage', 'config', 'manifest.json'):
        require(b['artifact_sha256'].get(name) == p['files'][name], 'Build artifact differs: ' + name)
    require(b.get('module_count') == 41 and b.get('module_tree_sha256') == p['files']['module-tree.json'], 'Build module set differs')
    tree = load_json(stage / 'module-tree.json')
    require({n: r['sha256'] for n, r in tree.items()} == p['module_tree'] and all(r.get('kind') == 'file' for r in tree.values()), 'Module manifest differs')
    require(artifacts.get('kernel_sha256') == p['kernel_sha256'] and artifacts.get('kernel_config_sha256') == p['config_sha256'] and artifacts.get('modules_archive', {}).get('sha256') == p['modules_archive_sha256'] and artifacts.get('inputs_archive', {}).get('sha256') == p['files']['inputs.tar.zst'], 'Artifact declarations differ')
    require(artifacts.get('source_archive',{}).get('path') == 'source-'+RELEASE+'.tar.zst' and pack['artifact_sha256'].get('source-'+RELEASE+'.tar.zst') == p.get('source_archive_sha256'), 'Corresponding-source archive binding missing')
    require(artifacts.get('source_archive', {}).get('sha256') == p.get('source_archive_sha256') and digest(p.get('source_archive_sha256')), 'Source archive missing')
    checks = pack.get('archive_verification', {})
    require(set(checks) == {'modules', 'inputs', 'source'}, 'Archive readback receipts incomplete')
    require(all(type(r.get('members_verified')) is int and type(r.get('regular_files')) is int and r['members_verified'] >= r['regular_files'] > 0 for r in checks.values()), 'Archive readback counts invalid')
    require(checks['modules']['regular_files'] == checks['modules']['members_verified'] == 55, 'Whole module archive readback absent')
    require(checks['inputs']['regular_files'] == len(load_json(stage / 'project-inputs.json')) + 1, 'Input archive readback count differs')
    c = load_json(stage / 'compiled-review.json')
    expected = {'status': 'PASS_SCOPED_COMPILED_REVIEW_NO_HARDWARE_ACCEPTANCE', 'target': TARGET, 'release': RELEASE,
                'patch_sha256': PATCH, 'ready_source_sha256': CANDIDATE, 'source_sha256':SOURCE_PINS, 'kernel_sha256': p['kernel_sha256'],
                'modules_archive_sha256': p['modules_archive_sha256'], 'config_sha256': p['config_sha256'],
                'vmlinux_sha256': b['vmlinux_sha256'], 'native_build_receipt_sha256': p['files']['build.json'],
                'source_review_sha256': review_sha, 'hardware_acceptance': False, 'must_fix_findings': [],
                'prior_interrupted_reviews_remain_incomplete': True}
    require(all(c.get(k) == v for k, v in expected.items()) and digest(c.get('ready_object_sha256')) and digest(c.get('canary_object_sha256')), 'Compiled review does not cover the candidate')
    validate_install_source(p, stage)
    return c


def validate_install_source(p, stage):
    source=load_json(HERE/'install-source.json')
    for name,key in (('prior-plan.json','plan_sha256'),('prior-install-receipt.json','install_receipt_sha256'),('prior-install-complete.json','install_complete_sha256')):
        require(p['files'][name] == source['pins'][key], 'Prior installation evidence differs: '+name)
    plan,receipt,complete=(load_json(stage/n) for n in ('prior-plan.json','prior-install-receipt.json','prior-install-complete.json'))
    require(receipt.get('experiment') == complete.get('experiment') == plan.get('experiment') == source['experiment'], 'Prior experiment identities differ')
    require(receipt.get('committed') is True and receipt.get('unmounted') is True and receipt.get('boot_after') == source['boot_hashes'], 'Prior install not completed')
    require(complete.get('status') == 'PASS_INSTALLED_UNMOUNTED' and complete.get('plan_sha256') == source['pins']['plan_sha256'], 'Prior completion differs')
    require(plan.get('release') == source['release'] and plan.get('module_tree') == source['module_tree'], 'Prior module release differs')
    require(plan.get('old_boot_hashes') == p['old_boot_hashes'] and plan.get('rollback_module_tree') == p['rollback_module_tree'], 'Native rollback provenance changed')


def validate_host_receipts(p, stage, primary):
    rows=primary.get('host_receipts',[])
    require(len(rows)==2 and {r['sha256'] for r in rows} == {p['files']['host-mac.json'],p['files']['host-linux.json']}, 'Pinned host receipts differ')
    generated=set()
    for name,system in (('host-mac.json','Darwin'),('host-linux.json','Linux')):
        r=load_json(stage/name)
        require(r.get('status') == 'PASS_EXTRACTED_SOURCE_HOST_ONLY', 'Host fixture incomplete')
        for key,pin in (('patch_sha256','patch'),('checker_sha256','checker'),('fixture_sha256','fixture'),('planner_sha256','planner'),('flow_sha256','flow'),('generator_sha256','generator')):
            require(r.get(key)==primary['pins'][pin]['sha256'], 'Host input differs: '+key)
        expected={**SOURCE_PINS,PREFIX+'amdgpu_ps4_uvd_ring_plan.h':'2299fd28005bdbcc2aa3c3664740f5d87a34cb7294c705c45facdde46d44df2e'}
        require(r.get('output_sha256') == expected, 'Host source output differs')
        tc=r.get('toolchain',{});host=tc.get('host',{})
        require(host.get('system')==system and (system!='Linux' or host.get('machine')=='x86_64'), 'Host receipt platform differs')
        require(all(tc.get(k) for k in ('compiler_argv','compiler_path','compiler_version','compiler_target')), 'Host compiler absent')
        results=r.get('results',[])
        require(len(results)==2 and {x.get('mode') for x in results}=={'optimized','sanitized'} and all(x.get('status')=='PASS' and x.get('returncode')==0 and re.match(rf"PASS: {primary['host_cases']} actual-source cases;",x.get('output','')) for x in results), 'Host optimized/sanitized result differs')
        required=primary.get('required_mutations',{})
        require(required and len(r.get('sensitivity',[]))==len(required) and {x.get('name') for x in r['sensitivity']}==set(required), 'Required mutation set differs')
        require(all(x.get('detected') is True and type(x.get('returncode')) is int and x['returncode']!=0 and x.get('expected_witness')==x.get('observed_witness')==required[x['name']] for x in r['sensitivity']), 'Mutation witness differs')
        require(digest(r.get('generated_fixture_sha256')), 'Generated fixture digest missing')
        generated.add(r['generated_fixture_sha256'])
    require(len(generated)==1, 'Hosts ran different extracted sources')


def load_inputs(stage, rollback=False):
    p = validate_plan(load_json(stage / 'plan.json'))
    old = {n: raw(stage / 'old-boot' / n) for n in t.BOOT_FILES}
    require({n: sha(b) for n, b in old.items()} == p['old_boot_hashes'], 'Native recovery changed')
    args = raw(stage / 'bootargs.new')
    require(args == new_bootargs(old['bootargs.txt']) and sha(args) == p['new_bootargs_sha256'], 'Bootargs differ')
    if rollback:
        return p, None, None, {}, old, args
    source = {n:raw(stage/'install-from'/n) for n in t.BOOT_FILES}
    require({n:sha(b) for n,b in source.items()} == p['install_boot_hashes'], 'Retained Revision C host copy changed')
    for name, h in p['files'].items():
        require(file_sha(stage / name) == h, 'Stage bytes changed: ' + name)
    validate_chain(p, stage)
    kernel = raw(stage / 'bzImage')
    require(kernel[0x202:0x206] == b'HdrS', 'Missing x86 kernel header')
    initramfs = raw(stage / 'initramfs.cpio.gz', 16 * 1024 * 1024)
    t.validate_initramfs_receipt(initramfs, load_json(stage / 'initramfs-preparation.json'))
    files = t.validate_archive(t.run('zstd', '-d', '-c', '-M256MB', str(stage / 'modules.tar.zst'), binary=True), p)
    with tempfile.TemporaryDirectory(prefix='stobs-module-') as scratch:
        for name, content in files.items():
            if name.endswith('.ko'):
                path = Path(scratch) / Path(name).name
                write(path, content)
                require(t.run('modinfo', '-F', 'vermagic', str(path)).split()[0] == RELEASE, 'Wrong module vermagic')
                path.unlink()
    return p, kernel, initramfs, files, old, args


def validate_reviewed_handoff(stage, reviewed_sha, plan_sha):
    require(digest(reviewed_sha) and digest(plan_sha), 'Final review pins absent')
    require(file_sha(stage / 'plan.json') == plan_sha, 'Reviewed plan changed')
    review_path = stage.parent / 'REVIEWED-HANDOFF.json'
    require(file_sha(review_path) == reviewed_sha, 'Handoff review changed')
    p, r = validate_plan(load_json(stage / 'plan.json')), load_json(review_path)
    expected = {'status': 'APPROVED_ONE_BOUNDED_TEST', 'experiment': p['experiment'], 'target': TARGET, 'release': RELEASE,
                'plan_sha256': plan_sha, 'tools': p['tools'], 'compiled_review_sha256': p['files']['compiled-review.json'],
                'source_review_sha256': p['files']['primary-review.json'], 'build_receipt_sha256': p['files']['build.json'],
                'package_receipt_sha256': p['files']['package.json'], 'hardware_acceptance': False,
                'prior_interrupted_reviews_remain_incomplete': True}
    require(all(r.get(k) == v for k, v in expected.items()), 'Final handoff review differs')
    return r


def configure(p):
    t.EXP = experiment(p['experiment'])
    t.load_inputs = load_inputs
    t.validate_reviewed_handoff = validate_reviewed_handoff

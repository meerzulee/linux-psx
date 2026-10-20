#!/usr/bin/env python3
"""Build an exclusive private host stage from actual native receipts. No devices/UART."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location('stobs_common', HERE / 'common.py')
c = importlib.util.module_from_spec(s)
s.loader.exec_module(c)


def prepare(run, compiled, exp, destination):
    c.experiment(exp)
    c.require(not os.path.lexists(destination), 'Stage already exists; preserve it')
    receipts = run / 'receipts'
    output = run / 'project/output' / c.TARGET
    package = c.load_json(receipts / 'package.json')
    c.require(package.get('status') == 'PASS_PACKAGE', 'Native package incomplete')
    for name, digest in package['artifact_sha256'].items():
        c.require(Path(name).name == name and c.file_sha(output / name) == digest, 'Packaged output changed: ' + name)
    prior = c.WORKSPACE / 'lab/uvd-2026-10-19/data-control/handoff/stage'
    bindings = {name: receipts / name for name in ('build.json', 'package.json', 'prepare.json', 'prepare-base.json', 'identity.json', 'module-tree.json', 'project-inputs.json')}
    bindings.update({'primary-review.json': receipts / 'review.json', 'compiled-review.json': compiled,
                     'bzImage': output / 'bzImage', 'config': output / 'config', 'manifest.json': output / 'manifest.json',
                     'artifacts.json': output / 'artifacts.json', 'inputs.tar.zst': output / 'inputs.tar.zst',
                     'modules.tar.zst': output / ('modules-' + c.RELEASE + '.tar.zst'),
                     'initramfs.cpio.gz': prior / 'initramfs.cpio.gz', 'initramfs-preparation.json': prior / 'initramfs-preparation.json'})
    plan = c.load_json(HERE / 'rollback.json')
    old = {n: c.raw(prior / 'old-boot' / n) for n in c.t.BOOT_FILES}
    c.require({n: c.sha(b) for n, b in old.items()} == plan['old_boot_hashes'], 'Preserved native rollback changed')
    args = c.new_bootargs(old['bootargs.txt'])
    old_test_args = c.raw(prior / 'bootargs.new')
    c.require(old_test_args.count(b'amdgpu.ps4_uvd_data_control=1') == 1 and
              args == old_test_args.replace(b'amdgpu.ps4_uvd_data_control=1', b'amdgpu.ps4_uvd_status_observation=1'), 'Console/other EXP005 arguments changed')
    tree = c.load_json(receipts / 'module-tree.json')
    plan.update(schema=1, status='HOST_STAGED_PENDING_FINAL_REVIEW', experiment=exp, target=c.TARGET, release=c.RELEASE,
                candidate_sha256=c.CANDIDATE, patch_sha256=c.PATCH, hardware_acceptance=False,
                launch_count=1, launch_timeout_seconds=180,
                installation_state={'usb':'attached-to-Omarchynator','console':['orbis-idle','fully-off'],'operator_holds_linux_launch':True,'fresh_full_poweroff_required_for_post_test_rollback':True},
                objective='Observe STATUS bit16 relative to markers and pointers over one bounded window using the identical EXP005 ring packet.',
                observation={'max_records': 40, 'deadline_ns': 250000000, 'publication_count': 1, 'ring_crc32': 'cadc2d79', 'accepted': 0},
                tools=c.tool_pins(), module_tree={n:r['sha256'] for n,r in tree.items()},
                source_archive_sha256=package['artifacts']['source_archive']['sha256'])
    plan['files'] = {n:c.file_sha(p) for n,p in bindings.items()}
    plan['files']['bootargs.new'] = c.sha(args)
    for name, key in (('bzImage','kernel_sha256'),('config','config_sha256'),('modules.tar.zst','modules_archive_sha256'),('bootargs.new','new_bootargs_sha256')):
        plan[key] = plan['files'][name]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.stobs-stage-', dir=destination.parent) as scratch:
        stage = Path(scratch) / 'stage'
        stage.mkdir(mode=0o700)
        (stage / 'old-boot').mkdir(mode=0o700)
        for n,b in old.items():
            c.write(stage / 'old-boot' / n, b)
        for n,p in bindings.items():
            # Inputs copied only after measurement; load_inputs rehashes the copies.
            with p.open('rb') as source, (stage / n).open('xb') as target:
                shutil.copyfileobj(source, target)
                target.flush(); os.fsync(target.fileno())
            os.chmod(stage / n, 0o600)
        c.write(stage / 'bootargs.new', args)
        c.save(stage / 'plan.json', plan)
        c.load_inputs(stage)
        c.t.fsync_path(stage / 'old-boot'); c.t.fsync_path(stage)
        os.rename(stage, destination)
        c.t.fsync_path(destination.parent)
    return {'status':'HOST_STAGE_VERIFIED_NO_DEVICES', 'stage':str(destination), 'plan_sha256':c.file_sha(destination / 'plan.json'),
            'experiment':exp, 'hardware_acceptance':False, 'installed':False, 'session_started':False}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--compiled-review',type=Path,required=True)
    p.add_argument('--experiment',required=True)
    p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args()
    import json
    print(json.dumps(prepare(a.run,a.compiled_review,a.experiment,a.stage),indent=2,sort_keys=True))

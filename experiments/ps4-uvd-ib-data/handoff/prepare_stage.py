#!/usr/bin/env python3
"""Build an exclusive private host stage from actual native receipts. No devices/UART."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location('ibdata_common', HERE / 'common.py')
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
    prior = c.WORKSPACE / 'lab/uvd-2026-10-19/status-observation/handoff/stage'
    bindings = {name: receipts / name for name in ('build.json', 'package.json', 'prepare.json', 'prepare-base.json', 'identity.json', 'module-tree.json', 'project-inputs.json')}
    bindings.update({'primary-review.json': receipts / 'review.json', 'compiled-review.json': compiled,
                     'bzImage': output / 'bzImage', 'config': output / 'config', 'manifest.json': output / 'manifest.json',
                     'artifacts.json': output / 'artifacts.json', 'inputs.tar.zst': output / 'inputs.tar.zst',
                     'modules.tar.zst': output / ('modules-' + c.RELEASE + '.tar.zst'),
                     'initramfs.cpio.gz': prior / 'initramfs.cpio.gz', 'initramfs-preparation.json': prior / 'initramfs-preparation.json'})
    bindings.update({'prior-plan.json':prior/'plan.json',
                     'prior-install-receipt.json':prior.parent/'runtime/install-receipt.json',
                     'prior-install-complete.json':prior.parent/'runtime/install-complete.json'})
    primary=c.load_json(receipts/'review.json')
    host_names={'Darwin':'host-mac.json','Linux':'host-linux.json'}
    for row in primary['host_receipts']:
        source=c.WORKSPACE/row['path']
        c.require(c.file_sha(source)==row['sha256'], 'Primary host receipt changed')
        name=host_names[c.load_json(source)['toolchain']['host']['system']]
        c.require(name not in bindings, 'Duplicate host receipt platform')
        bindings[name]=source
    plan = c.load_json(HERE / 'rollback.json')
    install=c.load_json(HERE/'install-source.json')
    plan.update(install_boot_hashes=install['boot_hashes'], install_release=install['release'], install_module_tree=install['module_tree'], native_restore_automatic=False)
    source_paths={n:prior/n for n in c.t.BOOT_FILES}
    source_paths.update({'bootargs.txt':prior/'bootargs.new','vram.txt':prior/'old-boot/vram.txt','release.json':prior/'old-boot/release.json'})
    source_bytes={n:c.raw(p) for n,p in source_paths.items()}
    c.require({n:c.sha(b) for n,b in source_bytes.items()} == plan['install_boot_hashes'], 'Retained Revision C host bytes changed')
    old = {n: c.raw(prior / 'old-boot' / n) for n in c.t.BOOT_FILES}
    c.require({n: c.sha(b) for n, b in old.items()} == plan['old_boot_hashes'], 'Preserved native rollback changed')
    args = c.new_bootargs(old['bootargs.txt'])
    old_test_args = c.raw(prior / 'bootargs.new')
    c.require(old_test_args.count(b'amdgpu.ps4_uvd_status_observation=1') == 1 and
              args == old_test_args.replace(b'amdgpu.ps4_uvd_status_observation=1', b'amdgpu.ps4_uvd_ib_data=1'), 'Frozen native-derived console/other arguments changed')
    tree = c.load_json(receipts / 'module-tree.json')
    plan.update(schema=1, status='HOST_STAGED_PENDING_FINAL_REVIEW', experiment=exp, target=c.TARGET, release=c.RELEASE,
                candidate_sha256=c.CANDIDATE, patch_sha256=c.PATCH, hardware_acceptance=False,
                launch_count=1, launch_timeout_seconds=180,
                installation_state={'usb':'attached-to-Omarchynator','console':['not-observed','orbis-idle','fully-off'],'console_power_observation_required':False,'operator_holds_linux_launch':True,'fresh_full_poweroff_required_for_post_test_rollback':True},
                objective='Test one owned IB DATA0 marker with no alternate result writer; preserve all observations and two consecutive valid predeadline joint confirmations.',
                observation={'max_records':40,'deadline_ns':250000000,'publication_count':1,'confirmation_count':2,'ring_crc32':'e5b6afa6','ib_crc32':'e847443a','data_baseline':'43504442','data_result':'55564442','context_baseline':'43504342','accepted':0},
                tools=c.tool_pins(), module_tree={n:r['sha256'] for n,r in tree.items()},
                source_archive_sha256=package['artifacts']['source_archive']['sha256'])
    plan['files'] = {n:c.file_sha(p) for n,p in bindings.items()}
    plan['files']['bootargs.new'] = c.sha(args)
    for name, key in (('bzImage','kernel_sha256'),('config','config_sha256'),('modules.tar.zst','modules_archive_sha256'),('bootargs.new','new_bootargs_sha256')):
        plan[key] = plan['files'][name]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ibdata-stage-', dir=destination.parent) as scratch:
        stage = Path(scratch) / 'stage'
        stage.mkdir(mode=0o700)
        (stage / 'old-boot').mkdir(mode=0o700)
        (stage / 'install-from').mkdir(mode=0o700)
        for n,b in source_bytes.items():
            c.write(stage/'install-from'/n,b)
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
        c.t.fsync_path(stage / 'old-boot'); c.t.fsync_path(stage / 'install-from'); c.t.fsync_path(stage)
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

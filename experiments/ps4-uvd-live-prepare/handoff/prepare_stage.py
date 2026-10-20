#!/usr/bin/env python3
"""Create a host-only stage from the frozen bundle; never allocate a session."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location('live_prepare_common', HERE / 'common.py')
c = importlib.util.module_from_spec(s); s.loader.exec_module(c)


def prepare(bundle, bundle_manifest_sha, compiled_sha, source_sha, compatibility,
            compatibility_sha, disposition, prior, prior_receipt, prior_complete,
            contract, contract_sha, exp, destination):
    c.experiment(exp)
    destination = c.b.safe_path(destination, 'new')
    c.require(not os.path.lexists(destination), 'Stage already exists; preserve it')
    c.b.safe_path(destination.parent, 'dir')
    bundle, prior = c.b.safe_path(bundle, 'dir'), c.b.safe_path(prior, 'dir')
    c.require(not destination.is_relative_to(bundle) and not destination.is_relative_to(prior), 'Stage must be outside preserved inputs')
    manifest, bundle_paths, tree = c.validate_bundle(bundle, bundle_manifest_sha, compiled_sha, source_sha)
    c.validate_compatibility(compatibility, disposition, compatibility_sha)
    source = c.validate_prior(prior / 'plan.json', prior_receipt, prior_complete)
    c.require(c.file_sha(prior / 'plan.json') == c.file_sha(bundle_paths['provenance/retained-ib-plan.json']), 'Bundle and retained IB stage differ')
    c.require(c.digest(contract_sha) and c.file_sha(contract) == contract_sha, 'Explicit observation contract pin missing/changed')
    observation = c.load_json(contract); observation_tools = c.contract_tools(observation)
    old = {n: c.raw(prior / 'old-boot' / n) for n in c.t.BOOT_FILES}
    current_paths = {n: prior / n for n in ('bzImage', 'initramfs.cpio.gz')}
    current_paths.update({'bootargs.txt': prior / 'bootargs.new', 'vram.txt': prior / 'old-boot/vram.txt', 'release.json': prior / 'old-boot/release.json'})
    current = {n: c.raw(p) for n, p in current_paths.items()}
    rollback = c.load_json(HERE / 'rollback.json')
    c.require({n: c.sha(v) for n, v in old.items()} == rollback['old_boot_hashes'], 'Preserved native recovery changed')
    c.require({n: c.sha(v) for n, v in current.items()} == source['boot_hashes'], 'Retained IB files changed')
    bindings = {'bundle/' + n: p for n, p in bundle_paths.items()}
    bindings.update({'compatibility-status.json': compatibility, 'compatibility-disposition.md': disposition,
                     'prior-plan.json': prior / 'plan.json', 'prior-install-receipt.json': prior_receipt,
                     'prior-install-complete.json': prior_complete, 'observation-contract.json': contract})
    bindings.update({'observation-tools/' + n + '.source': p for n, p in observation_tools.items()})
    measured = {n: c.b.measured(p) for n, p in bindings.items()}
    c.require(measured['bundle/MANIFEST.json']['sha256'] == bundle_manifest_sha and measured['compatibility-status.json']['sha256'] == compatibility_sha and measured['observation-contract.json']['sha256'] == contract_sha, 'Pinned input changed after validation')
    plan = {**rollback, 'schema': 1, 'status': 'HOST_STAGED_PENDING_FINAL_REVIEW', 'experiment': exp,
            'target': c.TARGET, 'release': c.RELEASE, 'patch_sha256': c.PATCH, 'basis': dict(c.FROZEN),
            'hardware_acceptance': False, 'executable_install': False, 'launch_count': 1, 'launch_timeout_seconds': 180,
            'installation_state': c.ISOLATION, 'observation': c.OBSERVATION,
            'objective': 'Observe full preparation and normal Linux userspace with matching cached PREPARED history; no UVD arm, decode or support claim.',
            'install_release': source['release'], 'install_boot_hashes': source['boot_hashes'], 'install_module_tree': source['module_tree'],
            'module_tree': {n: row['sha256'] for n, row in tree.items()}, 'native_restore_automatic': False,
            'tools': c.tool_pins(), 'observation_contract_sha256': contract_sha,
            'observation_tools': {n: observation['tools'][n]['sha256'] for n in observation_tools},
            'external_archives': manifest['external_archives'],
            'portability': 'Corresponding-source and build-input archives remain external, hash-pinned private references. This stage is not a self-contained public release.',
            'files': {n: row['sha256'] for n, row in measured.items()}}
    for key, name in (('kernel_sha256', 'bzImage'), ('config_sha256', 'config'), ('modules_archive_sha256', 'modules.tar.zst'), ('new_bootargs_sha256', 'bootargs.new')):
        plan[key] = plan['files']['bundle/' + name]
    c.validate_plan(plan)
    scratch = Path(tempfile.mkdtemp(prefix='.preparation-stage-', dir=destination.parent))
    stage = scratch / 'stage'; stage.mkdir(mode=0o700)
    try:
        for folder, rows in (('old-boot', old), ('install-from', current)):
            (stage / folder).mkdir(mode=0o700)
            for name, data in rows.items(): c.write(stage / folder / name, data)
        for name, path in bindings.items():
            output = stage / name; output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            c.b.copy_checked(path, output, measured[name])
        c.save(stage / 'plan.json', plan)
        c.load_inputs(stage)
        for folder, _, _ in os.walk(stage, topdown=False): c.t.fsync_path(Path(folder))
        c.require(not os.path.lexists(destination), 'Destination appeared during staging; preserve candidate')
        os.rename(stage, destination); c.t.fsync_path(destination.parent); scratch.rmdir()
    except BaseException as error:
        if scratch.exists():
            try: c.save(scratch / 'FAILURE.json', {'status': 'INCOMPLETE_STAGE_NO_INSTALL_AUTHORITY', 'error': str(error), 'hardware_acceptance': False})
            except OSError: pass
        raise
    return {'status': 'HOST_STAGE_VERIFIED_NO_DEVICES', 'stage': str(destination), 'plan_sha256': c.file_sha(destination / 'plan.json'),
            'experiment': exp, 'hardware_acceptance': False, 'installed': False, 'session_started': False,
            'final_review_required': True, 'observation_tools_executed': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'compatibility-status', 'compatibility-disposition', 'retained-ib-stage', 'retained-install-receipt', 'retained-install-complete', 'observation-contract', 'stage'):
        p.add_argument('--' + name, type=Path, required=True)
    for name in ('bundle-manifest-sha256', 'compiled-review-sha256', 'source-review-sha256', 'compatibility-status-sha256', 'observation-contract-sha256', 'experiment'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.bundle, a.bundle_manifest_sha256, a.compiled_review_sha256, a.source_review_sha256,
                            a.compatibility_status, a.compatibility_status_sha256, a.compatibility_disposition,
                            a.retained_ib_stage, a.retained_install_receipt, a.retained_install_complete,
                            a.observation_contract, a.observation_contract_sha256, a.experiment, a.stage), indent=2, sort_keys=True))

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic net1 module-only build guards; no kernel build, device or console operation."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('mts_reclaim_builder', HERE / 'build.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
CONFIG = b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-runtime-dev6"\nCONFIG_EXTRA_FIRMWARE=""\nCONFIG_INITRAMFS_SOURCE=""\n'


def patch(paths):
    return ''.join(f'diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n'
                   '@@ -1 +1 @@\n-before\n+after\n' for name in paths).encode()


class Guards(unittest.TestCase):
    def setUp(self):
        for name, raw in (('SOURCE_BEFORE_SHA', b'before\n'), ('SOURCE_AFTER_SHA', b'after\n')):
            guard = mock.patch.object(B, name, B.hashlib.sha256(raw).hexdigest())
            guard.start(); self.addCleanup(guard.stop)

    def directory(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-net1-guard-')
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def tree(self, root):
        for name, raw in (('.config', CONFIG), (B.DELTA[0], b'before\n'),
                          ('drivers/ata/libahci.c', b'protected SATA\n'),
                          ('drivers/ps4/ps4-bpcie-icc.c', b'protected ICC\n'),
                          ('drivers/gpu/.runtime.o.cmd', b'savedcmd := gcc -c runtime.c\n'),
                          ('drivers/gpu/runtime.o', b'synthetic compiled object\n')):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (root / 'link').symlink_to('drivers/ata/libahci.c')
        return B.cache_inventory(root)

    def test_config_is_preserved_and_refuses_payloads_or_wrong_base(self):
        self.assertEqual(B.changed_config(CONFIG), CONFIG)
        for raw in (CONFIG * 2, CONFIG.replace(b'dev6', b'dev5'),
                    CONFIG.replace(b'FIRMWARE=""', b'FIRMWARE="private.bin"'),
                    CONFIG.replace(b'INITRAMFS_SOURCE=""', b'INITRAMFS_SOURCE="private"')):
            with self.subTest(raw=raw), self.assertRaises(ValueError): B.changed_config(raw)

    def test_only_one_mts_source_path_can_change(self):
        good = patch(B.DELTA)
        self.assertEqual(B.patch_paths(good, B.DELTA), list(B.DELTA))
        for raw in (patch(B.DELTA + ('drivers/ata/libahci.c',)),
                    patch(('drivers/ps4/ps4-bpcie-icc.c',)), good + good,
                    good + b'new mode 100755\n', good.replace(b'+++ b/', b'+++ /dev/null ')):
            with self.subTest(raw=raw), self.assertRaises(ValueError): B.patch_paths(raw, B.DELTA)

    def test_code_inventory_protects_icc_sata_and_modes(self):
        root = self.directory()
        self.tree(root)
        names = (B.DELTA[0], 'drivers/ata/libahci.c', 'drivers/ps4/ps4-bpcie-icc.c')
        original = {name: B.C.file_record(root / name) for name in names}
        (root / B.DELTA[0]).write_text('after\n')
        expected = B.update_code_inventory(root, original)
        self.assertEqual(expected[names[1]], original[names[1]])
        for name in names[1:]:
            path = root / name
            saved = path.read_bytes()
            path.write_text('undeclared change\n')
            with self.assertRaisesRegex(ValueError, 'Source byte/mode identity'):
                B.update_code_inventory(root, original)
            path.write_bytes(saved)
        (root / B.DELTA[0]).chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'declared code change'):
            B.update_code_inventory(root, original)

    def test_cache_inventory_covers_objects_commands_modes_and_mtimes(self):
        root = self.directory()
        records = self.tree(root)
        source = {B.DELTA[0]: B.C.file_record(root / B.DELTA[0])}
        B.verify_source_inventory(records, source)
        for name, change in (('drivers/gpu/runtime.o', 'bytes'),
                             ('drivers/gpu/.runtime.o.cmd', 'bytes'),
                             ('drivers/gpu/runtime.o', 'mode'),
                             ('drivers/gpu/runtime.o', 'mtime')):
            with self.subTest(name=name, change=change):
                path = root / name; raw = path.read_bytes(); info = path.stat()
                if change == 'bytes': path.write_bytes(b'x' * len(raw))
                elif change == 'mode': path.chmod(0o755)
                else: os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
                self.assertNotEqual(B.inventory_digest(B.cache_inventory(root)), B.inventory_digest(records))
                path.write_bytes(raw); path.chmod(info.st_mode & 0o777)
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(B.cache_inventory(root), records)
        source[B.DELTA[0]]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'corresponding source differs'):
            B.verify_source_inventory(records, source)

    def test_cache_inventory_refuses_hardlinks_special_files_and_midread_changes(self):
        root = self.directory()
        self.tree(root)
        original = root / 'drivers/gpu/runtime.o'
        link = root / 'hardlink'; os.link(original, link)
        with self.assertRaisesRegex(ValueError, 'Hardlinked'): B.cache_inventory(root)
        link.unlink()
        fifo = root / 'fifo'; os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'Special cache'): B.cache_inventory(root)
        fifo.unlink()
        digest = B.C.digest
        def mutate(path):
            value = digest(path)
            if path == original: path.write_bytes(path.read_bytes() + b'changed')
            return value
        with mock.patch.object(B.C, 'digest', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'changed during read'): B.cache_inventory(root)

    def test_actual_reflink_copy_is_exact_independent_and_mutation_isolated(self):
        root = self.directory(); base = root / 'base'; base.mkdir()
        records = self.tree(base)
        copied = root / 'copy'
        subprocess.run(B.reflink_command(base, copied), check=True, capture_output=True)
        self.assertEqual(B.verify_clone(base, copied, records), B.inventory_digest(records))
        object_path = copied / 'drivers/gpu/runtime.o'
        object_path.write_text('changed only in clone\n')
        self.assertEqual((base / 'drivers/gpu/runtime.o').read_bytes(), b'synthetic compiled object\n')
        self.assertEqual(B.cache_inventory(base), records)
        with self.assertRaisesRegex(ValueError, 'clone bytes/modes/timestamps'):
            B.verify_clone(base, copied, records)

    def test_clone_rejects_a_shared_input_inode_or_added_entry(self):
        root = self.directory(); base = root / 'base'; base.mkdir()
        records = self.tree(base)
        with self.assertRaisesRegex(ValueError, 'shares an input inode'):
            B.verify_clone(base, base, records)
        copied = root / 'copy'; shutil.copytree(base, copied, symlinks=True)
        (copied / 'extra.o').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'inventory differ'):
            B.verify_clone(base, copied, records)

    def plan_fixture(self):
        root = self.directory(); base = root / 'package'; base.mkdir()
        cache = base / ('linux-' + B.BASE); cache.mkdir()
        records = self.tree(cache)
        (base / 'config').write_bytes(CONFIG)
        (base / 'bzImage').write_bytes(b'synthetic kernel')
        (base / ('source-' + B.BASE + '.tar.zst')).write_bytes(b'synthetic archive')
        original = {name: B.C.file_record(cache / name) for name in
                    ('.config', B.DELTA[0], 'drivers/ata/libahci.c', 'drivers/ps4/ps4-bpcie-icc.c')}
        (base / 'SOURCE-ARCHIVE.json').write_text(json.dumps(original))
        pins = {p.name: B.C.digest(p) for p in base.iterdir() if p.is_file()}
        terminal = root / 'terminal.patch'; terminal.write_bytes(patch(B.DELTA))
        args = argparse.Namespace(base_package=base, base_cache=cache, mts_patch=terminal,
                                  output=root / 'new', jobs=16, plan=True)
        return base, cache, records, pins, terminal, args

    def plan_guards(self, pins, terminal, records):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(B, 'PINS', pins))
        stack.enter_context(mock.patch.object(B, 'CACHE_PINS', {'.config': pins['config']}))
        stack.enter_context(mock.patch.object(B, 'MTS_PATCH_SHA', B.C.digest(terminal)))
        stack.enter_context(mock.patch.object(B, 'CACHE_INVENTORY_SHA', B.inventory_digest(records)))
        stack.enter_context(mock.patch.object(B, 'filesystem_type', return_value='btrfs'))
        stack.enter_context(mock.patch.object(B.os, 'cpu_count', return_value=16))
        stack.enter_context(mock.patch.object(B.shutil, 'disk_usage', return_value=argparse.Namespace(free=B.MINIMUM_FREE_BYTES)))
        stack.enter_context(mock.patch.object(B.subprocess, 'run', side_effect=AssertionError('plan invoked a build/copy command')))
        return stack

    def test_plan_checks_package_and_entire_cache_without_output_or_build(self):
        base, cache, records, pins, terminal, args = self.plan_fixture()
        with self.plan_guards(pins, terminal, records), contextlib.redirect_stdout(io.StringIO()) as output:
            B.build(args)
            self.assertIn('PLAN_ONLY', output.getvalue())
            self.assertFalse(args.output.exists())
            (cache / 'drivers/gpu/runtime.o').write_text('substituted object')
            with self.assertRaisesRegex(ValueError, 'complete dev6 build cache differs'): B.build(args)
            self.assertFalse(args.output.exists())
            (base / 'bzImage').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Frozen input differs'): B.build(args)

    def test_plan_refuses_wrong_jobs_overlap_pending_patch_and_non_btrfs(self):
        base, cache, records, pins, terminal, args = self.plan_fixture()
        with self.plan_guards(pins, terminal, records):
            for jobs in (1, 8, 17, True):
                args.jobs = jobs
                with self.assertRaisesRegex(ValueError, '16 logical'): B.build(args)
            args.jobs = 16
            with mock.patch.object(B, 'MTS_PATCH_SHA', None):
                with self.assertRaisesRegex(ValueError, 'not frozen'): B.build(args)
            with mock.patch.object(B, 'filesystem_type', return_value='ext4'):
                with self.assertRaisesRegex(ValueError, 'same Btrfs'): B.build(args)
            args.output = base / 'new'
            with self.assertRaisesRegex(ValueError, 'overlaps'): B.build(args)
            self.assertFalse(args.output.exists())

    def test_lineage_preserves_uvd_sata_icc_without_claiming_acceptance(self):
        parent = {'release': B.BASE, 'status': 'PASS_PACKAGE', 'module_count': 41,
                  'uvd_restart_experiment': {'post_start_failure_permitted': True},
                  'uvd_poweroff_experiment': {'retain_dma': True},
                  'restart_fix': {'atomic_registered_restart': True},
                  'sata_probe_experiment': {'data_write_filter': False},
                  'source_base_commit': 'c' * 40, 'uvd_lifecycle_sources_unchanged': False,
                  'artifacts': {'old': 'pin'}}
        snapshot = json.loads(json.dumps(parent))
        result = B.lineage_metadata(parent, {'checked': True}, {'build.py': 'hash'})
        self.assertEqual(parent, snapshot)
        for key in ('uvd_restart_experiment', 'uvd_poweroff_experiment', 'restart_fix', 'sata_probe_experiment'):
            self.assertEqual(result[key], parent[key])
        self.assertEqual(result['release'], B.BASE)
        self.assertEqual(result['variant'], 'mts-tx-reclaim-net1')
        self.assertEqual(result['config_delta'], [])
        self.assertTrue(result['kernel_unchanged'] and result['abi_unchanged'])
        self.assertEqual(result['mts_tx_reclaim_fix']['patch_sha256'], B.MTS_PATCH_SHA)
        self.assertFalse(result['hardware_acceptance'])
        self.assertIs(result['uvd_lifecycle_sources_unchanged'], False)
        self.assertTrue(result['uvd_sources_unchanged_from_dev6'])
        self.assertTrue(result['mts_tx_reclaim_experiment']['hypothesis_only'])
        with self.assertRaisesRegex(ValueError, 'complete dev6 parent'):
            B.lineage_metadata(dict(parent, release='unknown'), {}, {})

    def test_module_delta_rejects_any_other_module_or_changed_abi(self):
        mts = 'lib/modules/' + B.RELEASE + '/kernel/drivers/net/ethernet/psx_mts_net.ko'
        base = {f'other-{i}.ko': {'kind': 'file', 'mode': 0o644, 'bytes': 4,
                                'sha256': '0' * 64, 'vermagic': B.RELEASE} for i in range(40)}
        base[mts] = dict(next(iter(base.values())))
        valid = json.loads(json.dumps(base)); valid[mts]['sha256'] = '1' * 64
        B.verify_module_delta(valid, base)
        for change in ('other', 'same', 'mode', 'vermagic', 'missing'):
            current = json.loads(json.dumps(valid))
            if change == 'other': current['other-0.ko']['sha256'] = '2' * 64
            elif change == 'same': current[mts] = dict(base[mts])
            elif change == 'missing': del current['other-0.ko']
            elif change == 'mode': current[mts]['mode'] = 0o755
            else: current[mts]['vermagic'] = 'another release'
            with self.subTest(change=change), self.assertRaises(ValueError):
                B.verify_module_delta(current, base)

    def test_external_kbuild_targets_only_module_without_configuration_or_kernel_targets(self):
        command = B.module_build_command(Path('/verified-kernel'), Path('/isolated-module'))
        self.assertEqual(command, ['make', 'ARCH=x86_64', 'LOCALVERSION=', 'CC=gcc', 'HOSTCC=gcc',
                                   '-C', '/verified-kernel', 'M=/isolated-module', '-j16', 'modules'])
        self.assertFalse(any(target in command for target in
                             ('olddefconfig', 'syncconfig', 'bzImage', 'vmlinux', 'modules_install')))
        self.assertNotIn('intree', B.EXTERNAL_MAKEFILE.decode())

    def test_external_sources_are_exact_and_preparation_keeps_config_untouched(self):
        root = self.directory(); source = root / 'source'; source.mkdir()
        metadata = source / 'PSX-MTS-TX-RECLAIM'; metadata.mkdir()
        driver = source / 'drivers/net/ethernet'; driver.mkdir(parents=True)
        (driver / 'psx_mts_net.c').write_bytes(b'after\n')
        header = driver / 'mts_net_core.h'; header.write_bytes(b'original core header\n')
        config = source / '.config'; config.write_bytes(CONFIG); before = config.stat()
        original = {'drivers/net/ethernet/mts_net_core.h': B.C.file_record(header)}
        external = B.prepare_external_module(source, metadata, original)
        self.assertEqual((external / 'psx_mts_net.c').read_bytes(), b'after\n')
        self.assertEqual((external / 'mts_net_core.h').read_bytes(), header.read_bytes())
        self.assertEqual((external / 'Makefile').read_bytes(), b'obj-m := psx_mts_net.o\n')
        self.assertEqual(config.read_bytes(), CONFIG)
        self.assertEqual(config.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertFalse((source / 'Module.symvers').exists())

    def test_external_preparation_rejects_header_drift_and_symlink(self):
        root = self.directory(); driver = root / 'drivers/net/ethernet'; driver.mkdir(parents=True)
        (driver / 'psx_mts_net.c').write_bytes(b'after\n')
        header = driver / 'mts_net_core.h'; header.write_bytes(b'original\n')
        original = {'drivers/net/ethernet/mts_net_core.h': B.C.file_record(header)}
        for name, symlink in (('drift', False), ('link', True)):
            metadata = root / name; metadata.mkdir()
            header.unlink()
            if symlink: header.symlink_to(root / 'another')
            else: header.write_bytes(b'changed\n')
            with self.subTest(symlink=symlink), self.assertRaises(ValueError):
                B.prepare_external_module(root, metadata, original)

    def test_incremental_commands_keep_compiler_spelling_and_mandatory_reflink(self):
        self.assertEqual(B.make_command(), ['make', 'ARCH=x86_64', 'LOCALVERSION=', 'CC=gcc', 'HOSTCC=gcc'])
        command = B.reflink_command(Path('/preserved'), Path('/new'))
        self.assertEqual(command, ['cp', '-a', '--reflink=always', '--', '/preserved', '/new'])


if __name__ == '__main__':
    unittest.main()

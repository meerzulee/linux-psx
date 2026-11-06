#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU-only boundary checks; never compile, install or operate hardware."""
import argparse
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('power_restart_builder', HERE / 'build_power_restart.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


def patch(paths):
    return ''.join(f'diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n'
                   '@@ -1 +1 @@\n-before\n+after\n' for name in paths).encode()


CONFIG = b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-runtime-dev4"\nCONFIG_EXTRA_FIRMWARE=""\nCONFIG_INITRAMFS_SOURCE=""\n'


class Guards(unittest.TestCase):
    def test_config_changes_only_release_and_refuses_implicit_payloads(self):
        self.assertEqual(B.changed_config(CONFIG).replace(b'dev5', b'dev4'), CONFIG)
        for bad in (CONFIG.replace(b'dev4', b'dev3'), CONFIG * 2,
                    CONFIG.replace(b'FIRMWARE=""', b'FIRMWARE="device.bin"'),
                    CONFIG.replace(b'INITRAMFS_SOURCE=""', b'INITRAMFS_SOURCE="private"')):
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                B.changed_config(bad)

    def test_patch_scope_checks_actual_headers_modes_and_declared_paths(self):
        good = patch(B.ICC_DELTA)
        self.assertEqual(B.patch_paths(good, B.ICC_DELTA), list(B.ICC_DELTA))
        invalid = (patch(B.ICC_DELTA + ('drivers/ata/libahci.c',)), patch(B.ICC_DELTA[:1]),
                   good + good, good + b'new mode 100755\n',
                   good.replace(b'--- a/' + B.ICC_DELTA[0].encode(), b'--- a/drivers/ata/libahci.c'),
                   good.replace(b'+++ b/' + B.ICC_DELTA[0].encode(), b'+++ /dev/null'))
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                B.patch_paths(raw, B.ICC_DELTA)

    def test_whole_tree_protects_sata_and_unrelated_modes_and_bytes(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            source = Path(temporary)
            protected = 'drivers/ata/libahci.c'
            original = {}
            for name in (*B.DELTA, protected):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('before\n')
                original[name] = B.C.file_record(path)
            for name in B.DELTA:
                (source / name).write_text('after\n')
            expected = B.update_code_inventory(source, original)
            self.assertEqual(expected[protected], original[protected])
            (source / protected).write_text('unrelated change\n')
            with self.assertRaisesRegex(ValueError, 'Source byte/mode identity'):
                B.update_code_inventory(source, original)
            (source / protected).write_text('before\n')
            (source / B.DELTA[0]).chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'declared code change'):
                B.update_code_inventory(source, original)

    def test_plan_verifies_frozen_inputs_without_creating_output(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            root = Path(temporary)
            base = root / 'base'; base.mkdir()
            for name, data in (('config', CONFIG), ('bzImage', b'kernel'),
                               ('source-' + B.BASE + '.tar.zst', b'archive')):
                (base / name).write_bytes(data)
            pins = {p.name: B.C.digest(p) for p in base.iterdir()}
            icc = root / 'icc.patch'; icc.write_bytes(patch(B.ICC_DELTA))
            uvd = root / 'uvd.patch'; uvd.write_bytes(patch(B.UVD_DELTA))
            args = argparse.Namespace(base_package=base, icc_patch=icc, uvd_patch=uvd,
                                      output=root / 'new', jobs=1, plan=True)
            with mock.patch.object(B, 'PINS', pins), \
                    mock.patch.object(B, 'ICC_PATCH_SHA', B.C.digest(icc)), \
                    mock.patch.object(B, 'UVD_PATCH_SHA', B.C.digest(uvd)), \
                    mock.patch.object(B.shutil, 'disk_usage', return_value=argparse.Namespace(free=B.MINIMUM_FREE_BYTES)), \
                    mock.patch.object(B.subprocess, 'run', side_effect=AssertionError('build from plan')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                B.build(args)
                self.assertIn('PLAN_ONLY', output.getvalue())
                self.assertFalse(args.output.exists())
                (base / ('source-' + B.BASE + '.tar.zst')).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'Frozen input differs'):
                    B.build(args)
                self.assertFalse(args.output.exists())

    def test_hash_guard_refuses_symlink_and_pending_hash(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            root = Path(temporary)
            path = root / 'input'; path.write_text('frozen')
            link = root / 'link'; link.symlink_to(path)
            B.checked_file(path, B.C.digest(path))
            for candidate, pin in ((link, B.C.digest(path)), (path, '0' * 64), (path, None)):
                with self.subTest(candidate=candidate, pin=pin), self.assertRaises(ValueError):
                    B.checked_file(candidate, pin)


if __name__ == '__main__':
    unittest.main()

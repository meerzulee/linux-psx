#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU-only checks for the exact retained-DMA power-off derivative boundary."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('shutdown_builder', HERE / 'build_shutdown_test.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
G = B.load('shutdown_generator', HERE / 'build_candidate.py')


class Guards(unittest.TestCase):
    def test_config_changes_only_release_and_rejects_firmware(self):
        raw = b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-runtime-dev2"\nCONFIG_EXTRA_FIRMWARE=""\n'
        new = B.changed_config(raw)
        self.assertEqual(new.replace(b'uvd-runtime-dev3', b'uvd-runtime-dev2'), raw)
        for bad in (raw.replace(b'dev2', b'other'), raw.replace(b'FIRMWARE=""', b'FIRMWARE="secret"')):
            with self.assertRaises(ValueError): B.changed_config(bad)

    def test_change_is_confined_to_shutdown_without_touching_remove(self):
        old = ('\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n'
               '\t\tamdgpu_ps4_uvd_runtime_park(adev, "amdgpu_pci_shutdown");\n')
        remove = old.replace('amdgpu_pci_shutdown', 'amdgpu_pci_remove')
        raw = 'prefix\n' + remove + old + 'suffix\n'
        guard = G.pci_shutdown_retention_guard().lstrip('\n')
        changed = B.changed_driver(raw, guard)
        self.assertEqual(changed.replace(guard, old, 1), raw)
        self.assertIn(remove, changed)
        for bad in (remove, old + old, old.replace('adev', 'other')):
            with self.assertRaises(ValueError): B.changed_driver(bad, guard)

    def test_source_verification_refuses_modes_and_symlink_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); p = root / 'input.c'; p.write_text('exact')
            expected = {'input.c': B.C.file_record(p)}
            B.N.verify_tree(root, expected)
            p.chmod(0o600)
            with self.assertRaises(ValueError): B.N.verify_tree(root, expected)
            p.unlink(); target = root / 'target'; target.write_text('exact'); p.symlink_to(target)
            with self.assertRaises(ValueError): B.N.verify_tree(root, expected)


if __name__ == '__main__': unittest.main()

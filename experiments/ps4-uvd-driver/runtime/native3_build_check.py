#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite synthetic checks for the native3/UVD source composition contract."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native3_builder', Path(__file__).with_name('build_native3.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Guards(unittest.TestCase):
    def test_native3_config_changes_only_release(self):
        raw = b'CONFIG_LOCALVERSION="-psx-ps4-baikal-native-dev3"\nCONFIG_EXTRA_FIRMWARE=""\nCONFIG_DRM_AMDGPU=y\n'
        changed = builder.changed_config(raw)
        self.assertEqual(changed.replace(b'-uvd-runtime-dev2', b'-native-dev3'), raw)

    def test_wrong_base_and_embedded_firmware_are_refused(self):
        for raw in (b'CONFIG_LOCALVERSION="wrong"\nCONFIG_EXTRA_FIRMWARE=""\n',
                    b'CONFIG_LOCALVERSION="-psx-ps4-baikal-native-dev3"\nCONFIG_EXTRA_FIRMWARE="blob"\n'):
            with self.assertRaises(ValueError): builder.changed_config(raw)

    def test_overlay_has_no_native_connector_audio_platform_or_power_substitution(self):
        self.assertEqual(len(builder.OVERLAY), 41)
        self.assertEqual(len(set(builder.OVERLAY)), 41)
        self.assertTrue(all(name.startswith(builder.PREFIX) for name in builder.OVERLAY))
        self.assertNotIn(builder.PREFIX + 'amdgpu_connectors.c', builder.OVERLAY)
        self.assertNotIn(builder.PREFIX + 'dce_v8_0.c', builder.OVERLAY)
        self.assertIn(builder.PREFIX + 'amdgpu_ps4_uvd_runtime.c', builder.OVERLAY)

    def test_source_verification_refuses_byte_and_mode_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'file.c'; path.write_bytes(b'known source')
            record = builder.C.file_record(path)
            builder.verify_tree(root, {'file.c': record})
            path.write_bytes(b'changed source')
            with self.assertRaises(ValueError): builder.verify_tree(root, {'file.c': record})


if __name__ == '__main__': unittest.main()

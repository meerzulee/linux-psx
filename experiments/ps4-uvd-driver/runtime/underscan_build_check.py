#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic guards for the private underscan derivative builder; no console."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('underscan_builder', Path(__file__).with_name('build_underscan.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Guards(unittest.TestCase):
    def base_config(self):
        return (b'CONFIG_EXTRA_FIRMWARE=""\n'
                b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-runtime-dev1"\n'
                b'CONFIG_MODULES=y\n')

    def test_only_release_changes(self):
        before = self.base_config()
        after = builder.changed_config(before)
        self.assertEqual(after.replace(b'-uvd-runtime-underscan-dev1', b'-uvd-runtime-dev1'), before)

    def test_missing_release_rejected(self):
        with self.assertRaises(ValueError):
            builder.changed_config(b'CONFIG_EXTRA_FIRMWARE=""\n')

    def test_duplicate_release_rejected(self):
        with self.assertRaises(ValueError):
            builder.changed_config(self.base_config() * 2)

    def test_firmware_embedding_rejected(self):
        with self.assertRaises(ValueError):
            builder.changed_config(self.base_config().replace(b'CONFIG_EXTRA_FIRMWARE=""', b'CONFIG_EXTRA_FIRMWARE="blob"'))

    def test_archive_prefix_removed(self):
        self.assertEqual(builder.member_name('linux-example/drivers/a.c', 'linux-example'), 'drivers/a.c')

    def test_archive_escape_or_wrong_prefix_rejected(self):
        for name in ('/linux-example/a', 'linux-example/../a', 'other/a'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                builder.member_name(name, 'linux-example')


if __name__ == '__main__':
    unittest.main()

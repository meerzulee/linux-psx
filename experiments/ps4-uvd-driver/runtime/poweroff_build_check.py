#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Source/config/archive guards only; no compiler, console or device operations."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('poweroff_builder', Path(__file__).with_name('build_poweroff.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Guards(unittest.TestCase):
    def config(self):
        return (b'CONFIG_EXTRA_FIRMWARE=""\n'
                b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-runtime-dev1"\n'
                b'CONFIG_DRM_AMDGPU=y\nCONFIG_MODULES=y\n')

    def test_only_release_changes_and_runtime_identity_is_distinct(self):
        before = self.config()
        after = builder.changed_config(before)
        self.assertEqual(after.replace(b'-uvd-runtime-dev2', b'-uvd-runtime-dev1'), before)
        self.assertEqual(builder.RELEASE, '7.2.11-psx-ps4-baikal-uvd-runtime-dev2')

    def test_wrong_duplicate_or_firmware_config_is_refused(self):
        for raw in (b'CONFIG_EXTRA_FIRMWARE=""\n', self.config() * 2,
                    self.config().replace(b'CONFIG_EXTRA_FIRMWARE=""', b'CONFIG_EXTRA_FIRMWARE="blob"')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                builder.changed_config(raw)

    def test_archive_paths_cannot_escape_or_change_source_root(self):
        self.assertEqual(builder.member_name('linux-example/drivers/a.c', 'linux-example'), 'drivers/a.c')
        for name in ('/linux-example/a', 'linux-example/../a', 'other/a'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                builder.member_name(name, 'linux-example')

    def test_lifecycle_sources_are_checked_individually_and_may_not_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            prefix = source / 'drivers/gpu/drm/amd/amdgpu'
            prefix.mkdir(parents=True)
            candidates = {'amdgpu_drv.c': b'retained shutdown park',
                          'amdgpu_ps4_uvd_runtime.c': b'hw_fini EBUSY, no_suspend EBUSY'}
            for name, raw in candidates.items(): (prefix / name).write_bytes(raw)
            receipt = {'candidate_files': {name: hashlib.sha256(raw).hexdigest() for name, raw in candidates.items()}}
            builder.verify_lifecycle(source, receipt)
            (prefix / 'amdgpu_drv.c').write_bytes(b'guard removed')
            with self.assertRaisesRegex(ValueError, 'Protected UVD source changed'):
                builder.verify_lifecycle(source, receipt)

    def test_exact_reviewed_patch_and_attribution_are_preserved(self):
        root = Path(__file__).resolve().parents[3]
        patch = root / 'platforms/ps4/patches/7.2-ps4-baikal/0200-ps4-drivers/0001-ps4-bpcie-icc-power-off-before-acpi.patch'
        self.assertEqual(builder.digest(patch), builder.PATCH_SHA)
        text = patch.read_text()
        self.assertIn('From: suds-mcduff', text)
        self.assertIn('reboot_force || READ_ONCE(sc->icc.reply_pending)', text)
        self.assertIn('SYS_OFF_PRIO_FIRMWARE + 1', text)
        paths = [line[6:] for line in text.splitlines() if line.startswith('+++ b/')]
        self.assertEqual(paths, [builder.ICC])


if __name__ == '__main__':
    unittest.main()

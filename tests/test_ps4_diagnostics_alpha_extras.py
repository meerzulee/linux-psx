"""Offline alpha pack contracts; no browser, GPU, mount or console access."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('psx_alpha_extras_tests', str(ROOT/'scripts/prepare-ps4-diagnostics-alpha-extras'))
spec = importlib.util.spec_from_loader(loader.name, loader)
APP = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = APP
loader.exec_module(APP)
ALPHA = json.loads((ROOT/'manifests/ps4-diagnostics-uvd-alpha1.json').read_text())


class AlphaExtrasTests(unittest.TestCase):
    def test_only_one_expected_module_literal_can_be_adapted(self):
        original = "#!/usr/bin/python3\nRELEASE = 'old'\nURL = 'https://example.org/a?b=1'\ndef unused():\n    raise RuntimeError('must never run')\n"
        adapted = APP.replace_assignment(original, 'RELEASE', 'old', 'alpha')
        self.assertIn("RELEASE = 'alpha'", adapted)
        self.assertIn("URL = 'https://example.org/a?b=1'", adapted)
        for source, expected in ((original, 'different'), (original+"RELEASE = 'old'\n", 'old')):
            with self.assertRaisesRegex(ValueError, 'literal differs'):
                APP.replace_assignment(source, 'RELEASE', expected, 'alpha')

    def test_provider_flags_and_expected_pins_are_literal_data(self):
        original = "EXPECTED = {'release': 'native', 'root': 'old'}\nLAUNCH_CONFIG = {'pin': EXPECTED['root']}\n"
        changed = APP.replace_assignment(original, 'EXPECTED', {'release': 'native', 'root': 'old'},
                                         {'release': 'alpha', 'root': 'new'})
        self.assertIn("LAUNCH_CONFIG = {'pin': EXPECTED['root']}", changed)
        self.assertEqual(APP.VIDEO_FLAG, '--disable-accelerated-video-decode')

    def test_wayland_preserves_provider_executables_while_rebinding_exact_kernel(self):
        old = {'kernel_release': ALPHA['alpha_base']['kernel_release'],
               'mesa_manifest_sha256': ALPHA['mesa_manifest_sha256'],
               'maximum_unpacked_bytes': 1024*1024, 'hardware_acceptance': False}
        members = {'bin/cage': (b'synthetic provider executable', 0o755),
                   'licenses/cage-MIT.txt': (b'synthetic preserved license', 0o644),
                   'runtime.py': (b'old runtime', 0o755), 'suite.json': (b'old receipt', 0o644)}
        manifest, changed = APP.wayland_members(ALPHA, old, members)
        for name in ('bin/cage', 'licenses/cage-MIT.txt'):
            self.assertEqual(changed[name], members[name])
        self.assertEqual(manifest['kernel_release'], ALPHA['kernel_release'])
        self.assertEqual(manifest['kernel_sha256'], ALPHA['kernel_sha256'])
        self.assertFalse(manifest['hardware_acceptance'])
        self.assertFalse(manifest['hardware_run'])
        self.assertEqual(changed['runtime.py'][0], (ROOT/'diagnostics/ps4/wayland/runtime.py').read_bytes())
        self.assertEqual(set(manifest['files']), set(members)-{'suite.json'})
        self.assertEqual(members['runtime.py'][0], b'old runtime')

    def test_wayland_wrong_stack_and_excess_ram_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'kernel/Mesa'):
            APP.wayland_members(ALPHA, {}, {})
        old = {'kernel_release': ALPHA['alpha_base']['kernel_release'],
               'mesa_manifest_sha256': ALPHA['mesa_manifest_sha256'], 'maximum_unpacked_bytes': 1}
        with self.assertRaisesRegex(ValueError, 'RAM budget'):
            APP.wayland_members(ALPHA, old, {})

    def test_profile_rejects_native_or_unpinned_relabeling(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            path = Path(temporary)/'profile.json'
            for changes in ({'uvd_runtime': False}, {'kernel_sha256': 'a'*64}, {'kernel_release': 'native'}):
                path.write_text(json.dumps(dict(ALPHA, **changes)))
                with self.assertRaisesRegex(ValueError, 'frozen UVD'):
                    APP.profile(path)

    def test_offline_image_writer_cannot_mutate_a_shared_hardlink_or_device(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            image = Path(temporary)/'original.ext4'
            image.write_bytes(b'synthetic regular image')
            os.link(image, Path(temporary)/'copy.ext4')
            with mock.patch.object(APP.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'new owned regular image'):
                    APP.debugfs(image, 'rm /PROVIDER.json', write=True)
                with self.assertRaisesRegex(ValueError, 'new owned regular image'):
                    APP.debugfs(Path('/dev/null'), 'rm /PROVIDER.json', write=True)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

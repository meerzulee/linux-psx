"""Synthetic Mesa selection tests; fake driver bytes, never GPU access."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'userspace/mesa/with-mesa.py'
spec = importlib.util.spec_from_file_location('mesa_selection', SCRIPT)
MESA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MESA)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / 'candidate'
        self.root.mkdir()
        required = ('usr/lib/libEGL_mesa.so.0', 'usr/lib/libGLX_mesa.so.0', 'usr/lib/libgbm.so.1',
                    'usr/lib/dri/radeonsi_dri.so', 'usr/lib/dri/radeonsi_drv_video.so',
                    'usr/lib/libvulkan_radeon.so', 'usr/lib/gbm/dri_gbm.so')
        files = {}
        for name in (*required, 'usr/share/vulkan/icd.d/radeon_icd.x86_64.json'):
            data = (json.dumps({'ICD': {'library_path': 'libvulkan_radeon.so'}}).encode()
                    if name.endswith('.json') else ('synthetic data: ' + name).encode())
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        inputs = json.loads(SCRIPT.with_name('sources.json').read_text())
        manifest = {'schema': 1, 'hardware_acceptance': False,
                    'status': 'BUILT_TESTED_STAGED_NOT_HARDWARE_QUALIFIED',
                    'mesa_version': inputs['upstream']['version'], 'inputs': inputs, 'files': files}
        (self.root / 'MESA-PSX-CANDIDATE.json').write_text(json.dumps(manifest))

    def tearDown(self):
        for path in self.base.rglob('*'):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        self.root.chmod(0o700)
        self.temporary.cleanup()

    def call(self, *args):
        output = io.StringIO()
        with patch.object(sys, 'argv', [str(SCRIPT), '--root', str(self.root), *map(str, args)]), \
                patch('sys.stdout', output), patch.object(MESA.os, 'execvpe') as execute:
            result = MESA.main()
        return result, output.getvalue(), execute

    def test_readonly_candidate_uses_separate_private_ram_selection(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        for path in self.root.rglob('*'):
            path.chmod(0o555 if path.is_dir() else 0o444)
        self.root.chmod(0o555)
        runtime = self.base / 'run' / str(os.geteuid())
        result, output, execute = self.call('--runtime-dir', runtime, '--print-env')
        self.assertEqual(result, 0)
        execute.assert_not_called()
        environment = json.loads(output)
        self.assertEqual(Path(environment['MESA_SHADER_CACHE_DIR']), runtime / 'shader-cache')
        self.assertEqual(Path(environment['VK_DRIVER_FILES']), runtime / 'vulkan.json')
        egl = json.loads((runtime / 'egl.json').read_text())
        self.assertEqual(egl['ICD']['library_path'], str(self.root / 'usr/lib/libEGL_mesa.so.0'))
        self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o700)
        self.assertFalse((self.root / 'selection').exists())
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_legacy_default_selection_location_is_preserved(self):
        _, output, execute = self.call('--print-env')
        self.assertEqual(Path(json.loads(output)['VK_DRIVER_FILES']), self.root / 'selection/vulkan.json')
        execute.assert_not_called()

    def test_verify_only_does_not_create_or_validate_runtime_state(self):
        runtime = self.base / 'absent'
        _, output, execute = self.call('--verify-only', '--runtime-dir', runtime)
        self.assertTrue(json.loads(output)['verified'])
        self.assertFalse(runtime.exists())
        self.assertFalse((self.root / 'selection').exists())
        execute.assert_not_called()

    def test_symlink_and_public_runtime_directories_are_rejected(self):
        public = self.base / 'public'; public.mkdir(mode=0o755)
        public.chmod(0o755)
        private = self.base / 'private'; private.mkdir(mode=0o700)
        link = self.base / 'link'; link.symlink_to(private)
        for path in (public, link):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call('--runtime-dir', path, '--print-env')
        self.assertEqual(list(private.iterdir()), [])

    def test_another_users_runtime_directory_is_rejected(self):
        runtime = self.base / 'private'; runtime.mkdir(mode=0o700)
        with patch.object(MESA.os, 'geteuid', return_value=runtime.stat().st_uid + 1), self.assertRaises(ValueError):
            self.call('--runtime-dir', runtime, '--print-env')
        self.assertEqual(list(runtime.iterdir()), [])

    def test_selection_cannot_modify_the_verified_driver_tree(self):
        with self.assertRaises(ValueError):
            self.call('--runtime-dir', self.root / 'usr/runtime', '--print-env')
        self.assertFalse((self.root / 'usr/runtime').exists())

    def test_verified_usr_alias_cannot_bypass_runtime_separation(self):
        actual = self.root / 'driver-tree'
        (self.root / 'usr').rename(actual)
        (self.root / 'usr').symlink_to(actual.name)
        with self.assertRaises(ValueError):
            self.call('--runtime-dir', actual / 'runtime', '--print-env')
        self.assertFalse((actual / 'runtime').exists())

    def test_command_receives_runtime_selection_without_executing_a_gpu_program(self):
        runtime = self.base / 'private'
        result, _, execute = self.call('--runtime-dir', runtime, '--', 'synthetic-command', 'argument')
        self.assertEqual(result, 0)
        execute.assert_called_once()
        binary, arguments, environment = execute.call_args.args
        self.assertEqual((binary, arguments), ('synthetic-command', ['synthetic-command', 'argument']))
        self.assertEqual(environment['__EGL_VENDOR_LIBRARY_FILENAMES'], str(runtime / 'egl.json'))

    def test_existing_output_symlink_cannot_redirect_a_json_write(self):
        runtime = self.base / 'private'; runtime.mkdir(mode=0o700)
        other = self.base / 'unrelated'; other.write_text('preserve')
        (runtime / 'egl.json').symlink_to(other)
        self.call('--runtime-dir', runtime, '--print-env')
        self.assertEqual(other.read_text(), 'preserve')
        self.assertFalse((runtime / 'egl.json').is_symlink())


if __name__ == '__main__':
    unittest.main()

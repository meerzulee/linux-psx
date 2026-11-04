"""Automatic-only PID1 browser deadlines; filesystem fixtures and fake commands."""
import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('psx_browser_runtime',str(ROOT/'rootfs/ps4-diagnostics-desktop/launch-graphics'))
spec=importlib.util.spec_from_loader(loader.name,loader)
APP=importlib.util.module_from_spec(spec);loader.exec_module(APP)


class BrowserRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir='/var/tmp');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.calls=[];self.windowed=False
        for patch in [mock.patch.object(APP,'SYSTEMD_RUNTIME',self.root/'systemd'),
                      mock.patch.object(APP.os,'geteuid',return_value=0),
                      mock.patch.object(APP.os,'uname',return_value=SimpleNamespace(release='fixture-kernel')),
                      mock.patch.object(APP.Path,'read_text',return_value='{"kernel_release":"fixture-kernel"}'),
                      mock.patch.object(APP.os,'access',return_value=True),
                      mock.patch.object(APP.subprocess,'run',side_effect=self.command)]:
            patch.start();self.addCleanup(patch.stop)

    def command(self,argv,**kwargs):
        self.calls.append(argv)
        active=self.windowed and argv[-1]=='psx-diagnostics-graphics@diagnostics.service'
        return SimpleNamespace(stdout='active\n' if active else 'inactive\n')

    def run_launch(self,*arguments):
        with mock.patch.object(APP.sys,'argv',['launch-graphics',*arguments]),contextlib.redirect_stdout(io.StringIO()):
            APP.main()

    def test_shared_display_auto_has_independent_limit_and_manual_is_unlimited(self):
        self.windowed=True;self.run_launch('--automatic','browser')
        self.assertIn('--property=RuntimeMaxSec=120',self.calls[-1])
        self.assertIn('--unit=psx-diagnostics-window-browser.service',self.calls[-1])
        self.calls.clear();self.run_launch('browser')
        self.assertFalse(any('RuntimeMaxSec' in arg for arg in self.calls[-1]))
        self.assertFalse(list(self.root.rglob('*.conf')))

    def test_requested_firefox_and_chromium_launch_the_matching_window_on_active_menu(self):
        self.windowed=True
        for app in ('browser','chromium'):
            for prefix in ([],['--automatic']):
                self.calls.clear();self.run_launch(*prefix,app)
                launched=self.calls[-1]
                self.assertEqual(launched[0],'systemd-run')
                self.assertIn('--unit=psx-diagnostics-window-'+app+'.service',launched)
                self.assertEqual(launched[-2:],['/run/psx-frontend/graphics-window',app])
                self.assertIn('--property=BindsTo=psx-diagnostics-graphics@diagnostics.service',launched)
                self.assertEqual('--property=RuntimeMaxSec=120' in launched,bool(prefix))

    def test_standalone_auto_limit_survives_controller_and_manual_removes_only_owned_dropin(self):
        self.run_launch('--automatic','chromium')
        path=self.root/'systemd/psx-diagnostics-graphics@chromium.service.d/50-psx-browser-runtime.conf'
        self.assertEqual(path.read_bytes(),b'[Service]\nRuntimeMaxSec=120s\n')
        self.assertLess(self.calls.index(['systemctl','daemon-reload']),self.calls.index(['systemctl','start','psx-diagnostics-graphics@chromium.service']))
        self.calls.clear();self.run_launch('chromium')
        self.assertFalse(path.exists());self.assertIn(['systemctl','daemon-reload'],self.calls)

    def test_auto_flag_cannot_limit_diagnostic_menu_or_accept_unknown_modes(self):
        for args in [('--automatic','diagnostics'),('--automatic','opengl'),('--automatic','protondb'),('--automatic','anything'),('--automatic',)]:
            with self.assertRaises(ValueError):self.run_launch(*args)
        self.assertEqual(self.calls,[]);self.assertFalse(list(self.root.rglob('*.conf')))

    def test_unknown_or_symlink_runtime_dropin_is_never_overwritten_or_removed(self):
        directory=self.root/'systemd/psx-diagnostics-graphics@browser.service.d';directory.mkdir(parents=True)
        path=directory/'50-psx-browser-runtime.conf';path.write_bytes(b'unrelated config')
        with self.assertRaises(ValueError):self.run_launch('browser')
        self.assertEqual(path.read_bytes(),b'unrelated config')
        path.unlink();outside=self.root/'outside';outside.write_bytes(b'unchanged');path.symlink_to(outside)
        with self.assertRaises(ValueError):self.run_launch('--automatic','browser')
        self.assertEqual(outside.read_bytes(),b'unchanged')

    def test_protondb_uses_fixed_manual_services_with_no_runtime_deadline(self):
        self.windowed=True;self.run_launch('protondb')
        self.assertIn('--unit=psx-diagnostics-window-protondb.service',self.calls[-1])
        self.assertEqual(self.calls[-1][-2:],['/run/psx-frontend/graphics-window','protondb'])
        self.assertFalse(any('RuntimeMaxSec' in arg for arg in self.calls[-1]))
        self.windowed=False;self.calls.clear();self.run_launch('protondb')
        self.assertEqual(self.calls[-1],['systemctl','start','psx-diagnostics-graphics@protondb.service'])
        self.assertFalse(list(self.root.rglob('*.conf')))

    def test_active_protondb_blocks_other_apps_and_arbitrary_url_arguments_are_rejected(self):
        with mock.patch.object(APP,'active_state',side_effect=lambda unit:'active' if unit=='psx-diagnostics-window-protondb.service' else 'inactive'):
            with self.assertRaisesRegex(ValueError,'already active'):self.run_launch('browser')
        with self.assertRaises(ValueError):self.run_launch('protondb','https://example.invalid/')
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()

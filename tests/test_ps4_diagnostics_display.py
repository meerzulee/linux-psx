"""Read-only RandR queries with synthetic pointer storage; no display access."""
import ctypes as C
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader(
    'psx_display_query', str(ROOT / 'rootfs/ps4-diagnostics-desktop/wait-graphics-wm'))
spec = importlib.util.spec_from_loader(loader.name, loader)
APP = importlib.util.module_from_spec(spec)
loader.exec_module(APP)


class DisplayQueryTests(unittest.TestCase):
    def setUp(self):
        self.sizes = (APP.ScreenSize * 2)(APP.ScreenSize(1280, 720, 0, 0),
                                         APP.ScreenSize(1920, 1080, 0, 0))
        def current(_config, rotation):
            C.cast(rotation, C.POINTER(C.c_ushort))[0] = 1
            return 1
        def sizes(_config, count):
            C.cast(count, C.POINTER(C.c_int))[0] = 2
            return self.sizes
        self.randr = SimpleNamespace(
            XRRGetScreenInfo=Mock(return_value=42),
            XRRFreeScreenConfigInfo=Mock(),
            XRRConfigCurrentConfiguration=Mock(side_effect=current),
            XRRConfigSizes=Mock(side_effect=sizes),
            XRRConfigCurrentRate=Mock(return_value=60))
        self.library = Mock(return_value=self.randr)
        self.connection = Mock()
        self.connection.__enter__ = Mock(return_value=SimpleNamespace(display=11, root=22))
        self.connection.__exit__ = Mock(return_value=False)
        self.factory = Mock(return_value=self.connection)

    def test_selects_current_size_and_nominal_rate_without_setting_anything(self):
        result = APP.display_info(self.factory, self.library)
        self.assertEqual((result['width'], result['height'], result['configured_refresh_hz']),
                         (1920, 1080, 60))
        self.assertIn('not measured physical refresh', result['scope'])
        self.randr.XRRGetScreenInfo.assert_called_once_with(11, 22)
        self.randr.XRRFreeScreenConfigInfo.assert_called_once_with(42)
        self.connection.__exit__.assert_called_once()
        self.library.assert_called_once_with('libXrandr.so.2')

    def test_invalid_index_is_rejected_and_configuration_freed(self):
        self.randr.XRRConfigCurrentConfiguration.side_effect = None
        self.randr.XRRConfigCurrentConfiguration.return_value = 2
        with self.assertRaisesRegex(RuntimeError, 'index'):
            APP.display_info(self.factory, self.library)
        self.randr.XRRFreeScreenConfigInfo.assert_called_once_with(42)

    def test_unknown_rate_stays_unknown(self):
        self.randr.XRRConfigCurrentRate.return_value = 0
        self.assertIsNone(APP.display_info(self.factory, self.library)['configured_refresh_hz'])

    def test_missing_extension_is_rejected_without_freeing_null(self):
        self.randr.XRRGetScreenInfo.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'no RandR'):
            APP.display_info(self.factory, self.library)
        self.randr.XRRFreeScreenConfigInfo.assert_not_called()


if __name__ == '__main__':
    unittest.main()

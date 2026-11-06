# SPDX-License-Identifier: MIT
"""ICC device setup with synthetic mount data/syscalls; no host device access."""
import errno
import importlib.util
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('power_device_controller', ROOT / 'diagnostics/ps4/controller.py')
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)
MOUNTS = (b'1 0 7:0 / / ro - ext4 /dev/loop0 ro\n'
          b'2 1 0:5 / /dev rw,nosuid - devtmpfs devtmpfs rw,mode=755\n'
          b'3 1 0:6 / /run rw,nosuid,nodev - tmpfs tmpfs rw\n')


def metadata(mode, inode, **changes):
    return SimpleNamespace(**dict(dict(st_mode=mode, st_uid=0, st_gid=0,
        st_dev=5, st_ino=inode, st_rdev=os.makedev(73, 0)), **changes))


class PowerDeviceTests(unittest.TestCase):
    def setUp(self):
        self.fs = SimpleNamespace(fixture=False, root=Path('/'), data=mock.Mock(return_value=MOUNTS),
                                  text=mock.Mock(return_value='Character devices:\n 73 icc\n'))
        self.controller = APP.Controller.__new__(APP.Controller)
        self.controller.runner = SimpleNamespace(fs=self.fs)
        self.nodes = {10: metadata(stat.S_IFDIR | 0o755, 1),
                      11: metadata(stat.S_IFDIR | 0o700, 2),
                      12: metadata(stat.S_IFCHR | 0o600, 3)}
        self.node = self.nodes[12]
        self.exists = True
        self.open_failure = None
        # Replace the module's OS object completely: unexpected syscalls cannot
        # fall through to host /dev, /proc, mknod or descriptor operations.
        self.os = SimpleNamespace(**{name: getattr(os, name) for name in (
            'O_RDONLY', 'O_RDWR', 'O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC', 'makedev', 'major')})
        self.os.open = mock.Mock(side_effect=self.open)
        self.os.close = mock.Mock()
        self.os.fstat = mock.Mock(side_effect=lambda fd: self.nodes[fd])
        self.os.mkdir = mock.Mock(side_effect=FileExistsError)
        self.os.stat = mock.Mock(side_effect=self.stat)
        self.os.mknod = mock.Mock(side_effect=self.mknod)
        patch = mock.patch.object(APP, 'os', self.os)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(APP.fcntl, 'ioctl', side_effect=AssertionError('real ioctl forbidden'))
        self.ioctl = patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(APP.Path, 'iterdir', side_effect=AssertionError('host inventory forbidden'))
        self.iterdir = patch.start(); self.addCleanup(patch.stop)

    def open(self, path, flags, *, dir_fd=None):
        expected = {'/dev': (10, None), 'psx-diagnostics-power': (11, 10), 'icc': (12, 11)}
        self.assertIn(path, expected)
        fd, parent = expected[path]
        self.assertEqual(dir_fd, parent)
        self.assertTrue(flags & os.O_NOFOLLOW)
        self.assertTrue(flags & os.O_CLOEXEC)
        self.assertEqual(bool(flags & os.O_DIRECTORY), fd != 12)
        if path == self.open_failure:
            raise OSError(errno.EACCES, 'synthetic open refusal')
        return fd

    def stat(self, path, *, dir_fd, follow_symlinks):
        self.assertEqual((path, dir_fd, follow_symlinks), ('icc', 11, False))
        if not self.exists: raise FileNotFoundError
        return self.node

    def mknod(self, path, mode, device, *, dir_fd):
        self.assertEqual((path, mode, device, dir_fd), ('icc', stat.S_IFCHR | 0o600, os.makedev(73, 0), 11))
        self.exists = True

    def consume(self):
        with self.controller._power_icc_device() as descriptor:
            self.assertEqual(descriptor, 12)

    def test_fixed_dev_path_works_with_nodev_run_and_closes_all_descriptors(self):
        self.consume()
        self.assertEqual([call.args[0] for call in self.os.open.call_args_list],
                         ['/dev', 'psx-diagnostics-power', 'icc'])
        self.os.mkdir.assert_called_once_with('psx-diagnostics-power', 0o700, dir_fd=10)
        self.os.mknod.assert_not_called()
        self.assertEqual(self.os.close.call_args_list, [mock.call(12), mock.call(11), mock.call(10)])
        self.ioctl.assert_not_called(); self.iterdir.assert_not_called()

    def test_missing_node_created_with_fixed_protected_identity(self):
        self.exists = False
        self.os.mkdir.side_effect = None
        self.consume()
        self.os.mknod.assert_called_once()
        self.assertEqual(self.os.stat.call_count, 2)

    def test_mount_refusals_precede_all_device_access(self):
        bad = [MOUNTS.replace(b'/dev rw,nosuid', b'/dev rw,nosuid,nodev'),
               MOUNTS.replace(b'devtmpfs rw,mode=755', b'devtmpfs rw,nodev'),
               MOUNTS.replace(b'/dev rw,nosuid', b'/dev ro,nosuid'),
               MOUNTS.replace(b'- devtmpfs ', b'- tmpfs '),
               MOUNTS.replace(b'/ /dev ', b'/other /dev '),
               MOUNTS.replace(b'/ /dev ', b'/ /elsewhere '),
               MOUNTS + b'4 1 0:8 / /dev rw - devtmpfs devtmpfs rw\n',
               MOUNTS + b'4 2 0:8 / /dev/psx-diagnostics-power rw - tmpfs tmpfs rw\n',
               MOUNTS + b'4 2 0:8 / /dev/psx-diagnostics-power/icc rw - tmpfs tmpfs rw\n',
               b'incomplete\n', None, b'x' * (1024**2 + 1)]
        for value in bad:
            with self.subTest(mount=value[:100] if value else value):
                self.fs.data.return_value = value
                with self.assertRaises(ValueError): self.consume()
                self.os.open.assert_not_called(); self.os.mknod.assert_not_called()

    def test_fixture_and_nonroot_fs_never_open_device(self):
        for fixture, root in ((True, Path('/')), (False, Path('/synthetic'))):
            self.fs.fixture, self.fs.root = fixture, root
            with self.assertRaises(ValueError): self.consume()
            with self.assertRaises(ValueError): self.controller._power_icc_restart()
        self.os.open.assert_not_called(); self.fs.data.assert_not_called()
        self.fs.text.assert_not_called(); self.iterdir.assert_not_called()

    def test_unprotected_dev_or_parent_refused_and_descriptors_closed(self):
        cases = [(10, dict(st_uid=1)), (10, dict(st_mode=stat.S_IFDIR | 0o777)),
                 (10, dict(st_mode=stat.S_IFLNK | 0o777)), (11, dict(st_uid=1)),
                 (11, dict(st_gid=1)), (11, dict(st_mode=stat.S_IFDIR | 0o750)),
                 (11, dict(st_mode=stat.S_IFLNK | 0o700)), (11, dict(st_dev=6))]
        for fd, changes in cases:
            with self.subTest(fd=fd, changes=changes):
                original = self.nodes[fd]
                self.nodes[fd] = SimpleNamespace(**dict(vars(original), **changes))
                self.os.open.reset_mock(); self.os.close.reset_mock()
                with self.assertRaises(ValueError): self.consume()
                self.assertEqual(self.os.close.call_args_list,
                                 [mock.call(10)] if fd == 10 else [mock.call(11), mock.call(10)])
                self.assertNotIn(mock.call('icc', mock.ANY, dir_fd=11), self.os.open.call_args_list)
                self.nodes[fd] = original
        self.os.mknod.assert_not_called()

    def test_wrong_or_symlink_existing_node_refused_without_fixup(self):
        for changes in ({'st_mode': stat.S_IFLNK | 0o600}, {'st_mode': stat.S_IFREG | 0o600},
                        {'st_mode': stat.S_IFCHR | 0o666}, {'st_uid': 1}, {'st_gid': 1},
                        {'st_rdev': os.makedev(73, 1)}, {'st_dev': 6}):
            with self.subTest(changes=changes):
                self.node = SimpleNamespace(**dict(vars(self.nodes[12]), **changes))
                self.os.open.reset_mock(); self.os.close.reset_mock()
                with self.assertRaises(ValueError): self.consume()
                self.assertEqual(self.os.open.call_count, 2)
                self.assertEqual(self.os.close.call_args_list, [mock.call(11), mock.call(10)])
        self.os.mknod.assert_not_called(); self.ioctl.assert_not_called()

    def test_opened_identity_checked_and_closed_before_use(self):
        for changes in ({'st_ino': 4}, {'st_dev': 6}, {'st_uid': 1},
                        {'st_rdev': os.makedev(74, 0)}, {'st_mode': stat.S_IFREG | 0o600}):
            with self.subTest(changes=changes):
                self.nodes[12] = SimpleNamespace(**dict(vars(self.node), **changes))
                self.os.close.reset_mock()
                with self.assertRaises(ValueError): self.consume()
                self.assertEqual(self.os.close.call_args_list, [mock.call(12), mock.call(11), mock.call(10)])
        self.ioctl.assert_not_called()

    def test_open_failure_has_no_retry_and_closes_only_open_descriptors(self):
        for path, closed in (('/dev', []), ('psx-diagnostics-power', [10]), ('icc', [11, 10])):
            self.open_failure = path
            self.os.open.reset_mock(); self.os.close.reset_mock()
            with self.assertRaises(OSError): self.consume()
            self.assertEqual(self.os.close.call_args_list, [mock.call(fd) for fd in closed])
            self.assertEqual(sum(call.args[0] == path for call in self.os.open.call_args_list), 1)
        self.ioctl.assert_not_called()

    def test_unchanged_single_ioctl_payload_abi_and_exception_cleanup(self):
        self.iterdir.side_effect = None
        self.iterdir.return_value = iter(())
        def inspect_ioctl(fd, operation, command, mutate):
            self.assertEqual((fd, operation, len(command), mutate), (12, 0xc0284901, 40, True))
            self.assertEqual(command[0], 4)
            self.assertEqual(int.from_bytes(command[2:4], sys.byteorder), 1)
            self.assertEqual(int.from_bytes(command[16:18], sys.byteorder), 6)
            self.assertEqual(command[24:34], bytes(10))
            pointer = int.from_bytes(command[8:16], sys.byteorder)
            self.assertEqual(APP.ctypes.string_at(pointer, 6), bytes.fromhex('000102000100'))
            raise OSError(errno.EIO, 'synthetic ioctl failure')
        self.ioctl.side_effect = inspect_ioctl
        with self.assertRaises(OSError): self.controller._power_icc_restart()
        self.ioctl.assert_called_once()
        self.assertEqual(self.os.close.call_args_list, [mock.call(12), mock.call(11), mock.call(10)])

    def test_existing_icc_caller_refused_before_device_open(self):
        descriptor = SimpleNamespace(stat=lambda: metadata(stat.S_IFCHR | 0o600, 9))
        self.iterdir.side_effect = [iter([Path('/proc/123')]), iter([descriptor])]
        with self.assertRaisesRegex(ValueError, 'Another ICC caller'):
            self.controller._power_icc_restart()
        self.os.open.assert_not_called(); self.ioctl.assert_not_called()


if __name__ == '__main__':
    unittest.main()

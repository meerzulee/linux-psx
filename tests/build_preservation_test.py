from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TARGET = "6.18-ps4-baikal"


class GeneratedSourcePreservationTests(unittest.TestCase):
    def test_unrecognized_or_redirected_sources_never_reach_git(self):
        for case in ("source-link", "marker-link", "wrong-marker", "git-link", "parent-link"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                build = workspace / "build"
                source_parent = build / "src"
                source_parent.mkdir(parents=True)
                source = source_parent / TARGET
                marker = source_parent / f".{TARGET}.linux-psx-generated"
                outside = workspace / "outside"
                outside.mkdir()
                (outside / "precious").write_text("preserve")
                if case == "source-link":
                    source.symlink_to(outside, target_is_directory=True)
                else:
                    source.mkdir()
                    (source / ".git").mkdir()
                marker.write_text(TARGET + "\n")
                if case == "marker-link":
                    marker.unlink()
                    (outside / "marker").write_text(TARGET + "\n")
                    marker.symlink_to(outside / "marker")
                elif case == "wrong-marker":
                    marker.write_text("another-target\n")
                elif case == "git-link":
                    (source / ".git").rmdir()
                    (source / ".git").symlink_to(outside, target_is_directory=True)
                elif case == "parent-link":
                    source_parent.rename(outside / "redirected-src")
                    source_parent.symlink_to(outside / "redirected-src", target_is_directory=True)
                binaries = workspace / "bin"
                binaries.mkdir()
                for name, script in {"uname": "printf 'Linux\\n'", "git": 'touch "$GIT_WAS_REACHED"; exit 42'}.items():
                    path = binaries / name
                    path.write_text("#!/bin/sh\n" + script + "\n")
                    path.chmod(0o755)
                touched = workspace / "git-was-reached"
                result = subprocess.run([str(ROOT / "build.sh"), "--jobs", "1", "--clean"],
                                        capture_output=True, text=True, env={**os.environ,
                                        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                                        "PSX_KERNEL_BUILD_ROOT": str(build), "GIT_WAS_REACHED": str(touched)})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(touched.exists(), result.stdout + result.stderr)
                self.assertEqual((outside / "precious").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()

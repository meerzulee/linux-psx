"""Exercise clone seeding with local shallow Git fixtures; no network/builds."""
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GNU_MV = shutil.which("gmv") or (shutil.which("mv") if sys.platform.startswith("linux") else None)


@unittest.skipUnless(GNU_MV, "reference publication requires the Linux builder's GNU mv")
class ReferenceCloneTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                            "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "Fixture",
                            "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
                            "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        self.upstream = self.workspace / "upstream"
        self.git("init", "-q", self.upstream)
        (self.upstream / "kernel.txt").write_text("selected older source\n")
        self.git("-C", self.upstream, "add", "kernel.txt")
        self.git("-C", self.upstream, "commit", "-qm", "selected source")
        self.selected = self.git("-C", self.upstream, "rev-parse", "HEAD").strip()
        self.git("-C", self.upstream, "tag", "selected-ref")
        (self.upstream / "kernel.txt").write_text("newer reference source\n")
        self.git("-C", self.upstream, "commit", "-qam", "newer source")
        self.build = self.workspace / "build"
        (self.build / "src").mkdir(parents=True)
        self.reference = self.build / "src/reference"
        self.git("clone", "-q", "--depth=1", self.upstream.as_uri(), self.reference)
        self.assertEqual(self.git("-C", self.reference, "rev-parse", "--is-shallow-repository").strip(), "true")
        self.marker = self.build / "src/.reference.linux-psx-generated"
        self.marker.write_text("reference\n")
        (self.reference / "kernel.txt").write_text("operator's pending tracked work\n")
        (self.reference / "keep-untracked").write_text("preserve pending reference input\n")
        self.repo = self.workspace / "builder"
        (self.repo / "targets").mkdir(parents=True)
        shutil.copy2(ROOT / "build.sh", self.repo / "build.sh")
        (self.repo / "config").write_text('CONFIG_EXTRA_FIRMWARE=""\n')
        (self.repo / "series").write_text("")
        self.target = self.repo / "targets/candidate.env"
        fields = {"TARGET_NAME": "candidate", "BASE_REPO": self.upstream.as_uri(),
                  "BASE_REF": "selected-ref", "BASE_COMMIT": self.selected, "BASE_DEPTH": "1",
                  "CONFIG_FILE": "config", "SERIES_FILE": "series", "PATCHES_DIR": ".",
                  "SOURCE_DATE_EPOCH": "1"}
        self.target.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in fields.items()))
        binaries = self.workspace / "bin"
        binaries.mkdir()
        (binaries / "uname").write_text("#!/bin/sh\nprintf 'Linux\\n'\n")
        (binaries / "uname").chmod(0o755)
        (binaries / "mv").symlink_to(GNU_MV)
        self.environment.update(PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                                PSX_KERNEL_BUILD_ROOT=str(self.build))

    def git(self, *args):
        return subprocess.check_output(["git", *map(str, args)], env=self.environment,
                                       stderr=subprocess.PIPE, text=True)

    def snapshot(self):
        return {str(path.relative_to(self.reference)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.reference.rglob("*") if path.is_file()}

    def run_builder(self, reference="reference"):
        return subprocess.run([str(self.repo / "build.sh"), "-t", "candidate", "--jobs", "1",
                               "--patches-only", "--reference-target", reference],
                              env=self.environment, capture_output=True, text=True)

    def test_shallow_reference_is_unchanged_and_selected_pin_is_independent(self):
        before = self.snapshot()
        result = self.run_builder()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)
        candidate = self.build / "src/candidate"
        self.assertEqual(self.git("-C", candidate, "rev-parse", "HEAD").strip(), self.selected)
        self.assertEqual(self.git("-C", candidate, "remote", "get-url", "origin").strip(), self.upstream.as_uri())
        self.assertEqual((candidate / "kernel.txt").read_text(), "selected older source\n")
        self.assertFalse((candidate / "keep-untracked").exists())
        self.assertFalse((candidate / ".git/objects/info/alternates").exists())
        for path in (self.reference / ".git/objects").rglob("*"):
            copied = candidate / ".git/objects" / path.relative_to(self.reference / ".git/objects")
            if path.is_file() and copied.is_file():
                self.assertNotEqual((path.stat().st_dev, path.stat().st_ino),
                                    (copied.stat().st_dev, copied.stat().st_ino))
        self.assertEqual((self.build / "src/.candidate.linux-psx-generated").read_text(), "candidate\n")
        self.assertFalse(list((self.build / "src").glob(".candidate.reference.*")))
        # Repeat builds use their own recognized source, not the former seed.
        self.marker.unlink()
        result = self.run_builder()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_wrong_reference_origin_is_refused_without_changes(self):
        self.git("-C", self.reference, "remote", "set-url", "origin", (self.workspace / "wrong").as_uri())
        before = self.snapshot()
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reference origin differs", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.build / "src/candidate").exists())

    def test_failed_pin_check_cleans_only_its_temporary_clone(self):
        self.target.write_text(self.target.read_text().replace(self.selected, "0" * 40))
        before = self.snapshot()
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fetched upstream commit mismatch", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.build / "src/candidate").exists())
        self.assertFalse((self.build / "src/.candidate.linux-psx-generated").exists())
        self.assertFalse(list((self.build / "src").glob(".candidate.reference.*")))

    def test_self_path_injection_unmarked_and_symlink_references_are_refused(self):
        before = self.snapshot()
        for reference in ("", "candidate", "../reference", "reference;touch injected"):
            with self.subTest(reference=reference):
                self.assertNotEqual(self.run_builder(reference).returncode, 0)
        self.marker.unlink()
        self.assertNotEqual(self.run_builder().returncode, 0)
        self.marker.write_text("reference\n")
        moved = self.build / "reference-outside"
        self.reference.rename(moved)
        self.reference.symlink_to(moved, target_is_directory=True)
        self.assertNotEqual(self.run_builder().returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.build / "src/candidate").exists())


if __name__ == "__main__":
    unittest.main()

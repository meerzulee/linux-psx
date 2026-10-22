import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts/ci/package_release.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
RELEASE = "6.18.44-psx-ps4-baikal2"


class ReleaseContractTests(unittest.TestCase):
    def test_ps5_uses_its_gpu_contract_without_ps4_mts_requirement(self):
        release = "7.2.3-psx-ps5-dev1"
        config = (ROOT / "platforms/ps5/config/7.2-psx-ps5.config").read_text()
        package.check_config(config, release, "ps5")
        with self.assertRaisesRegex(ValueError, "missing target"):
            package.check_config(config.replace("CONFIG_DRM_AMDGPU=m", "# CONFIG_DRM_AMDGPU is not set"), release, "ps5")
        notes = package.release_notes({"target": "7.2-ps5", "kernel_release": release}, "c" * 40, "local")
        self.assertIn("has not been boot-tested", notes)
        self.assertIn("PS4 MTS instructions do not apply", notes)

    def test_native_series_uses_recorded_shared_patch_paths_and_refuses_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "native").mkdir()
            (repo / "shared").mkdir()
            (repo / "native/series").write_text("driver.patch\n")
            patch = repo / "shared/driver.patch"
            patch.write_text("diff --git a/driver.c b/driver.c\n--- a/driver.c\n+++ b/driver.c\n@@ -1 +1 @@\n-old\n+new\n")
            target = {"series_path": "native/series", "patches": [
                {"entry": "driver.patch", "path": "shared/driver.patch", "sha256": package.digest(patch)}]}
            self.assertEqual(package.patched_source_paths(repo, target), {"driver.c"})
            patch.write_text(patch.read_text().replace("+new", "+tampered"))
            with self.assertRaisesRegex(ValueError, "patch identity"):
                package.patched_source_paths(repo, target)

    def test_release_notes_keep_hardware_evidence_specific_to_target_and_build(self):
        for target, release, included, excluded in [
            ("6.18-ps4-baikal", RELEASE, "two Ethernet boots", "passed one cable-connected"),
            ("7.2-ps4-baikal-native-dev", "7.2.11-psx-ps4-baikal-native-dev2", "EXP-20261018-004", "two Ethernet boots"),
        ]:
            text = package.release_notes({"target": target, "kernel_release": release}, "c" * 40, "https://example.com/run")
            self.assertIn(release, text)
            self.assertIn(included, text)
            self.assertNotIn(excluded, text)
            self.assertIn("has not been boot-tested", text)
            self.assertIn("source-" + release + ".tar.zst", text)
        with self.assertRaisesRegex(ValueError, "explicitly supported"):
            package.release_notes({"target": "7.2-ps5", "kernel_release": "other"}, "c" * 40, "local")

    def test_firmware_embedding_and_wrong_release_are_refused(self):
        config = (ROOT / "platforms/ps4/config/6.18-psx-baikal.config").read_text()
        package.check_config(config, RELEASE)
        with self.assertRaisesRegex(ValueError, "must not embed"):
            package.check_config(config.replace('CONFIG_EXTRA_FIRMWARE=""', 'CONFIG_EXTRA_FIRMWARE="vendor.bin"'), RELEASE)
        with self.assertRaisesRegex(ValueError, "missing target"):
            package.check_config(config.replace(f'CONFIG_LOCALVERSION="-{RELEASE.split("-", 1)[1]}"', 'CONFIG_LOCALVERSION="-wrong"'), RELEASE)

    def test_complete_source_includes_ignored_patch_input_not_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            for name, data in {"COPYING": "license", "LICENSES/preferred/GPL-2.0": "GPL", "tracked.c": "base", ".gitignore": "*.o\n.config\nignored-source.h\n"}.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data)
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            (source / "tracked.c").write_text("patched")
            for name in ["drivers/net/ethernet/psx_mts_net.c", "drivers/net/ethernet/mts_net_core.h", ".config", "build.o", "ignored-source.h"]:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source")
            result = package.source_files(source, {"ignored-source.h"})
            for name in ["ignored-source.h", ".config", "tracked.c", "drivers/net/ethernet/psx_mts_net.c", "LICENSES/preferred/GPL-2.0"]:
                self.assertIn(name, result)
            self.assertNotIn("build.o", result)
            firmware = source / "firmware/vendor.bin"
            firmware.parent.mkdir()
            firmware.write_bytes(b"firmware")
            with self.assertRaisesRegex(ValueError, "unexpected staged firmware"):
                package.source_files(source)

    def test_module_archive_refuses_escape_links_and_other_releases(self):
        for name in [f"lib/modules/{RELEASE}/kernel/driver.ko", "lib", "lib/modules"]:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE if name in ("lib", "lib/modules") else tarfile.REGTYPE
            package.check_module_member(member, RELEASE)
        for name in ["/etc/passwd", f"lib/modules/{RELEASE}/../../etc/passwd", "lib/modules/wrong/driver.ko"]:
            with self.assertRaisesRegex(ValueError, "unsafe module archive"):
                package.check_module_member(tarfile.TarInfo(name), RELEASE)
        member = tarfile.TarInfo(f"lib/modules/{RELEASE}/source")
        member.type = tarfile.SYMTYPE
        member.linkname = "/private/source"
        with self.assertRaisesRegex(ValueError, "unexpected module archive member type"):
            package.check_module_member(member, RELEASE)


class SplitReleaseTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(self.sandbox.cleanup)
        self.root = Path(self.sandbox.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.commit = "c" * 40
        self.source_name = f"source-{RELEASE}.tar.zst"
        names = ["bzImage", "config", "System.map", "manifest.txt", "version.txt",
                 "RELEASE-NOTES.md", "BUILD-SHA256SUMS", "BOOTING.md", "ETHERNET.md",
                 "HARDWARE-TESTING.md", "ARTIFACTS.md", f"modules-{RELEASE}.tar.zst",
                 self.source_name]
        for name in names:
            (self.dist / name).write_bytes(("exact fixture bytes: " + name + "\n").encode())
        (self.dist / "version.txt").write_text(RELEASE + "\n")
        self.metadata = {
            "schema": 1, "target": "6.18-ps4-baikal", "platform": "ps4-baikal", "kernel_release": RELEASE,
            "kernel_sha256": package.digest(self.dist / "bzImage"),
            "kernel_config_sha256": package.digest(self.dist / "config"), "embedded_firmware": False,
            "modules_archive": {"path": f"modules-{RELEASE}.tar.zst", "sha256": package.digest(self.dist / f"modules-{RELEASE}.tar.zst")},
            "module": {"name": "psx_mts_net", "path": f"lib/modules/{RELEASE}/kernel/drivers/net/ethernet/psx_mts_net.ko",
                       "sha256": "a" * 64, "build_note_hex": "12" * 24},
            "source_commit": self.commit,
            "source_archive": {"path": self.source_name, "sha256": package.digest(self.dist / self.source_name)},
        }
        self.write_metadata(self.dist)
        package.write_checksums(self.dist)
        self.output = self.root / "release"

    def write_metadata(self, directory, metadata=None):
        (directory / "artifacts.json").write_text(json.dumps(metadata or self.metadata, indent=2) + "\n")

    def stage(self):
        return package.stage_ci_artifacts(self.dist, RELEASE)

    def assert_refused_without_output(self, runtime, source, expected_commit=None):
        with self.assertRaises(ValueError):
            package.assemble_release(runtime, source, self.output, expected_commit)
        self.assertFalse(self.output.exists())

    def test_split_bundles_preserve_all_runtime_bytes_complete_source_and_independent_checksums(self):
        original = {name: path.read_bytes() for name, path in package.verified_files(self.dist).items()}
        runtime, source = self.stage()
        runtime_files, source_files = package.verified_files(runtime), package.verified_files(source)
        self.assertNotIn(self.source_name, runtime_files)
        self.assertEqual(set(source_files), {self.source_name, "artifacts.json", "SHA256SUMS"})
        for name, path in runtime_files.items():
            if name != "SHA256SUMS":
                self.assertEqual(path.read_bytes(), original[name])
        self.assertEqual(source_files[self.source_name].read_bytes(), original[self.source_name])
        self.assertEqual(package.digest(source_files[self.source_name]), self.metadata["source_archive"]["sha256"])
        self.assertEqual(runtime_files["artifacts.json"].read_bytes(), source_files["artifacts.json"].read_bytes())
        for name, data in original.items():
            self.assertEqual((self.dist / name).read_bytes(), data)
        for folder in (runtime, source):
            self.assertNotIn("runtime/", (folder / "SHA256SUMS").read_text())
            self.assertNotIn("source/", (folder / "SHA256SUMS").read_text())

    def test_assembled_assets_zip_own_checksums_and_source_identity(self):
        runtime, source = self.stage()
        package.assemble_release(runtime, source, self.output, expected_commit=self.commit)
        final = package.verified_files(self.output)
        self.assertEqual(set(final), {f"kernel-{RELEASE}.zip", self.source_name, "artifacts.json", "RELEASE-NOTES.md", "SHA256SUMS"})
        self.assertEqual((self.output / self.source_name).read_bytes(), (source / self.source_name).read_bytes())
        self.assertEqual((self.output / "artifacts.json").read_bytes(), (runtime / "artifacts.json").read_bytes())
        with zipfile.ZipFile(self.output / f"kernel-{RELEASE}.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(package.verified_files(runtime)))
            self.assertNotIn(self.source_name, archive.namelist())
            archive.extractall(self.root / "unpacked")
        package.verified_files(self.root / "unpacked")
        for name in ("bzImage", "config", f"modules-{RELEASE}.tar.zst"):
            self.assertEqual((self.root / "unpacked" / name).read_bytes(), (self.dist / name).read_bytes())

    def test_assembly_cli_is_stdlib_only_and_checks_expected_commit(self):
        runtime, source = self.stage()
        subprocess.run([sys.executable, str(ROOT / "scripts/ci/package_release.py"), "--assemble-release",
                        "--runtime", str(runtime), "--source", str(source), "--destination", str(self.output),
                        "--expected-commit", self.commit], cwd=self.root, check=True, capture_output=True)
        package.verified_files(self.output)

    def test_corrupt_missing_or_extra_unlisted_input_refused_without_output(self):
        for change in ("corrupt-runtime", "missing-source", "unlisted"):
            with self.subTest(change=change):
                runtime, source = self.stage()
                if change == "corrupt-runtime":
                    (runtime / "bzImage").write_bytes(b"corrupt")
                elif change == "missing-source":
                    (source / self.source_name).unlink()
                else:
                    (runtime / "unlisted.txt").write_text("not checksummed")
                self.assert_refused_without_output(runtime, source)
                import shutil
                shutil.rmtree(runtime)
                shutil.rmtree(source)

    def test_metadata_mismatched_commit_release_or_source_hash_refused(self):
        for change in ("commit", "release", "source-hash", "kernel-hash", "modules-hash"):
            with self.subTest(change=change):
                runtime, source = self.stage()
                metadata = json.loads((source / "artifacts.json").read_text())
                if change == "commit":
                    metadata["source_commit"] = "d" * 40
                elif change == "release":
                    metadata["kernel_release"] = "6.18.44-other"
                elif change == "source-hash":
                    metadata["source_archive"]["sha256"] = "0" * 64
                elif change == "kernel-hash":
                    metadata["kernel_sha256"] = "0" * 64
                else:
                    metadata["modules_archive"]["sha256"] = "0" * 64
                self.write_metadata(source, metadata)
                if change not in ("commit", "release"):
                    self.write_metadata(runtime, metadata)
                    package.write_checksums(runtime)
                package.write_checksums(source)
                self.assert_refused_without_output(runtime, source)
                import shutil
                shutil.rmtree(runtime)
                shutil.rmtree(source)

    def test_expected_commit_existing_output_and_unsafe_source_name_are_refused(self):
        runtime, source = self.stage()
        self.assert_refused_without_output(runtime, source, "d" * 40)
        self.assert_refused_without_output(runtime, source, "short")
        for folder in (runtime, source):
            metadata = json.loads((folder / "artifacts.json").read_text())
            metadata["source_archive"]["path"] = "../outside.tar.zst"
            self.write_metadata(folder, metadata)
            package.write_checksums(folder)
        self.assert_refused_without_output(runtime, source)
        for folder in (runtime, source):
            self.write_metadata(folder)
            package.write_checksums(folder)
        self.output.mkdir()
        (self.output / "keep").write_bytes(b"existing user file")
        with self.assertRaisesRegex(ValueError, "already exists"):
            package.assemble_release(runtime, source, self.output)
        self.assertEqual(list(self.output.iterdir()), [self.output / "keep"])

    def test_symlinks_generated_subdirectories_and_missing_source_rejected_before_staging(self):
        for change in ("symlink", "directory", "missing-source"):
            with self.subTest(change=change):
                original = (self.dist / self.source_name).read_bytes()
                if change == "symlink":
                    (self.dist / "alias.txt").symlink_to(self.dist / "bzImage")
                elif change == "directory":
                    (self.dist / "foreign-output").mkdir()
                else:
                    (self.dist / self.source_name).unlink()
                with self.assertRaises(ValueError):
                    self.stage()
                self.assertFalse((self.dist / "runtime").exists() or (self.dist / "source").exists())
                if change == "symlink":
                    (self.dist / "alias.txt").unlink()
                elif change == "directory":
                    (self.dist / "foreign-output").rmdir()
                else:
                    (self.dist / self.source_name).write_bytes(original)

    def test_assembly_rejects_symlinked_input_checksum_escape_and_duplicate_entries(self):
        runtime, source = self.stage()
        original = (runtime / "SHA256SUMS").read_text()
        for text in (original + original.splitlines()[0] + "\n", original + "0" * 64 + "  ../outside\n"):
            (runtime / "SHA256SUMS").write_text(text)
            self.assert_refused_without_output(runtime, source)
        (runtime / "SHA256SUMS").write_text(original)
        (runtime / "alias").symlink_to(runtime / "bzImage")
        self.assert_refused_without_output(runtime, source)


if __name__ == "__main__":
    unittest.main()

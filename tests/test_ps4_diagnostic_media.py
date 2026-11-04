"""Synthetic media recipe/oracle guards; no codecs, network, devices or console."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import struct
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
APP = runpy.run_path(str(ROOT / "scripts/prepare-ps4-diagnostics-media"))
SPEC = json.loads((ROOT / "manifests/ps4-diagnostic-media.json").read_text())


class MediaTests(unittest.TestCase):
    def test_recipe_rejects_unsupported_codecs_unbounded_jobs_and_unsafe_members(self):
        APP["validate"](SPEC)
        for field, value in (("codec", "hevc"), ("fps", 60), ("seconds", 60),
                             ("width", 3840), ("profile", "high10"), ("container", "avi")):
            recipe = copy.deepcopy(SPEC)
            recipe["fixtures"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                APP["validate"](recipe)
        for change in ("budget", "member", "duplicate"):
            recipe = copy.deepcopy(SPEC)
            if change == "budget": recipe["maximum_pack_bytes"] = 64 * 1024**2
            elif change == "member": recipe["source"]["member"] = "../../escape.mp4"
            else: recipe["fixtures"].append(recipe["fixtures"][0])
            with self.subTest(change=change), self.assertRaises(ValueError):
                APP["validate"](recipe)

    def test_output_is_new_and_confined_to_generated_output_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); allowed = root / "output"; allowed.mkdir()
            existing = allowed / "existing"; existing.mkdir()
            alias = allowed / "alias"; alias.symlink_to(root, target_is_directory=True)
            self.assertEqual(APP["output_path"](allowed / "fresh", allowed), allowed / "fresh")
            for path in (allowed, existing, root / "outside", alias / "escape"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    APP["output_path"](path, allowed)

    def test_source_verification_rejects_drift_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source"; path.write_bytes(b"fixture")
            pin = hashlib.sha256(b"fixture").hexdigest()
            APP["verify"](path, 7, pin)
            with self.assertRaises(ValueError): APP["verify"](path, 8, pin)
            path.write_bytes(b"changed")
            with self.assertRaises(ValueError): APP["verify"](path, 7, pin)
            alias = path.with_name("alias"); alias.symlink_to(path)
            with self.assertRaises(ValueError): APP["verify"](alias, 7, APP["digest"](path))

    def test_frame_oracle_refuses_missing_reordered_or_wrong_sized_frames(self):
        text = "#tb 0: 1/30\n0, 0, 0, 1, 6, " + "a" * 64 + "\n0, 1, 1, 1, 6, " + "b" * 64 + "\n"
        result = APP["frame_hashes"](text, 2, 2, 2)
        self.assertEqual(result["frames"][1]["pts"], 1)
        for broken in (text.replace("1/30", "1/60"), text.replace("0, 1, 1", "0, 2, 2"),
                       text.replace(", 6,", ", 8,"), text.rsplit("\n", 2)[0] + "\n"):
            with self.assertRaises(ValueError): APP["frame_hashes"](broken, 2, 2, 2)

    def test_sync_audio_has_exact_stereo_samples_and_quiet_intervals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sync.wav"
            oracle = APP["audio_fixture"](path, 2)
            with wave.open(str(path)) as stream:
                self.assertEqual((stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getnframes()),
                                 (44100, 2, 2, 88200))
                data = stream.readframes(stream.getnframes())
            self.assertEqual(hashlib.sha256(data).hexdigest(), oracle["pcm_sha256"])
            samples = list(struct.iter_unpack("<hh", data))
            self.assertTrue(all(left == right for left, right in samples))
            self.assertTrue(any(left != 0 for left, _ in samples[:4410]))
            self.assertTrue(all(left == 0 for left, _ in samples[4410:44100]))
            self.assertTrue(any(left != 0 for left, _ in samples[44100:48510]))
            self.assertEqual(oracle["audio_onsets_samples"], [0, 44100])
            self.assertEqual(oracle["video_flash_frames"], [[0, 1, 2], [30, 31, 32]])


if __name__ == "__main__":
    unittest.main()

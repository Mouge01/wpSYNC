"""GRUB backend checks confined to unprivileged temporary directories."""

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from wallpaper_sync.grub import GrubBackend, digest, inspect_jpeg, validate_jpeg


def jpeg(size, color, *options):
    return subprocess.run(["/usr/bin/magick", "-size", size, "xc:" + color,
                           "-colorspace", "sRGB", "-type", "TrueColor",
                           "-depth", "8", "-interlace", "None", *options, "JPEG:-"],
                          capture_output=True, check=True, timeout=25).stdout


@unittest.skipIf(os.geteuid() == 0, "Tests intentionally reject privileged path overrides")
class GrubBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = jpeg("80x50", "#3465a4")
        cls.first = jpeg("2880x1800", "#975aaf")
        cls.second = jpeg("2880x1800", "#448855")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wallpaper-sync-grub-test-")
        self.directory = Path(self.temporary.name)
        self.theme_dir = self.directory / "theme"
        self.theme_dir.mkdir(mode=0o755)
        self.target = self.theme_dir / "background.jpg"
        self.target.write_bytes(self.original)
        self.target.chmod(0o644)
        self.theme = self.theme_dir / "theme.txt"
        self.theme.write_text('desktop-image: "background.jpg"\n+ image { file = "scrim.png" }\n')
        self.theme.chmod(0o644)
        self.state = self.directory / "state" / "grub.json"
        self.backend = GrubBackend(uid=os.getuid(), target=self.target, state_file=self.state, test_mode=True)
        self.metadata = {"source_uri": "file:///wallpaper.png", "image_sha256": digest(self.first)}

    def tearDown(self):
        self.temporary.cleanup()

    def apply(self, data=None, uri=None):
        data = self.first if data is None else data
        return self.backend.apply(data, {"source_uri": uri or self.metadata["source_uri"], "image_sha256": digest(data)})

    def assert_untouched(self):
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.backend.previous.exists())
        self.assertFalse(self.state.exists())

    def test_apply_backup_and_restore_original_dimensions(self):
        result = self.apply()
        self.assertTrue(result["changed"])
        self.assertEqual(self.target.read_bytes(), self.first)
        self.assertEqual(self.backend.previous.read_bytes(), self.original)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.backend.previous.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        previous_mtime = self.backend.previous.stat().st_mtime_ns
        restored = self.backend.restore()
        self.assertTrue(restored["changed"])
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.backend.previous.read_bytes(), self.original)
        self.assertEqual(self.backend.previous.stat().st_mtime_ns, previous_mtime)
        self.assertEqual((restored["width"], restored["height"]), (80, 50))
        self.assertFalse(self.backend.restore()["changed"])

    def test_configured_resolution_is_used_and_reported_separately(self):
        self.backend = GrubBackend(uid=os.getuid(), target=self.target, state_file=self.state, test_mode=True,
                                   config={"enabled": True, "theme": "public-example", "width": 1920, "height": 1080})
        status = self.backend.status()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["theme"], "public-example")
        self.assertEqual((status["output_width"], status["output_height"]), (1920, 1080))
        self.assertEqual((status["width"], status["height"]), (80, 50))
        with self.assertRaisesRegex(ValueError, "1920×1080"):
            self.apply()
        self.assert_untouched()
        data = jpeg("1920x1080", "#8844aa")
        result = self.apply(data)
        self.assertEqual((result["width"], result["height"]), (1920, 1080))
        self.backend.restore()
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_production_destination_comes_only_from_validated_theme(self):
        backend = GrubBackend(config={"enabled": True, "theme": "public-example", "width": 1920, "height": 1080})
        self.assertEqual(backend.target, Path("/boot/grub2/themes/public-example/background.jpg"))
        self.assertEqual(backend.previous, Path("/boot/grub2/themes/public-example/background.previous.jpg"))
        self.assertEqual(backend.theme, Path("/boot/grub2/themes/public-example/theme.txt"))

    def test_production_defaults_disabled_and_never_reads_boot_files(self):
        backend = GrubBackend()
        with patch.object(backend, "_snapshot", side_effect=AssertionError("Unexpected boot access")), \
             patch("wallpaper_sync.grub.validate_jpeg", side_effect=AssertionError("Unexpected decode")):
            status = backend.status()
            self.assertFalse(status["enabled"])
            self.assertFalse(status["available"])
            self.assertTrue(status["configuration_required"])
            self.assertIn("disabled", status["message"])
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                backend.apply(self.first, self.metadata)
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                backend.restore()

    def test_successive_apply_keeps_exact_last_known_good(self):
        self.apply()
        self.apply(self.second)
        self.assertEqual(self.backend.previous.read_bytes(), self.first)
        self.backend.restore()
        self.assertEqual(self.target.read_bytes(), self.first)
        self.assertEqual(self.backend.previous.read_bytes(), self.first)

    def test_equal_bytes_preserve_all_mtimes_when_metadata_matches(self):
        self.apply()
        paths = [self.target, self.backend.previous, self.state]
        mtimes = [path.stat().st_mtime_ns for path in paths]
        with patch.object(self.backend, "_stage", side_effect=AssertionError("Unexpected write")):
            result = self.apply()
        self.assertFalse(result["changed"])
        self.assertEqual([path.stat().st_mtime_ns for path in paths], mtimes)
        self.assertEqual(self.backend.previous.read_bytes(), self.original)

    def test_equal_initial_bytes_record_metadata_only(self):
        self.target.write_bytes(self.first)
        mtime = self.target.stat().st_mtime_ns
        result = self.apply()
        self.assertFalse(result["changed"])
        self.assertEqual(self.target.stat().st_mtime_ns, mtime)
        self.assertFalse(self.backend.previous.exists())
        self.assertTrue(result["matches_last_operation"])
        self.assertEqual(self.backend.status()["source_uri"], self.metadata["source_uri"])
        self.assertEqual(json.loads(self.state.read_text())["image_sha256"], digest(self.first))

    def test_equal_bytes_new_source_records_metadata_preserves_boot_assets(self):
        self.apply()
        mtimes = [self.target.stat().st_mtime_ns, self.backend.previous.stat().st_mtime_ns]
        result = self.apply(uri="file:///another-identical-image.png")
        self.assertFalse(result["changed"])
        self.assertEqual(result["source_uri"], "file:///another-identical-image.png")
        self.assertEqual([self.target.stat().st_mtime_ns, self.backend.previous.stat().st_mtime_ns], mtimes)

    def test_status_hash_is_actual_after_external_change(self):
        self.apply()
        self.target.write_bytes(self.second)
        result = self.backend.status()
        self.assertEqual(result["current_sha256"], digest(self.second))
        self.assertFalse(result["matches_last_operation"])
        self.assertIsNone(result["source_uri"])
        self.assertIsNone(result["image_sha256"])

    def test_candidate_rejections_preserve_current(self):
        progressive = jpeg("2880x1800", "#123456", "-interlace", "Plane")
        grayscale = jpeg("2880x1800", "#123456", "-colorspace", "Gray", "-type", "Grayscale")
        for data in [b"not jpeg", self.original, progressive, grayscale, self.first[:-20], self.first + b"junk"]:
            with self.subTest(data_size=len(data)):
                with self.assertRaises(ValueError):
                    self.apply(data)
                self.assert_untouched()

    def test_full_decode_rejects_entropy_truncation_with_end_marker(self):
        truncated = self.first[:len(self.first) // 2] + b"\xff\xd9"
        self.assertEqual(inspect_jpeg(truncated), (2880, 1800))
        with self.assertRaisesRegex(ValueError, "decoding"):
            self.apply(truncated)
        self.assert_untouched()

    def test_corrupt_current_or_previous_rejected_before_mutation(self):
        self.backend.previous.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.backend.previous.read_bytes(), b"broken")
        self.assertFalse(self.state.exists())
        self.backend.previous.unlink()
        self.target.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.target.read_bytes(), b"broken")
        self.assertFalse(self.backend.previous.exists())

    def test_theme_must_reference_exact_existing_background(self):
        for content in ['desktop-image: "other.jpg"', '# desktop-image: "background.jpg"',
                        'desktop-image: "background.jpg"\ndesktop-image: "other.jpg"']:
            self.theme.write_text(content)
            with self.assertRaisesRegex(RuntimeError, "reference"):
                self.apply()
            self.assert_untouched()

    def test_symlink_target_theme_backup_and_state_rejected(self):
        for path in [self.target, self.theme, self.backend.previous, self.state]:
            with self.subTest(path=path):
                path.parent.mkdir(mode=0o700, exist_ok=True)
                contents = path.read_bytes() if path.exists() else None
                path.unlink(missing_ok=True)
                outside = self.directory / "outside"
                outside.write_bytes(contents or self.original)
                path.symlink_to(outside)
                with self.assertRaises((OSError, RuntimeError)):
                    self.apply()
                self.assertTrue(path.is_symlink())
                self.assertEqual(outside.read_bytes(), contents or self.original)
                path.unlink()
                if contents is not None:
                    path.write_bytes(contents)
                outside.unlink()

    def test_untrusted_ancestor_and_file_permissions_rejected(self):
        for path in [self.theme_dir, self.target, self.theme]:
            with self.subTest(path=path):
                original_mode = path.stat().st_mode & 0o777
                path.chmod(original_mode | 0o020)
                try:
                    with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                        self.apply()
                finally:
                    path.chmod(original_mode)
                self.assert_untouched()

    def test_ownership_check_rejects_wrong_owner(self):
        self.backend.owner += 1
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            self.apply()
        self.assert_untouched()

    def test_authorization_failure_keeps_all_images_unchanged(self):
        def denied():
            raise PermissionError("Authorization revoked")
        with self.assertRaises(PermissionError):
            self.backend.apply(self.first, self.metadata, authorize=denied)
        self.assert_untouched()
        calls = 0
        def revoke_before_commit():
            nonlocal calls
            calls += 1
            if calls == 2:
                denied()
        with self.assertRaises(PermissionError):
            self.backend.apply(self.first, self.metadata, authorize=revoke_before_commit)
        self.assert_untouched()
        self.assertFalse(list(self.theme_dir.glob(".wallpaper-sync-*")))

    def test_stage_failure_preserves_current_and_backup(self):
        self.apply()
        original_stage = self.backend._stage
        def fail_state(path, *args, **kwargs):
            if path == self.state:
                raise OSError("disk full")
            return original_stage(path, *args, **kwargs)
        with patch.object(self.backend, "_stage", side_effect=fail_state):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.apply(self.second)
        self.assertEqual(self.target.read_bytes(), self.first)
        self.assertEqual(self.backend.previous.read_bytes(), self.original)
        self.assertFalse(list(self.theme_dir.glob(".wallpaper-sync-*")))

    def test_missing_target_not_created_and_missing_backup_not_restored(self):
        with self.assertRaisesRegex(RuntimeError, "No previous"):
            self.backend.restore()
        self.target.unlink()
        with self.assertRaises(FileNotFoundError):
            self.apply()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.state.exists())

    def test_postcommit_state_failure_reports_installed_with_warning(self):
        replace = os.replace
        def fail_state(source, destination):
            if destination == self.state:
                raise OSError("state rename failed")
            return replace(source, destination)
        with patch("wallpaper_sync.grub.os.replace", side_effect=fail_state):
            result = self.apply()
        self.assertTrue(result["changed"])
        self.assertIn("state metadata", result["warning"])
        self.assertEqual(self.target.read_bytes(), self.first)
        self.assertEqual(self.backend.previous.read_bytes(), self.original)
        actual = self.backend.status()
        self.assertEqual(actual["current_sha256"], digest(self.first))
        self.assertFalse(actual["matches_last_operation"])
        self.assertIsNone(actual["source_uri"])

    def test_postrename_directory_fsync_failure_reports_durability_warning(self):
        fsync = os.fsync
        def fail_directory(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("directory fsync failed")
            return fsync(descriptor)
        with patch("wallpaper_sync.grub.os.fsync", side_effect=fail_directory):
            result = self.apply()
        self.assertTrue(result["changed"])
        self.assertIn("durability", result["warning"])
        self.assertEqual(self.target.read_bytes(), self.first)
        self.assertEqual(self.backend.previous.read_bytes(), self.original)

    def test_metadata_checksum_checked_and_unknown_fields_discarded(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.backend.apply(self.first, {"image_sha256": "0" * 64})
        self.assert_untouched()
        self.backend.apply(self.first, {**self.metadata, "target": "/etc/passwd", "arbitrary": "ignored"})
        saved = json.loads(self.state.read_text())
        self.assertNotIn("target", saved)
        self.assertNotIn("arbitrary", saved)

    def test_production_path_overrides_require_explicit_unprivileged_test_mode(self):
        with self.assertRaisesRegex(ValueError, "cannot be overridden"):
            GrubBackend(target=self.target)
        with patch("wallpaper_sync.grub.os.geteuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "without root"):
                GrubBackend(target=self.target, state_file=self.state, test_mode=True)

    def test_root_decoder_drops_all_identity_and_environment(self):
        with patch("wallpaper_sync.grub.os.geteuid", return_value=0), \
             patch("wallpaper_sync.grub.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stderr = b""
            run.return_value.stdout = b"0" * 64
            validate_jpeg(self.first)
        arguments, kwargs = run.call_args
        self.assertEqual(kwargs["user"], 65534)
        self.assertEqual(kwargs["group"], 65534)
        self.assertEqual(kwargs["extra_groups"], [])
        self.assertEqual(kwargs["input"], self.first)
        self.assertEqual(kwargs["cwd"], "/")
        self.assertTrue(kwargs["close_fds"])
        self.assertLessEqual(kwargs["timeout"], 30)
        self.assertNotIn("LD_PRELOAD", kwargs["env"])
        self.assertIn("JPEG:-", arguments[0])
        self.assertIn("%#", arguments[0])


if __name__ == "__main__":
    unittest.main()

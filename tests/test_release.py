"""Release artifacts are reproducible and never sweep up workstation files."""

import hashlib
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

from tools.build_release import MANIFEST, build_release, release_files

PROJECT = Path(__file__).resolve().parent.parent


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wpsync-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "source"
        self.root.mkdir()
        self.paths = [MANIFEST, "VERSION", "README.md", "LICENSE", "THIRD_PARTY_LICENSES.md",
                      "install.sh", "uninstall.sh", "tools/install.py", "tools/build_release.py",
                      "wallpaper_sync/example.py"]
        for relative in self.paths:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (self.root / "VERSION").write_text("0.1.0\n")
        (self.root / "install.sh").chmod(0o755)
        (self.root / "uninstall.sh").chmod(0o755)
        self.write_manifest()

    def write_manifest(self):
        (self.root / MANIFEST).write_text("\n".join(self.paths) + "\n")

    def build(self, name="dist", **kwargs):
        return build_release(self.root, self.directory / name, **kwargs)

    def test_identical_bytes_despite_mtime_and_nonexecutable_permission_changes(self):
        first, first_checksum = self.build("first")
        for relative in self.paths:
            os.utime(self.root / relative, (1234567890, 1234567890))
        (self.root / "README.md").chmod(0o640)
        second, second_checksum = self.build("second")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        expected = f"{hashlib.sha256(first.read_bytes()).hexdigest()}  wpSYNC-0.1.0.tar.gz\n"
        self.assertEqual(first_checksum.read_text(), expected)
        self.assertEqual(second_checksum.read_text(), expected)

    def test_archive_paths_modes_and_metadata_are_portable(self):
        archive, _ = self.build(epoch=1234)
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            self.assertEqual([member.name for member in members],
                             ["wpSYNC-0.1.0/" + path for path in sorted(self.paths)])
            for member in members:
                self.assertTrue(member.isfile())
                self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, "", ""))
                self.assertEqual(member.mtime, 1234)
                self.assertEqual(member.mode, 0o755 if member.name.endswith(("/install.sh", "/uninstall.sh")) else 0o644)

    def test_unlisted_private_generated_and_old_release_files_never_enter_archive(self):
        for relative in [".env", ".git/config", "work/private-wallpaper.png", "dist/old.tar.gz",
                         "tests/fixtures/personal.jpg", "wallpaper_sync/private.py", "tools/__pycache__/x.pyc"]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"PRIVATE")
        archive, _ = self.build()
        with tarfile.open(archive, "r:gz") as bundle:
            self.assertEqual(len(bundle.getmembers()), len(self.paths))
            self.assertFalse(any(bundle.extractfile(member).read() == b"PRIVATE" for member in bundle.getmembers()))

    def test_symlink_file_and_symlink_parent_are_rejected(self):
        source = self.root / "wallpaper_sync/example.py"
        original = self.directory / "external.py"
        original.write_text("external")
        source.unlink()
        source.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            self.build()
        source.unlink()
        source.parent.rmdir()
        source.parent.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            self.build()

    def test_invalid_versions_are_rejected_before_output(self):
        for version in ["../escape", "v0.1.0", "0.1", "01.2.3", "0.1.0\nmalicious", "0.1.0-rc1"]:
            with self.subTest(version=version):
                (self.root / "VERSION").write_text(version)
                with self.assertRaisesRegex(ValueError, "VERSION"):
                    self.build()
        self.assertFalse((self.directory / "dist").exists())

    def test_invalid_manifest_entries_and_duplicates_are_rejected(self):
        for relative in ["../outside", "/etc/passwd", "tools//install.py", "work/output.py",
                         "extension/schemas/gschemas.compiled", ".env", "secrets.pem", "tools/__pycache__/x.pyc"]:
            with self.subTest(relative=relative):
                self.paths.append(relative)
                self.write_manifest()
                with self.assertRaises(ValueError):
                    self.build()
                self.paths.pop()
        self.paths.append("LICENSE")
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build()

    def test_missing_required_entries_and_missing_sources_are_rejected(self):
        self.paths.remove("LICENSE")
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "missing required"):
            self.build()
        self.paths.append("LICENSE")
        self.write_manifest()
        (self.root / "LICENSE").unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()

    def test_invalid_epoch_is_rejected(self):
        for epoch in [-1, 2**32, "1234"]:
            with self.subTest(epoch=epoch), self.assertRaisesRegex(ValueError, "SOURCE_DATE_EPOCH"):
                self.build(epoch=epoch)

    def test_project_manifest_contains_tests_licenses_and_executable_entrypoints(self):
        sources = {relative: info for relative, _, info in release_files(PROJECT)}
        for relative in ["tests/test_release.py", "tests/fixtures/blue.avif", "licenses/gnome-gdm-wallpaper-MIT.txt",
                         "tools/check.py", "docs/SECURITY.md", "extension/schemas/org.gnome.shell.extensions.wallpaper-sync.gschema.xml"]:
            self.assertIn(relative, sources)
        for relative in ["install.sh", "uninstall.sh", "cli/wallpaper-sync", "helper/wallpaper-sync-helper"]:
            self.assertTrue(sources[relative].st_mode & 0o111, relative)


if __name__ == "__main__":
    unittest.main()

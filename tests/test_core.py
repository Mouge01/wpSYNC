"""Local-only tests: generated files and memory GSettings, never installed state."""

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

os.environ["GSETTINGS_BACKEND"] = "memory"
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib

from wallpaper_sync.backend import Backend
from wallpaper_sync.common import atomic_json, desktop_source, local_path, settings
from wallpaper_sync.helper import inspect_image, read_sealed
from wallpaper_sync.images import check_size, prepare, sealed_image
from wallpaper_sync.resource import PREFIX, build, digest, entries

PROJECT = Path(__file__).resolve().parent.parent
WORK = PROJECT / "work"
WORK.mkdir(exist_ok=True)


def make_image(path, image_format="png"):
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 48)
    image.fill(0x3465a4ff)
    # A hard color edge makes blur assertions meaningful.
    other = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 32, 48)
    other.fill(0xef2929ff)
    other.copy_area(0, 0, 32, 48, image, 0, 0)
    image.savev(str(path), image_format, [], [])
    return path.read_bytes()


def make_resource(directory, color="black"):
    directory.mkdir(parents=True, exist_ok=True)
    root = ET.Element("gresources")
    node = ET.SubElement(root, "gresource", {"prefix": "/"})
    content = {PREFIX + name: f"#lockDialogGroup {{ background-color: {color}; }}\n".encode()
               for name in ["gnome-shell-dark.css", "gnome-shell-light.css", "gnome-shell-high-contrast.css"]}
    content["/unrelated/special & name.txt"] = b"original bytes outside the theme prefix"
    for index, (name, data) in enumerate(content.items()):
        (directory / str(index)).write_bytes(data)
        ET.SubElement(node, "file", {"alias": name.lstrip("/")}).text = str(index)
    manifest = directory / "fixture.xml"
    ET.ElementTree(root).write(manifest)
    target = directory / "fixture.gresource"
    subprocess.run(["glib-compile-resources", str(manifest), "--sourcedir=" + str(directory),
                    "--target=" + str(target)], check=True, capture_output=True)
    target.chmod(0o644)
    return target


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=WORK)
        self.directory = Path(self.temporary.name)
        self.cache_patch = patch("wallpaper_sync.images.GLib.get_user_cache_dir", return_value=str(self.directory / "cache"))
        self.cache_patch.start()

    def tearDown(self):
        self.cache_patch.stop()
        self.temporary.cleanup()


class ImageTests(TemporaryTest):
    def test_png_and_jpeg_pass_through_at_zero_blur(self):
        for image_format in ["png", "jpeg"]:
            path = self.directory / ("source." + image_format)
            original = make_image(path, image_format)
            result, metadata = prepare(path.as_uri(), 0)
            self.assertEqual(result, original)
            self.assertEqual(inspect_image(result), metadata["format"])

    def test_spaces_unicode_apostrophe_parentheses_and_underscores(self):
        path = self.directory / "Mouge's duvar kağıdı (夜)_01.png"
        original = make_image(path)
        self.assertEqual(local_path(path.as_uri()), path)
        result, _ = prepare(path.as_uri(), 12)
        self.assertEqual(inspect_image(result), "png")
        self.assertEqual(path.read_bytes(), original)

    def test_webp_with_false_png_extension_is_converted(self):
        source = self.directory / "source.png"
        subprocess.run(["/usr/bin/magick", "-size", "64x48", "xc:blue", "WEBP:" + str(source)], check=True)
        result, metadata = prepare(source.as_uri(), 0)
        self.assertEqual(inspect_image(result), "png")
        self.assertEqual((metadata["width"], metadata["height"]), (64, 48))
        with self.assertRaisesRegex(ValueError, "needs conversion"):
            prepare(source.as_uri(), 0, False)

    def test_avif_native_loader_conversion_and_blur(self):
        fixture = PROJECT / "tests/fixtures/blue.avif"
        result, metadata = prepare(fixture.as_uri(), 12)
        self.assertEqual(inspect_image(result), "png")
        self.assertEqual((metadata["width"], metadata["height"]), (64, 48))

    def test_blur_changes_pixels_and_preserves_shape(self):
        source = self.directory / "source.png"
        make_image(source)
        data = [prepare(source.as_uri(), blur)[0] for blur in [0, 12, 4]]
        self.assertEqual(len({digest(item) for item in data}), 3)
        self.assertTrue(all(struct.unpack(">II", item[16:24]) == (64, 48) for item in data))

    def test_remote_missing_and_non_image_are_rejected(self):
        with self.assertRaises(ValueError):
            local_path("https://example.com/image.png")
        with self.assertRaises(FileNotFoundError):
            prepare((self.directory / "missing.png").as_uri())
        text = self.directory / "fake.png"
        text.write_text("not an image")
        with self.assertRaises(ValueError):
            prepare(text.as_uri())

    def test_dimensions_and_blur_limits(self):
        for size in [(0, 1), (16385, 1), (10000, 10000)]:
            with self.assertRaises(ValueError):
                check_size(*size)
        for blur in [-1, 31, True, "12"]:
            with self.assertRaises(ValueError):
                prepare("file:///unused.png", blur)

    def test_sealed_descriptor_roundtrip_and_wrong_uid(self):
        data = make_image(self.directory / "image.png")
        descriptor = sealed_image(data)
        try:
            self.assertEqual(read_sealed(descriptor, os.getuid()), data)
            with self.assertRaises(ValueError):
                read_sealed(descriptor, os.getuid() + 1)
            with self.assertRaises(OSError):
                os.write(descriptor, b"mutate")
        finally:
            os.close(descriptor)

    def test_mutable_descriptor_and_invalid_payload_rejected(self):
        descriptor = os.memfd_create("unsealed", os.MFD_ALLOW_SEALING)
        try:
            os.write(descriptor, b"test")
            with self.assertRaisesRegex(ValueError, "immutable"):
                read_sealed(descriptor, os.getuid())
        finally:
            os.close(descriptor)
        for payload in [b"", b"#!/bin/bash\n", b"\xff\xd8\xff", b"\xff\xd8\xff\xc0\x00\x00"]:
            with self.assertRaises(ValueError):
                inspect_image(payload)


class ResourceTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.original = make_resource(self.directory / "original")
        self.original_data = self.original.read_bytes()
        self.target = self.directory / "installed.gresource"
        self.target.write_bytes(self.original_data)
        self.target.chmod(0o644)
        self.package = {"package": "gnome-shell-50.test", "sha256": digest(self.original_data)}
        self.backend = Backend(self.target, self.directory / "state", lambda _target: dict(self.package),
                               preserve_label=False)
        self.image = make_image(self.directory / "wallpaper.png")
        self.metadata = {"format": "png", "source_uri": "file:///test.png", "blur": 12}

    def test_apply_preserves_other_entries_and_restore_is_exact(self):
        original = entries(self.original)
        applied = self.backend.apply(self.image, self.metadata)
        modified = entries(self.target)
        self.assertEqual(modified["/unrelated/special & name.txt"], original["/unrelated/special & name.txt"])
        self.assertEqual(modified[PREFIX + "wallpaper-sync.png"], self.image)
        self.assertEqual(set(modified), set(original) | {PREFIX + "wallpaper-sync.png"})
        self.assertEqual(digest(self.target.read_bytes()), applied["installed_sha256"])
        self.assertTrue(self.backend.status()["backup_available"])
        self.backend.restore()
        self.assertEqual(self.target.read_bytes(), self.original_data)

    def test_repeat_apply_does_not_accumulate_css(self):
        self.backend.apply(self.image, self.metadata)
        first = self.target.read_bytes()
        self.backend.apply(self.image, self.metadata)
        self.assertEqual(self.target.read_bytes(), first)
        for name, data in entries(self.target).items():
            if name.endswith(".css"):
                self.assertEqual(data.count(b"Wallpaper Sync:"), 1)

    def test_import_only_verified_upstream_backup(self):
        custom = b"existing customization"
        self.target.write_bytes(custom)
        self.backend.initial_hash = digest(custom)
        old = Path(str(self.target) + ".bak-older")
        old.write_bytes(self.original_data)
        newest = Path(str(self.target) + ".bak-newest")
        newest.write_bytes(custom)
        self.backend.apply(self.image, self.metadata)
        self.assertEqual((self.backend.directory / "preinstall.gresource").read_bytes(), custom)
        self.backend.restore()
        self.assertEqual(self.target.read_bytes(), self.original_data)
        self.assertTrue(old.exists() and newest.exists())

    def test_external_modification_is_not_overwritten(self):
        self.backend.apply(self.image, self.metadata)
        self.target.write_bytes(b"external change")
        with self.assertRaisesRegex(RuntimeError, "outside Wallpaper Sync"):
            self.backend.restore()
        self.assertEqual(self.target.read_bytes(), b"external change")

    def test_corrupt_baseline_refused(self):
        self.backend.apply(self.image, self.metadata)
        snapshot = self.target.read_bytes()
        next(self.backend.directory.glob("original-*.gresource")).write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            self.backend.restore()
        self.assertEqual(self.target.read_bytes(), snapshot)

    def test_new_package_uses_new_original(self):
        self.backend.apply(self.image, self.metadata)
        upgraded = make_resource(self.directory / "upgraded", "white").read_bytes()
        self.package = {"package": "gnome-shell-50.next", "sha256": digest(upgraded)}
        self.target.write_bytes(upgraded)
        self.backend.apply(self.image, self.metadata)
        self.backend.restore()
        self.assertEqual(self.target.read_bytes(), upgraded)
        self.assertEqual(len(list(self.backend.directory.glob("original-*.gresource"))), 2)

    def test_authorization_lost_before_commit_preserves_theme(self):
        def deny():
            raise PermissionError("session is inactive")
        with self.assertRaises(PermissionError):
            self.backend.apply(self.image, self.metadata, deny)
        self.assertEqual(self.target.read_bytes(), self.original_data)

    def test_interrupted_transaction_after_replace_recovers(self):
        original_writer = atomic_json
        def fail_state(path, value):
            if Path(path).name == "state.json":
                raise OSError("simulated power loss")
            return original_writer(path, value)
        with patch("wallpaper_sync.backend.atomic_json", side_effect=fail_state):
            with self.assertRaises(OSError):
                self.backend.apply(self.image, self.metadata)
        self.assertTrue((self.backend.directory / "pending.json").exists())
        self.backend.restore()
        self.assertFalse((self.backend.directory / "pending.json").exists())
        self.assertEqual(self.target.read_bytes(), self.original_data)

    def test_symlink_theme_is_rejected(self):
        self.target.unlink()
        self.target.symlink_to(self.original)
        with self.assertRaises(RuntimeError):
            self.backend.apply(self.image, self.metadata)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.config = settings()
        for key in ["synchronize", "follow-current", "wallpaper-mode", "blur"]:
            self.config.reset(key)
        self.desktop = Gio.Settings.new("org.gnome.desktop.background")
        self.interface = Gio.Settings.new("org.gnome.desktop.interface")
        self.desktop.set_string("picture-uri", "file:///light.png")
        self.desktop.set_string("picture-uri-dark", "file:///dark.png")

    def test_active_dark_light_and_empty_dark_fallback(self):
        self.interface.set_string("color-scheme", "prefer-dark")
        self.assertEqual(desktop_source(self.config)["uri"], "file:///dark.png")
        self.interface.set_string("color-scheme", "default")
        self.assertEqual(desktop_source(self.config)["uri"], "file:///light.png")
        self.config.set_string("wallpaper-mode", "dark")
        self.assertEqual(desktop_source(self.config)["uri"], "file:///dark.png")
        self.config.set_string("wallpaper-mode", "light")
        self.assertEqual(desktop_source(self.config)["uri"], "file:///light.png")
        self.config.set_string("wallpaper-mode", "dark")
        self.desktop.set_string("picture-uri-dark", "")
        self.assertEqual(desktop_source(self.config)["uri"], "file:///light.png")

    def test_nautilus_sets_both_uris_while_sync_disabled(self):
        spec = importlib.util.spec_from_file_location("nautilus_provider", PROJECT / "nautilus/wallpaper_sync.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.config.set_boolean("synchronize", False)
        provider = module.WallpaperSyncMenu()
        location = Gio.File.new_for_path(str(PROJECT / "tests/fixtures/blue.avif"))
        provider.set_background(None, location)
        self.assertEqual(self.desktop.get_string("picture-uri"), location.get_uri())
        self.assertEqual(self.desktop.get_string("picture-uri-dark"), location.get_uri())
        self.assertFalse(self.config.get_boolean("synchronize"))
        self.assertEqual(provider.get_file_items([]), [])
        self.assertEqual(provider.get_file_items([None, None]), [])

    def test_worker_rejects_sync_when_disabled(self):
        from wallpaper_sync.worker import Worker
        class Connection:
            def register_object(self, *args):
                return 1
        class Invocation:
            error = None
            def return_dbus_error(self, name, message):
                self.error = message
        with patch("wallpaper_sync.worker.load_json", return_value={}):
            worker = Worker(Connection())
        worker.config.set_boolean("synchronize", False)
        invocation = Invocation()
        worker.call(None, None, None, None, "Sync", GLib.Variant("(b)", (True,)), invocation)
        self.assertIn("disabled", invocation.error)
        self.assertIsNone(worker.pending)


if __name__ == "__main__":
    unittest.main()

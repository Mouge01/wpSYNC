"""Unprivileged GRUB image tests; all writes are temporary local files."""

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from wallpaper_sync.grub_images import HEIGHT, WIDTH, prepare_grub, validate_grub_jpeg
from gi.repository import GdkPixbuf

PROJECT = Path(__file__).resolve().parent.parent
WORK = PROJECT / "work"
WORK.mkdir(exist_ok=True)


def image_file(path, image_format="png", width=160, height=100, alpha=False):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, alpha, 8, width, height)
    pixbuf.fill(0xff000000 if alpha else 0xff0000ff)
    pixbuf.savev(str(path), image_format, [], [])
    return pixbuf


def decode(data):
    loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
    loader.write(data)
    loader.close()
    return loader.get_pixbuf()


def pixel(pixbuf, x, y):
    start = y * pixbuf.get_rowstride() + x * pixbuf.get_n_channels()
    return tuple(pixbuf.get_pixels()[start:start + 3])


class GrubImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=WORK)
        self.directory = Path(self.temporary.name)
        self.cache_root = self.directory / "cache"
        self.cache = self.cache_root / "wallpaper-sync"
        self.cache_patch = patch("wallpaper_sync.grub_images.GLib.get_user_cache_dir",
                                 return_value=str(self.cache_root))
        self.cache_patch.start()

    def tearDown(self):
        self.cache_patch.stop()
        self.temporary.cleanup()

    def assert_valid(self, data, metadata):
        validate_grub_jpeg(data)
        self.assertEqual((metadata["width"], metadata["height"], metadata["format"]), (WIDTH, HEIGHT, "jpg"))
        self.assertEqual(metadata["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual((self.cache / "grub-current.jpg").read_bytes(), data)
        self.assertEqual((self.cache / "grub-current.jpg").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o700)

    def test_supported_raster_formats_with_misleading_extensions(self):
        for image_format in ["png", "jpeg", "webp", "bmp", "tiff"]:
            with self.subTest(image_format=image_format):
                source = self.directory / f"{image_format}.not-an-image-extension"
                image_file(source, image_format)
                original = source.read_bytes()
                data, metadata = prepare_grub(source.as_uri())
                self.assert_valid(data, metadata)
                self.assertEqual(source.read_bytes(), original)

    def test_avif_uses_native_loader(self):
        source = PROJECT / "tests/fixtures/blue.avif"
        data, metadata = prepare_grub(source.as_uri())
        self.assert_valid(data, metadata)

    def test_unicode_uri_and_metacharacters(self):
        source = self.directory / "Artist's duvar kağıdı (夜)_01% [0];$.png"
        image_file(source)
        data, metadata = prepare_grub(source.as_uri())
        self.assert_valid(data, metadata)
        self.assertEqual(metadata["source_path"], str(source))
        self.assertEqual(metadata["source_uri"], source.as_uri())

    def test_cover_resize_preserves_shape_and_crops_center(self):
        source = self.directory / "wide.png"
        pixbuf = image_file(source, width=400, height=100)
        for column, color in enumerate([0xff0000ff, 0xffff00ff, 0x0000ffff, 0x00ff00ff]):
            stripe = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 100, 100)
            stripe.fill(color)
            stripe.copy_area(0, 0, 100, 100, pixbuf, column * 100, 0)
        pixbuf.savev(str(source), "png", [], [])
        data, metadata = prepare_grub(source.as_uri())
        self.assert_valid(data, metadata)
        result = decode(data)
        left, right = pixel(result, 100, 900), pixel(result, 2780, 900)
        self.assertTrue(left[0] > 240 and left[1] > 240 and left[2] < 15, left)
        self.assertTrue(right[0] < 15 and right[1] < 15 and right[2] > 240, right)

    def test_exif_rotation_happens_before_center_crop_and_is_stripped(self):
        source = self.directory / "oriented.jpg"
        pixbuf = image_file(source)
        stripe = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 80, 100)
        stripe.fill(0x0000ffff)
        stripe.copy_area(0, 0, 80, 100, pixbuf, 80, 0)
        pixbuf.savev(str(source), "jpeg", [], [])
        # A minimal little-endian EXIF IFD: orientation=6 (90 degrees clockwise).
        tiff = b"II" + struct.pack("<HIH", 42, 8, 1) + struct.pack("<HHIHHI", 274, 3, 1, 6, 0, 0)
        exif = b"Exif\x00\x00" + tiff
        original = source.read_bytes()
        source.write_bytes(original[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + original[2:])
        data, metadata = prepare_grub(source.as_uri())
        self.assert_valid(data, metadata)
        self.assertNotIn(b"Exif", data)
        result = decode(data)
        upper, lower = pixel(result, 1440, 100), pixel(result, 1440, 1700)
        self.assertTrue(upper[0] > 240 and upper[2] < 15, upper)
        self.assertTrue(lower[0] < 15 and lower[2] > 240, lower)

    def test_alpha_flattens_to_black(self):
        source = self.directory / "transparent.png"
        image_file(source, alpha=True)
        data, metadata = prepare_grub(source.as_uri())
        self.assert_valid(data, metadata)
        self.assertEqual(pixel(decode(data), 1440, 900), (0, 0, 0))

    def test_identical_input_skips_conversion_and_preserves_cache_mtime(self):
        source = self.directory / "source.png"
        image_file(source)
        first, _ = prepare_grub(source.as_uri())
        mtime = (self.cache / "grub-current.jpg").stat().st_mtime_ns
        with patch("wallpaper_sync.grub_images.subprocess.run", side_effect=AssertionError("Unexpected conversion")):
            second, _ = prepare_grub(source.as_uri())
        self.assertEqual(first, second)
        self.assertEqual((self.cache / "grub-current.jpg").stat().st_mtime_ns, mtime)

    def test_configured_resolution_rebuilds_cache_and_is_used_for_validation(self):
        source = self.directory / "source.png"
        image_file(source)
        first, _ = prepare_grub(source.as_uri())
        with patch("wallpaper_sync.grub_images.subprocess.run", wraps=subprocess.run) as processing:
            result, metadata = prepare_grub(source.as_uri(), size=(1920, 1080))
        self.assertEqual(processing.call_count, 1)
        self.assertNotEqual(first, result)
        self.assertEqual((metadata["width"], metadata["height"]), (1920, 1080))
        validate_grub_jpeg(result, size=(1920, 1080))
        with self.assertRaises(ValueError):
            validate_grub_jpeg(result)
        with patch("wallpaper_sync.grub_images.subprocess.run", side_effect=AssertionError("Unexpected conversion")):
            cached, _ = prepare_grub(source.as_uri(), size=(1920, 1080))
        self.assertEqual(result, cached)

    def test_invalid_resolution_is_rejected_before_reading_or_converting(self):
        with patch("wallpaper_sync.grub_images._snapshot", side_effect=AssertionError("Unexpected file read")):
            for size in [(8192, 8192), (True, 1080), (1920, 0), (3840, 2161)]:
                with self.subTest(size=size), self.assertRaises(ValueError):
                    prepare_grub("file:///missing-image.png", size=size)

    def test_corrupt_cached_image_is_rebuilt(self):
        source = self.directory / "source.png"
        image_file(source)
        first, _ = prepare_grub(source.as_uri())
        corrupt = b"\xff\xd8not a JPEG\xff\xd9"
        (self.cache / "grub-current.jpg").write_bytes(corrupt)
        record_path = self.cache / "grub-current.json"
        record = json.loads(record_path.read_text())
        record["sha256"] = hashlib.sha256(corrupt).hexdigest()
        record_path.write_text(json.dumps(record))
        with patch("wallpaper_sync.grub_images.subprocess.run", wraps=subprocess.run) as processing:
            second, _ = prepare_grub(source.as_uri())
        self.assertEqual(processing.call_count, 1)
        self.assertEqual(first, second)

    def test_invalid_missing_remote_and_fifo_preserve_last_cache(self):
        source = self.directory / "source.png"
        image_file(source)
        first, _ = prepare_grub(source.as_uri())
        invalid = self.directory / "invalid.png"
        invalid.write_bytes(b"not an image")
        fifo = self.directory / "fifo"
        os.mkfifo(fifo)
        for uri in [invalid.as_uri(), (self.directory / "missing.png").as_uri(),
                    "https://example.com/image.png", fifo.as_uri()]:
            with self.subTest(uri=uri), self.assertRaises((ValueError, OSError)):
                prepare_grub(uri)
            self.assertEqual((self.cache / "grub-current.jpg").read_bytes(), first)

    def test_unsafe_cache_directory_is_rejected(self):
        source = self.directory / "source.png"
        image_file(source)
        self.cache_root.mkdir()
        self.cache.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "private directory"):
            prepare_grub(source.as_uri())

    def test_jpeg_validation_rejects_truncated_and_wrong_size_outputs(self):
        source = self.directory / "source.jpg"
        image_file(source, "jpeg")
        for data in [b"", b"not jpeg", source.read_bytes(), source.read_bytes()[:-2]]:
            with self.assertRaises(ValueError):
                validate_grub_jpeg(data)


if __name__ == "__main__":
    unittest.main()

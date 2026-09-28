"""Prepare GRUB wallpaper as the session user; never import in the root helper."""

import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile

from .common import MAX_BYTES, local_path, log
from .grub_config import validate_grub_size
from .images import FORMATS, check_size
from gi.repository import GdkPixbuf, GLib

WIDTH, HEIGHT = 2880, 1800


def _processor(size):
    return f"grub-jpeg-v2:{size[0]}x{size[1]}:cover:center:srgb:8bit:black:q90:baseline"


def _snapshot(path, limit=MAX_BYTES):
    """Read once through a bounded descriptor, never a FIFO or final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError("The wallpaper must be a nonempty regular file of at most 64 MiB.")
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("The wallpaper changed while reading or exceeds the supported size.")
    return data


def validate_grub_jpeg(data, size=(WIDTH, HEIGHT)):
    """Validate baseline, 8-bit RGB JPEG framing and actually decode all pixels."""
    output_width, output_height = validate_grub_size(size)
    if not 0 < len(data) <= MAX_BYTES or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("GRUB wallpaper is not a complete JPEG.")
    # GdkPixbuf also accepts progressive JPEG; explicitly require baseline DCT.
    position, baseline = 2, False
    while position < len(data) - 2:
        if data[position] != 0xff:
            raise ValueError("Invalid JPEG marker framing.")
        while position < len(data) and data[position] == 0xff:
            position += 1
        if position + 3 > len(data):
            raise ValueError("Truncated JPEG marker.")
        marker = data[position]
        position += 1
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            raise ValueError("Truncated JPEG segment.")
        if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
            if marker != 0xc0 or length != 17:
                raise ValueError("GRUB wallpaper must use baseline RGB JPEG.")
            depth, height, width, components = struct.unpack(">BHHB", data[position + 2:position + 8])
            if (depth, width, height, components) != (8, output_width, output_height, 3):
                raise ValueError(f"GRUB wallpaper must be an 8-bit {output_width}×{output_height} RGB JPEG.")
            baseline = True
        if marker == 0xda:
            break
        position += length
    if not baseline:
        raise ValueError("GRUB wallpaper has no baseline JPEG frame.")
    loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
    try:
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None or (pixbuf.get_width(), pixbuf.get_height()) != (output_width, output_height):
            raise ValueError("Decoded GRUB wallpaper dimensions are incorrect.")
        # A full PixbufLoader decode, not a header-only dimension probe.
        if not pixbuf.get_pixels():
            raise ValueError("Decoded GRUB wallpaper is empty.")
    except GLib.Error as error:
        raise ValueError("GRUB wallpaper JPEG decoding failed.") from error


def _cached_image(cache, source_hash, size):
    try:
        record = json.loads(_snapshot(cache / "grub-current.json", 4096))
        if record.get("processor") != _processor(size) or record.get("source_sha256") != source_hash:
            return None
        result = _snapshot(cache / "grub-current.jpg")
        if hashlib.sha256(result).hexdigest() != record.get("sha256"):
            return None
        validate_grub_jpeg(result, size=size)
        return result
    except (OSError, ValueError, TypeError, AttributeError, GLib.Error):
        return None


def prepare_grub(uri, size=(WIDTH, HEIGHT)):
    """Return validated JPEG bytes plus metadata without touching any root path."""
    size = validate_grub_size(size)
    output_width, output_height = size
    source = local_path(uri)
    data = _snapshot(source)
    source_hash = hashlib.sha256(data).hexdigest()
    cache = Path(GLib.get_user_cache_dir()) / "wallpaper-sync"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache_info = cache.lstat()
    if not stat.S_ISDIR(cache_info.st_mode) or cache_info.st_uid != os.getuid() or cache_info.st_mode & 0o077:
        raise ValueError("Wallpaper Sync cache must be a private directory owned by the session user.")
    result = _cached_image(cache, source_hash, size)
    if result is not None:
        log("grub_image_cache_hit", source=str(source), sha256=hashlib.sha256(result).hexdigest())
    else:
        log("grub_processing", source=str(source))
        with tempfile.TemporaryDirectory(prefix="grub-prepare-", dir=cache) as temporary:
            directory = Path(temporary)
            staged = directory / "source"
            staged.write_bytes(data)
            image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(str(staged))
            if image_format is None or image_format.get_name() not in FORMATS:
                raise ValueError("Supported raster formats: JPEG, PNG, WebP, AVIF, JPEG XL, BMP and TIFF.")
            check_size(width, height)
            name = image_format.get_name()
            if name == "avif":
                # The GNOME loader remains usable if ImageMagick lacks AVIF.
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(staged)).apply_embedded_orientation()
                staged, name = directory / "decoded.png", "png"
                pixbuf.savev(str(staged), "png", [], [])
            output = directory / "output.jpg"
            command = ["/usr/bin/magick", "-limit", "memory", "512MiB",
                       "-limit", "map", "1GiB", "-limit", "disk", "4GiB",
                       "-limit", "time", "90", "-limit", "thread", "4", "-regard-warnings",
                       f"{name}:{staged}[0]", "-auto-orient", "-colorspace", "sRGB",
                       "-resize", f"{output_width}x{output_height}^", "-gravity", "center",
                       "-background", "black", "-extent", f"{output_width}x{output_height}",
                       "-alpha", "remove", "-alpha", "off", "-type", "TrueColor",
                       "-depth", "8", "-interlace", "none", "-quality", "90", "-strip",
                       f"JPEG:{output}"]
            process = subprocess.run(command, capture_output=True, text=True, timeout=105,
                                     env={**os.environ, "MAGICK_TEMPORARY_PATH": str(directory)})
            if process.returncode:
                raise RuntimeError("GRUB image processing failed: " + process.stderr[-2000:])
            result = _snapshot(output)
            validate_grub_jpeg(result, size=size)
            output.chmod(0o600)
            record = {"processor": _processor(size), "source_sha256": source_hash,
                      "sha256": hashlib.sha256(result).hexdigest()}
            pending = directory / "cache.json"
            pending.write_text(json.dumps(record) + "\n")
            pending.chmod(0o600)
            # Neither a preexisting cache symlink nor source-name metacharacters
            # can redirect these replacements or the forced ImageMagick coder.
            os.replace(output, cache / "grub-current.jpg")
            os.replace(pending, cache / "grub-current.json")
            log("grub_image_generated", source=str(source), width=output_width, height=output_height,
                bytes=len(result), sha256=record["sha256"])
    return result, {"source_uri": uri, "source_path": str(source), "format": "jpg",
                    "sha256": hashlib.sha256(result).hexdigest(), "width": output_width, "height": output_height}

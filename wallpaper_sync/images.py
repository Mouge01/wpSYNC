"""Unprivileged image preparation. Never imported by the privileged backend."""

import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from .common import MAX_BYTES, MAX_PIXELS, MAX_SIDE, local_path, log

FORMATS = {"png", "jpeg", "webp", "avif", "bmp", "tiff", "jxl"}


def check_size(width, height):
    if min(width, height) < 1 or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
        raise ValueError(f"Image dimensions exceed the supported limit: {width}×{height}.")


def prepare(uri, blur=12, convert=True):
    if type(blur) is not int or not 0 <= blur <= 30:
        raise ValueError("Blur must be an integer from 0 to 30.")
    source = local_path(uri)
    cache = Path(GLib.get_user_cache_dir()) / "wallpaper-sync"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=cache) as temporary:
        directory = Path(temporary)
        staged = directory / "source"
        # Open once, reject devices/FIFOs, and copy bounded bytes before inspecting.
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise ValueError("The wallpaper must be a regular image file of at most 64 MiB.")
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Image changed while reading or exceeds 64 MiB.")
        staged.write_bytes(data)
        image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(str(staged))
        if image_format is None or image_format.get_name() not in FORMATS:
            raise ValueError("Supported raster formats: JPEG, PNG, WebP, AVIF, JPEG XL, BMP and TIFF.")
        name = image_format.get_name()
        check_size(width, height)
        if name not in {"png", "jpeg"} and not convert:
            raise ValueError(f"{name.upper()} needs conversion; enable Convert unsupported formats.")
        if name in {"png", "jpeg"} and blur == 0:
            result, extension = data, "png" if name == "png" else "jpg"
        else:
            if name == "avif":
                # Fedora's ImageMagick may lack its HEIC/AVIF coder. GNOME's loader
                # is already installed and decodes this format without root access.
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(staged))
                pixbuf = pixbuf.apply_embedded_orientation()
                pixbuf.savev(str(directory / "decoded.png"), "png", [], [])
                staged, name = directory / "decoded.png", "png"
            output = directory / "current.png"
            # Fedora ships Q16-HDRI: a 4000×4000 wallpaper needs multiple
            # ~0.5 GiB pixel caches. Bound RAM and allow scratch disk spill.
            command = ["/usr/bin/magick", "-limit", "memory", "512MiB",
                       "-limit", "map", "1GiB", "-limit", "disk", "4GiB",
                       "-limit", "time", "90", "-limit", "thread", "4",
                       f"{name}:{staged}[0]", "-auto-orient", "-colorspace", "sRGB", "-strip"]
            if blur:
                command += ["-blur", f"0x{blur}"]
            command += ["-depth", "8", f"PNG:{output}"]
            process = subprocess.run(command, capture_output=True, text=True, timeout=105,
                                     env={**os.environ, "MAGICK_TEMPORARY_PATH": str(directory)})
            if process.returncode:
                raise RuntimeError("Image processing failed: " + process.stderr[-2000:])
            if output.stat().st_size > MAX_BYTES:
                raise ValueError("Processed wallpaper exceeds 64 MiB.")
            result, extension = output.read_bytes(), "png"
            log("conversion_performed", source=str(source), format=image_format.get_name(), blur=blur)
        # Atomic stable cache for inspection; immutable transport below does not
        # depend on this user-writable path remaining unchanged.
        cached = cache / f"current.{extension}"
        pending = directory / "cached"
        pending.write_bytes(result)
        pending.chmod(0o600)
        os.replace(pending, cached)
    return result, {"source_uri": uri, "source_path": str(source), "blur": blur,
                    "format": extension, "sha256": hashlib.sha256(result).hexdigest(),
                    "width": width, "height": height}


def sealed_image(data):
    descriptor = os.memfd_create("wallpaper-sync-image", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(data)
            stream.flush()
        os.lseek(descriptor, 0, os.SEEK_SET)
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise

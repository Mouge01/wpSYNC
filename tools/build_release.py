#!/usr/bin/python3
"""Build a deterministic, explicitly allowlisted source release using only stdlib."""

import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import tempfile

PROJECT = Path(__file__).resolve().parent.parent
MANIFEST = "tools/release_manifest.txt"
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
BLOCKED_PARTS = {".git", "__pycache__", "work", "dist", ".venv", "node_modules"}
BLOCKED_SUFFIXES = {".pyc", ".pyo", ".log", ".pem", ".key", ".p12", ".pfx"}


def safe_source(root, relative):
    """Reject traversal, links, generated files and obvious credential material."""
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or str(path) != relative or any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"Invalid release path: {relative!r}")
    if any(part in BLOCKED_PARTS or part.startswith(".env") for part in path.parts):
        raise ValueError(f"Excluded release path: {relative}")
    if path.name == "gschemas.compiled" or path.suffix.lower() in BLOCKED_SUFFIXES:
        raise ValueError(f"Excluded generated or private file: {relative}")
    source = root
    for part in path.parts:
        source /= part
        if source.is_symlink():
            raise ValueError(f"Symlinks are not allowed in a release: {relative}")
    info = source.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Release entry is not a regular file: {relative}")
    return source, info


def release_files(root):
    manifest, _ = safe_source(root, MANIFEST)
    paths = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate paths in release manifest.")
    required = {MANIFEST, "VERSION", "README.md", "LICENSE", "THIRD_PARTY_LICENSES.md",
                "install.sh", "uninstall.sh", "tools/install.py", "tools/build_release.py"}
    if not required.issubset(paths):
        raise ValueError("Release manifest is missing required installer, license, or build files.")
    return [(relative, *safe_source(root, relative)) for relative in sorted(paths)]


def build_release(root=PROJECT, output=None, epoch=0):
    root = Path(root).resolve()
    version_file, _ = safe_source(root, "VERSION")
    version = version_file.read_text(encoding="ascii").strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("VERSION must contain a plain major.minor.patch version, for example 0.1.0.")
    if not isinstance(epoch, int) or not 0 <= epoch <= 0xffffffff:
        raise ValueError("SOURCE_DATE_EPOCH must be an integer between 0 and 4294967295.")
    files = release_files(root)
    output = Path(output) if output is not None else root / "dist"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"wpSYNC-{version}.tar.gz"
    checksum = output / f"{archive.name}.sha256"
    descriptor, temporary = tempfile.mkstemp(prefix=".wpsync-release-", dir=output)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=epoch, compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle:
                    for relative, source, info in files:
                        data = source.read_bytes()
                        entry = tarfile.TarInfo(f"wpSYNC-{version}/{relative}")
                        entry.size = len(data)
                        entry.mtime = epoch
                        entry.mode = 0o755 if info.st_mode & 0o111 else 0o644
                        entry.uid = entry.gid = 0
                        entry.uname = entry.gname = ""
                        bundle.addfile(entry, io.BytesIO(data))
            destination.flush()
            os.fchmod(destination.fileno(), 0o644)
            os.fsync(destination.fileno())
        os.replace(temporary, archive)
    finally:
        Path(temporary).unlink(missing_ok=True)
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="ascii")
    return archive, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Output directory (default: dist/)")
    args = parser.parse_args()
    try:
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
        for result in build_release(output=args.output, epoch=epoch):
            print(result)
    except (OSError, ValueError) as error:
        parser.exit(1, f"wpSYNC release: {error}\n")


if __name__ == "__main__":
    main()

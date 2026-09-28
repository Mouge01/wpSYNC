#!/usr/bin/python3
"""Validate the source tree without installing or contacting running services."""

import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parent.parent


def main():
    if os.geteuid() == 0:
        raise RuntimeError("Run make check as a normal user; privileged tests would be skipped.")
    required = ["glib-compile-schemas", "glib-compile-resources", "gresource", "magick", "bash", "node"]
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Missing check dependencies: " + ", ".join(missing))
    environment = dict(os.environ, GSETTINGS_BACKEND="memory", PYTHONDONTWRITEBYTECODE="1")
    schema_dir = PROJECT / "extension/schemas"
    compiled = schema_dir / "gschemas.compiled"
    if compiled.is_symlink():
        raise RuntimeError("Refusing to replace a symlinked compiled schema.")
    previous = compiled.read_bytes() if compiled.exists() else None
    previous_mode = compiled.stat().st_mode & 0o777 if previous is not None else None
    try:
        subprocess.run(["glib-compile-schemas", "--strict", str(schema_dir)], check=True, env=environment)
        with tempfile.TemporaryDirectory(prefix="wpsync-syntax-") as temporary:
            sources = sorted(path for directory in ["wallpaper_sync", "nautilus", "tools", "tests"]
                             for path in (PROJECT / directory).rglob("*.py"))
            sources += [PROJECT / "cli/wallpaper-sync", PROJECT / "helper/wallpaper-sync-helper"]
            for index, source in enumerate(sources):
                py_compile.compile(str(source), cfile=str(Path(temporary) / f"{index}.pyc"),
                                   dfile=str(source.relative_to(PROJECT)), doraise=True)
        subprocess.run(["bash", "-n", "install.sh", "uninstall.sh"], cwd=PROJECT, check=True)
        for source in sorted((PROJECT / "extension").glob("*.js")):
            subprocess.run(["node", "--input-type=module", "--check"],
                           input=source.read_bytes(), cwd=PROJECT, check=True)
        subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                       cwd=PROJECT, check=True, env=environment)
    finally:
        if previous is None:
            compiled.unlink(missing_ok=True)
        else:
            compiled.write_bytes(previous)
            compiled.chmod(previous_mode)
    print("Schemas, Python, shell, JavaScript, and isolated tests passed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, py_compile.PyCompileError, subprocess.CalledProcessError) as error:
        print(f"wpSYNC checks: {error}", file=sys.stderr)
        raise SystemExit(1)

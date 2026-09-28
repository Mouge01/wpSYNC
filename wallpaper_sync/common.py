"""Shared constants, settings, local state and structured journal messages."""

import datetime
import json
import os
from pathlib import Path
import tempfile

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

UUID = "wallpaper-sync@mouge"
SCHEMA = "org.gnome.shell.extensions.wallpaper-sync"
BUS = "io.github.mouge.WallpaperSync"
OBJECT = "/io/github/mouge/WallpaperSync"
HELPER_BUS = BUS + "1"
HELPER_OBJECT = OBJECT + "1"
POLKIT_PREFIX = "io.github.mouge.wallpaper-sync"
MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 64 * 1024 * 1024
MAX_SIDE = 16384
ROOT = Path(__file__).resolve().parent.parent


def version():
    return (ROOT / "VERSION").read_text().strip()


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def log(event, **fields):
    print(json.dumps({"time": timestamp(), "event": event, **fields}, ensure_ascii=False), flush=True)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(tmp).unlink(missing_ok=True)


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default


def user_state_path():
    return Path(GLib.get_user_state_dir()) / "wallpaper-sync/status.json"


def settings():
    candidates = [ROOT / "extension/schemas",
                  Path(GLib.get_user_data_dir()) / "gnome-shell/extensions" / UUID / "schemas"]
    for candidate in candidates:
        if (candidate / "gschemas.compiled").is_file():
            source = Gio.SettingsSchemaSource.new_from_directory(
                str(candidate), Gio.SettingsSchemaSource.get_default(), False)
            schema = source.lookup(SCHEMA, False)
            if schema:
                return Gio.Settings.new_full(schema, None, None)
    raise RuntimeError("Wallpaper Sync schemas are missing; run install.sh or compile the project schemas.")


def desktop_source(config=None):
    config = config or settings()
    background = Gio.Settings.new("org.gnome.desktop.background")
    scheme = Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme")
    mode = config.get_string("wallpaper-mode")
    dark = mode == "dark" or (mode == "active" and scheme == "prefer-dark")
    light_uri = background.get_string("picture-uri")
    dark_uri = background.get_string("picture-uri-dark")
    return {"uri": (dark_uri or light_uri) if dark else light_uri,
            "active_uri": (dark_uri or light_uri) if scheme == "prefer-dark" else light_uri,
            "light_uri": light_uri, "dark_uri": dark_uri, "color_scheme": scheme,
            "mode": mode, "blur": config.get_int("blur"),
            "convert": config.get_boolean("convert-unsupported"),
            "grub_enabled": config.get_boolean("grub-enabled")}


def local_path(uri):
    file = Gio.File.new_for_uri(uri)
    if file.get_uri_scheme() != "file" or file.get_path() is None:
        raise ValueError("Select a local image; remote URIs and slideshow XML are not supported.")
    return Path(file.get_path()).resolve(strict=True)


HELPER_XML = """<node><interface name="io.github.mouge.WallpaperSync1">
 <method name="ApplyGrub"><arg type="h" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
 <method name="Apply"><arg type="h" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
 <method name="RestoreGrub"><arg type="s" direction="out"/></method>
 <method name="Restore"><arg type="s" direction="out"/></method>
 <method name="GetStatus"><arg type="s" direction="out"/></method>
</interface></node>"""

USER_XML = """<node><interface name="io.github.mouge.WallpaperSync">
 <method name="Sync"><arg type="b" direction="in"/><arg type="s" direction="out"/></method>
 <method name="RestoreGrub"><arg type="s" direction="out"/></method>
 <method name="Restore"><arg type="s" direction="out"/></method>
 <method name="GetStatus"><arg type="s" direction="out"/></method>
 <signal name="Completed"><arg type="b"/><arg type="s"/></signal>
</interface></node>"""

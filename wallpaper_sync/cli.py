"""User-facing commands. No privilege escalation occurs here."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from gi.repository import Gio, GLib

from .client import helper_call, user_call
from .common import UUID, desktop_source, load_json, settings, user_state_path, version
from .images import FORMATS


def run_read(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        return (result.stdout + result.stderr).strip() or f"exit={result.returncode}"
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)


def diagnostics(doctor=False):
    result = {"last_sync": load_json(user_state_path())}
    try:
        config = settings()
        result.update({"desktop": desktop_source(config), "enabled": config.get_boolean("synchronize"),
                       "automatic": config.get_boolean("follow-current")})
    except Exception as error:
        result["settings_error"] = str(error)
    try:
        current = helper_call("GetStatus")
        result["grub"] = current.pop("grub", {})
        result["gdm"] = current
    except Exception as error:
        result["helper_error"] = str(error)
    if doctor:
        result.update({
            "fedora": Path("/etc/os-release").read_text(),
            "gnome": run_read(["gnome-shell", "--version"]),
            "nautilus": run_read(["nautilus", "--version"]),
            "extension": run_read(["gnome-extensions", "info", UUID]),
            "nautilus_integration": (Path(GLib.get_user_data_dir()) / "nautilus-python/extensions/wallpaper_sync.py").is_file(),
            "helper_installed": Path("/usr/local/libexec/wallpaper-sync-helper").is_file(),
            "polkit_actions": run_read(["pkaction", "--action-id", "io.github.mouge.wallpaper-sync.apply", "--verbose"]),
            "polkit_rule_installed": result.get("gdm", {}).get("polkit_rule_installed", "unknown; helper unavailable"),
            "imagemagick": run_read(["/usr/bin/magick", "--version"]),
            "image_formats": run_read(["/usr/bin/magick", "identify", "-list", "format"]),
            "user_service": run_read(["systemctl", "--user", "status", "wallpaper-sync.service", "--no-pager", "--lines=5"]),
            "user_journal": run_read(["journalctl", "--user", "-u", "wallpaper-sync", "-n", "20", "--no-pager"]),
            "helper_journal": run_read(["journalctl", "-u", "wallpaper-sync-helper", "-n", "20", "--no-pager"]),
        })
    return result


def wait_job(reply):
    if "job_id" not in reply:
        return reply
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = user_call("GetStatus")
        outcome = status.get("completed", {}).get(reply["job_id"])
        if outcome:
            if not outcome["success"]:
                raise RuntimeError(outcome["message"])
            return outcome
        time.sleep(0.5)
    raise RuntimeError("Timed out waiting for synchronization; inspect wallpaper-sync status before retrying.")


def set_wallpaper(filename):
    from gi.repository import GdkPixbuf
    path = Path(filename).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Select a regular image file.")
    image_format, _width, _height = GdkPixbuf.Pixbuf.get_file_info(str(path))
    if image_format is None or image_format.get_name() not in FORMATS:
        raise ValueError("Unsupported wallpaper image.")
    background = Gio.Settings.new("org.gnome.desktop.background")
    background.delay()
    uri = Gio.File.new_for_path(str(path)).get_uri()
    for key in ("picture-uri", "picture-uri-dark"):
        if not background.set_string(key, uri):
            raise RuntimeError(f"Desktop setting is locked: {key}")
    background.apply()
    Gio.Settings.sync()
    return {"desktop_wallpaper": uri, "note": "The enabled GNOME extension handles automatic GDM and GRUB synchronization."}


def main():
    parser = argparse.ArgumentParser(prog="wallpaper-sync")
    parser.add_argument("--version", action="version", version="wpSYNC " + version())
    parser.add_argument("command", choices=["status", "sync", "restore", "doctor", "set", "grub-enable", "grub-disable", "restore-grub", "_serve"])
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "_serve":
            from .worker import main as serve
            serve()
            return
        if args.command in {"status", "doctor"}:
            result = diagnostics(args.command == "doctor")
        elif args.command == "set":
            if not args.file:
                parser.error("set requires an image filename")
            result = set_wallpaper(args.file)
        elif args.command == "sync":
            result = wait_job(user_call("Sync", GLib.Variant("(b)", (True,))))
        elif args.command in {"grub-enable", "grub-disable"}:
            config = settings()
            if args.command == "grub-enable":
                grub = helper_call("GetStatus").get("grub", {})
                if not grub.get("enabled"):
                    raise RuntimeError("GRUB is not configured. Re-run the installer with --grub-theme and --grub-size.")
            config.set_boolean("grub-enabled", args.command == "grub-enable")
            Gio.Settings.sync()
            result = {"grub_enabled": config.get_boolean("grub-enabled")}
            if result["grub_enabled"] and config.get_boolean("synchronize"):
                result = wait_job(user_call("Sync", GLib.Variant("(b)", (True,))))
        elif args.command == "restore-grub":
            result = wait_job(user_call("RestoreGrub"))
        else:
            result = wait_job(user_call("Restore"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(f"Wallpaper Sync: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

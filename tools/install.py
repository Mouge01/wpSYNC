#!/usr/bin/python3
"""Explicit, reviewable installation. This tool is not a privileged RPC endpoint."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from wallpaper_sync.grub_config import parse_grub_config

ASSUME_YES = False
LIB = Path("/usr/local/lib/wallpaper-sync")
STATE = Path("/var/lib/wallpaper-sync")
MANIFEST = STATE / "install-manifest.json"
OWNER = Path("/etc/wallpaper-sync/owner.json")
UUID = "wallpaper-sync@mouge"
THEME = Path("/usr/share/gnome-shell/gnome-shell-theme.gresource")
PACKAGES = ["gnome-shell", "gdm", "gjs", "nautilus", "nautilus-python", "python3",
            "python3-gobject", "python3-pillow", "gtk4", "libadwaita", "gdk-pixbuf2", "glycin-loaders",
            "ImageMagick", "glib2-devel", "polkit", "policycoreutils", "dbus", "systemd"]
FIXED_FILES = {
    "cli/wallpaper-sync": "/usr/local/bin/wallpaper-sync",
    "helper/wallpaper-sync-helper": "/usr/local/libexec/wallpaper-sync-helper",
    "systemd/wallpaper-sync-helper.service": "/etc/systemd/system/wallpaper-sync-helper.service",
    "dbus/io.github.mouge.WallpaperSync1.service": "/usr/share/dbus-1/system-services/io.github.mouge.WallpaperSync1.service",
    "dbus/io.github.mouge.WallpaperSync1.conf": "/etc/dbus-1/system.d/io.github.mouge.WallpaperSync1.conf",
    "polkit/io.github.mouge.wallpaper-sync.policy": "/usr/share/polkit-1/actions/io.github.mouge.wallpaper-sync.policy",
}
RULE = Path("/etc/polkit-1/rules.d/49-wallpaper-sync.rules")


def command(arguments, **kwargs):
    return subprocess.run(arguments, check=True, **kwargs)


def confirm(message, default=False):
    if ASSUME_YES:
        return True
    if not sys.stdin.isatty():
        raise RuntimeError("An interactive terminal is required for installation confirmation.")
    answer = input(message + (" [Y/n] " if default else " [y/N] ")).strip().lower()
    return answer in {"y", "yes"} or (not answer and default)


def selected_grub_config(requested=None, owner=None):
    """Preserve upgrades, but require explicit opt-in for a fresh installation."""
    if requested is not None:
        return parse_grub_config(requested)
    if owner is None:
        owner = json.loads(OWNER.read_text()) if OWNER.exists() else {}
    if "grub" in owner:
        return parse_grub_config(owner["grub"])
    legacy_helper = LIB / "wallpaper_sync/grub.py"
    unit = Path(FIXED_FILES["systemd/wallpaper-sync-helper.service"])
    if owner and legacy_helper.is_file() and unit.is_file() and "-/boot/grub2/themes/tela" in unit.read_text():
        # Migrate the preceding local GRUB-enabled release without losing its
        # working fixed profile. Older GDM-only installs remain GDM-only.
        return parse_grub_config({"enabled": True, "theme": "tela", "width": 2880, "height": 1800})
    return {"enabled": False}


def grub_request(args):
    if args.without_grub:
        if args.grub_theme or args.grub_size:
            raise ValueError("--without-grub cannot be combined with GRUB theme/size options.")
        return {"enabled": False}
    if bool(args.grub_theme) != bool(args.grub_size):
        raise ValueError("Supply --grub-theme and --grub-size together.")
    if not args.grub_theme:
        return None
    fields = args.grub_size.lower().split("x")
    if len(fields) != 2 or not all(field.isascii() and field.isdecimal() for field in fields):
        raise ValueError("--grub-size must be WIDTHxHEIGHT, for example 1920x1080.")
    return parse_grub_config({"enabled": True, "theme": args.grub_theme,
                              "width": int(fields[0]), "height": int(fields[1])})


def grub_arguments(config):
    config = parse_grub_config(config)
    return (["--grub-theme", config["theme"], "--grub-size", f'{config["width"]}x{config["height"]}']
            if config["enabled"] else ["--without-grub"])


def system_files(username, grub_config=None):
    files = {Path(destination): ((PROJECT / source).read_bytes(), 0o755 if source.startswith(("cli/", "helper/")) else 0o644)
             for source, destination in FIXED_FILES.items()}
    grub_config = parse_grub_config(grub_config)
    if grub_config["enabled"]:
        unit = Path(FIXED_FILES["systemd/wallpaper-sync-helper.service"])
        data, mode = files[unit]
        lines = data.decode().splitlines()
        lines = [line + " -/boot/grub2/themes/" + grub_config["theme"]
                 if line.startswith("ReadWritePaths=") else line for line in lines]
        files[unit] = (("\n".join(lines) + "\n").encode(), mode)
    for directory, pattern in [("wallpaper_sync", "*.py"), ("licenses", "*"), ("docs", "*.md")]:
        for source in sorted((PROJECT / directory).glob(pattern)):
            if source.is_file():
                files[LIB / directory / source.name] = (source.read_bytes(), 0o644)
    for filename in ["LICENSE", "THIRD_PARTY_LICENSES.md", "README.md", "VERSION", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md"]:
        files[LIB / filename] = ((PROJECT / filename).read_bytes(), 0o644)
    rule = (PROJECT / "polkit/49-wallpaper-sync.rules.in").read_text().replace("@USERNAME@", json.dumps(username))
    files[RULE] = (rule.encode(), 0o644)
    return files


def ensure_root_path(path):
    for candidate in [path, *path.parents]:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError(f"Unsafe installation path: {candidate}")


def write_file(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".wallpaper-sync-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reload_system():
    command(["/usr/bin/systemctl", "daemon-reload"])
    command(["/usr/bin/busctl", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
             "org.freedesktop.DBus", "ReloadConfig"])


def install_system(uid, requested_grub=None):
    if os.geteuid() != 0 or type(uid) is not int or uid < 1000:
        raise RuntimeError("System installation requires root and a regular destination user.")
    account = pwd.getpwuid(uid)
    ensure_root_path(OWNER)
    ensure_root_path(MANIFEST)
    previous_bytes = MANIFEST.read_bytes() if MANIFEST.exists() else None
    previous = json.loads(previous_bytes) if previous_bytes is not None else {}
    if previous and previous.get("uid") != uid:
        raise RuntimeError("Wallpaper Sync is already installed for another account.")
    existing_owner = OWNER.exists()
    if existing_owner:
        owner = json.loads(OWNER.read_text())
        if owner.get("uid") != uid:
            raise RuntimeError("Existing owner configuration belongs to another account.")
    else:
        owner = {"uid": uid, "username": account.pw_name,
                 "initial_theme_sha256": hashlib.sha256(THEME.read_bytes()).hexdigest()}
    grub_config = selected_grub_config(requested_grub, owner if existing_owner else {})
    if grub_config["enabled"]:
        from wallpaper_sync.grub import GrubBackend
        # Verify the existing theme/image and trusted paths before installing
        # any files. This never installs a theme or changes boot configuration.
        GrubBackend(uid=uid, config=grub_config).status()
    owner["grub"] = grub_config
    files = system_files(account.pw_name, grub_config)
    files[OWNER] = (json.dumps(owner, indent=2).encode() + b"\n", 0o644)
    allowed = set(previous.get("files", []))
    for destination in files:
        ensure_root_path(destination)
        if destination.exists() and str(destination) not in allowed:
            raise RuntimeError(f"Refusing to overwrite an untracked existing file: {destination}")
    ensure_root_path(STATE)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    snapshots = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None for path in files}
    if previous:
        command(["/usr/bin/systemctl", "stop", "wallpaper-sync-helper.service"])
    try:
        for destination, (data, mode) in files.items():
            write_file(destination, data, mode)
        # Restore Fedora's default SELinux labels only on this application's files.
        command(["/usr/bin/restorecon", *map(str, files)])
        record = {"uid": uid, "files": list(map(str, files)), "version": 2, "release": (PROJECT / "VERSION").read_text().strip()}
        write_file(MANIFEST, json.dumps(record, indent=2).encode() + b"\n", 0o600)
        reload_system()
    except BaseException:
        for path, prior in snapshots.items():
            if prior is None:
                path.unlink(missing_ok=True)
            else:
                write_file(path, *prior)
        if previous:
            write_file(MANIFEST, previous_bytes, 0o600)
        else:
            MANIFEST.unlink(missing_ok=True)
        try:
            reload_system()
        except (OSError, subprocess.SubprocessError) as recovery_error:
            print("Restored installation files; reloading restored configuration failed: "
                  + str(recovery_error), file=sys.stderr)
        raise


def user_locations():
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return {"extension": data / "gnome-shell/extensions" / UUID,
            "nautilus": data / "nautilus-python/extensions/wallpaper_sync.py",
            "dbus": data / "dbus-1/services/io.github.mouge.WallpaperSync.service",
            "unit": config / "systemd/user/wallpaper-sync.service",
            "manifest": data / "wallpaper-sync/user-install.json"}


def preflight_user():
    paths = user_locations()
    previous = json.loads(paths["manifest"].read_text()) if paths["manifest"].exists() else {}
    for key in ["extension", "nautilus", "dbus", "unit"]:
        destination = paths[key]
        if destination.exists() and previous.get(key) != str(destination):
            raise RuntimeError(f"Refusing to overwrite an untracked user file: {destination}")
        if destination.is_symlink():
            raise RuntimeError(f"Installation target is a symlink: {destination}")
    return paths


def configure_user_grub(config, grub_config, explicit=False, legacy_default=False):
    """Preserve a saved preference and the legacy release's unstored true default."""
    if explicit or (legacy_default and config.get_user_value("grub-enabled") is None):
        config.set_boolean("grub-enabled", parse_grub_config(grub_config)["enabled"])


def install_user(grub_config=None, set_grub_preference=False, legacy_grub_default=False):
    paths = preflight_user()
    nautilus_source = (PROJECT / "nautilus/wallpaper_sync.py").read_bytes()
    nautilus_changed = not paths["nautilus"].exists() or paths["nautilus"].read_bytes() != nautilus_source
    destination = paths["extension"]
    destination.mkdir(parents=True, exist_ok=True)
    for source in (PROJECT / "extension").rglob("*"):
        if source.is_file() and source.name != "gschemas.compiled":
            target = destination / source.relative_to(PROJECT / "extension")
            write_file(target, source.read_bytes(), 0o644)
    command(["/usr/bin/glib-compile-schemas", "--strict", str(destination / "schemas")])
    for key, source in [("nautilus", "nautilus/wallpaper_sync.py"),
                        ("dbus", "dbus/io.github.mouge.WallpaperSync.service"),
                        ("unit", "systemd/wallpaper-sync.service")]:
        write_file(paths[key], (PROJECT / source).read_bytes(), 0o644)
    write_file(paths["manifest"], json.dumps({k: str(v) for k, v in paths.items()}).encode(), 0o600)
    command(["systemctl", "--user", "daemon-reload"])
    command(["busctl", "--user", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
             "org.freedesktop.DBus", "ReloadConfig"])
    if set_grub_preference or legacy_grub_default:
        from wallpaper_sync.common import settings
        from gi.repository import Gio
        configure_user_grub(settings(), grub_config, set_grub_preference, legacy_grub_default)
        Gio.Settings.sync()
    activation = subprocess.run(["gnome-extensions", "enable", UUID], capture_output=True, text=True)
    if activation.returncode:
        # A newly copied extension is discovered on the next Shell startup.
        # Queue just our UUID without changing other extensions' preferences.
        from gi.repository import Gio
        shell_settings = Gio.Settings.new("org.gnome.shell")
        enabled = shell_settings.get_strv("enabled-extensions")
        disabled = shell_settings.get_strv("disabled-extensions")
        if UUID not in enabled:
            shell_settings.set_strv("enabled-extensions", [*enabled, UUID])
        if UUID in disabled:
            shell_settings.set_strv("disabled-extensions", [item for item in disabled if item != UUID])
        Gio.Settings.sync()
        print("GNOME will discover and enable Wallpaper Sync at your next logout/login.")
    else:
        print("GNOME extension enabled. The next wallpaper change will request synchronization.")
    if nautilus_changed:
        print("Nautilus must restart once to load its new Python extension.")
    if nautilus_changed and not ASSUME_YES and confirm("Close Nautilus windows now so it can load Wallpaper Sync?"):
        command(["nautilus", "-q"])
    print("Installation complete. Services activate on demand; GDM was not restarted.")


def uninstall_system(uid):
    if os.geteuid() != 0:
        raise RuntimeError("Root is required to remove system files.")
    record = json.loads(MANIFEST.read_text())
    if uid != record["uid"]:
        raise RuntimeError("The requesting user does not match this installation.")
    allowed = set(map(str, system_files(pwd.getpwuid(uid).pw_name))) | {str(OWNER)}
    if not set(record["files"]).issubset(allowed):
        raise RuntimeError("Installation manifest contains unexpected paths.")
    command(["/usr/bin/systemctl", "stop", "wallpaper-sync-helper.service"])
    for filename in record["files"]:
        path = Path(filename)
        ensure_root_path(path)
        path.unlink(missing_ok=True)
    MANIFEST.unlink()
    for directory in [LIB / "wallpaper_sync", LIB / "licenses", LIB / "docs", LIB, OWNER.parent]:
        if directory.exists():
            # Python bytecode is created by our root-owned installation only.
            bytecode = directory / "__pycache__"
            if bytecode.is_dir() and not bytecode.is_symlink():
                shutil.rmtree(bytecode)
            try:
                directory.rmdir()
            except OSError:
                print(f"Keeping nonempty directory: {directory}")
    reload_system()
    print(f"Recovery backups retained in {STATE}; existing upstream backups remain untouched.")


def uninstall():
    paths = user_locations()
    if not paths["manifest"].exists():
        raise RuntimeError("No user installation manifest was found.")
    record = json.loads(paths["manifest"].read_text())
    if confirm("Restore Fedora's original GDM resource before uninstalling?", True):
        command(["/usr/local/bin/wallpaper-sync", "restore"])
    elif not confirm("Keep the current GDM wallpaper and retain recovery backups?"):
        return
    if not confirm("Remove the Wallpaper Sync files shown in the installation plan?"):
        return
    subprocess.run(["gnome-extensions", "disable", UUID], capture_output=True)
    subprocess.run(["systemctl", "--user", "stop", "wallpaper-sync.service"], capture_output=True)
    command(["sudo", "/usr/bin/python3", "-I", str(Path(__file__).resolve()), "--system-uninstall", "--uid", str(os.getuid())])
    for key in ["extension", "nautilus", "dbus", "unit"]:
        path = paths[key]
        if record.get(key) != str(path) or path.is_symlink():
            raise RuntimeError(f"Refusing to remove an unexpected user path: {path}")
        if key == "extension":
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
            if key == "nautilus":
                for bytecode in (path.parent / "__pycache__").glob("wallpaper_sync.*.pyc"):
                    bytecode.unlink()
    paths["manifest"].unlink()
    paths["manifest"].parent.rmdir()
    command(["systemctl", "--user", "daemon-reload"])
    command(["busctl", "--user", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
             "org.freedesktop.DBus", "ReloadConfig"])
    print("Wallpaper Sync removed. User wallpaper images and desktop wallpaper settings were preserved.")
    print("User cache/status and root recovery backups remain available for inspection.")
    if not ASSUME_YES and confirm("Restart Nautilus now to unload its removed extension?"):
        command(["nautilus", "-q"])


def plan(grub_config=None):
    grub_config = parse_grub_config(grub_config)
    account = pwd.getpwuid(os.getuid())
    print("System files to install (root-owned):")
    for path in sorted([*system_files(account.pw_name, grub_config), OWNER]):
        print("  " + str(path))
    print("State: /var/lib/wallpaper-sync/; runtime: /run/wallpaper-sync/")
    print("Synchronization targets: " + str(THEME))
    if grub_config["enabled"]:
        print(f'  /boot/grub2/themes/{grub_config["theme"]}/background.jpg '
              f'({grub_config["width"]}x{grub_config["height"]}; one background.previous.jpg backup)')
    else:
        print("GRUB: disabled; no boot files are read or changed by synchronization.")
    print("Atomic replacement creates temporary .wallpaper-sync-* files beside that resource.")
    print("User paths:")
    for path in user_locations().values():
        print("  " + str(path))


def main():
    global ASSUME_YES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="Show the file plan without installing")
    parser.add_argument("--yes", action="store_true", help="Skip install confirmations; never restart Nautilus")
    parser.add_argument("--install-deps", action="store_true", help="Authorize installation of missing Fedora dependencies")
    parser.add_argument("--grub-theme", help="Existing GRUB theme directory name; requires --grub-size")
    parser.add_argument("--grub-size", help="Output WIDTHxHEIGHT; requires --grub-theme")
    parser.add_argument("--without-grub", action="store_true", help="Disable privileged GRUB support on install or upgrade")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--system-install", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--system-uninstall", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--uid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    ASSUME_YES = args.yes
    try:
        requested = grub_request(args)
        if args.system_install:
            install_system(args.uid, requested)
            return
        if args.system_uninstall:
            uninstall_system(args.uid)
            return
        if os.geteuid() == 0:
            raise RuntimeError("Run install.sh as your normal desktop user; it elevates only its system phase.")
        grub_config = selected_grub_config(requested)
        plan(grub_config)
        if args.plan:
            return
        if args.uninstall:
            uninstall()
            return
        preflight_user()
        os_release = Path("/etc/os-release").read_text()
        if 'ID=fedora\n' not in os_release or 'VERSION_ID=44\n' not in os_release:
            raise RuntimeError("This release targets Fedora 44; installation on other releases is not validated.")
        shell = command(["gnome-shell", "--version"], capture_output=True, text=True).stdout
        if not shell.startswith("GNOME Shell 50."):
            raise RuntimeError("This release requires GNOME Shell 50.")
        missing = [package for package in PACKAGES if subprocess.run(
            ["rpm", "-q", package], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode]
        if missing:
            print("Missing packages: " + ", ".join(missing))
            if not args.install_deps:
                if args.yes or not sys.stdin.isatty():
                    raise RuntimeError("Missing dependencies; install them first or pass --install-deps explicitly.")
                if not confirm("Install only these missing Fedora packages?"):
                    return
            command(["sudo", "dnf", "install", "-y", *missing])
        else:
            print("All required Fedora packages are already installed.")
        command(["glib-compile-schemas", "--strict", str(PROJECT / "extension/schemas")])
        command(["/usr/bin/python3", "-m", "unittest", "discover", "-s", str(PROJECT / "tests"), "-q"], cwd=PROJECT)
        if not confirm("Install the listed system and user files, and enable Wallpaper Sync?"):
            return
        fresh = not user_locations()["manifest"].exists()
        legacy_default = (requested is None and grub_config["enabled"] and OWNER.is_file()
                          and "grub" not in json.loads(OWNER.read_text()))
        if not fresh:
            command(["systemctl", "--user", "stop", "wallpaper-sync.service"])
        command(["sudo", "/usr/bin/python3", "-I", str(Path(__file__).resolve()),
                 "--system-install", "--uid", str(os.getuid()), *grub_arguments(grub_config)])
        install_user(grub_config, set_grub_preference=fresh or requested is not None,
                     legacy_grub_default=legacy_default)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"Wallpaper Sync installer: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

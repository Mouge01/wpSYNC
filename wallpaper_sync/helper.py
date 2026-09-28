"""Privileged D-Bus endpoint. No user paths, commands, CSS or manifests accepted."""

import fcntl
import json
import os
from pathlib import Path
import stat
import struct
import threading
import time

import gi
gi.require_version("Polkit", "1.0")
from gi.repository import Gio, GLib, Polkit

from .backend import Backend
from .grub import GrubBackend
from .common import (HELPER_BUS, HELPER_OBJECT, HELPER_XML, MAX_BYTES, MAX_PIXELS,
                     MAX_SIDE, POLKIT_PREFIX, load_json, log)
from .resource import digest


def inspect_image(data):
    # Parse bounded headers only; image decoders never run in this root service.
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        extension = "png"
    elif data.startswith(b"\xff\xd8"):
        position, width, height = 2, 0, 0
        while position < min(len(data), 1024 * 1024):
            if data[position] != 0xff:
                raise ValueError("Malformed JPEG header.")
            while position < len(data) and data[position] == 0xff:
                position += 1
            if position >= len(data):
                break
            marker = data[position]
            position += 1
            if marker in {0xd9, 0xda}:
                break
            if marker in {0x01, *range(0xd0, 0xd8)}:
                continue
            if position + 2 > len(data):
                break
            length = int.from_bytes(data[position:position + 2], "big")
            if length < 2 or position + length > len(data):
                raise ValueError("Malformed JPEG segment.")
            if marker in {0xc0, 0xc1, 0xc2} and length >= 8:
                height, width = struct.unpack(">HH", data[position + 3:position + 7])
                break
            position += length
        extension = "jpg"
    else:
        raise ValueError("Helper accepts PNG or JPEG data only.")
    if min(width, height) < 1 or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
        raise ValueError("Unsupported image dimensions or JPEG encoding.")
    return extension


def read_sealed(descriptor, uid):
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or not 0 < info.st_size <= MAX_BYTES:
        raise ValueError("Image descriptor has invalid ownership, type or size.")
    required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    if fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & required != required:
        raise ValueError("Image descriptor must be an immutable sealed memfd.")
    data = os.pread(descriptor, info.st_size, 0)
    if len(data) != info.st_size:
        raise ValueError("Incomplete image data.")
    return data


class Helper:
    def __init__(self, connection, owner):
        self.connection = connection
        self.owner = owner
        self.backend = Backend(initial_hash=owner["initial_theme_sha256"])
        self.grub = GrubBackend(uid=owner["uid"], config=owner.get("grub"))
        self.lock = threading.Lock()
        self.last_request = {}
        self.last_activity = time.monotonic()
        self.loop = GLib.MainLoop()
        node = Gio.DBusNodeInfo.new_for_xml(HELPER_XML)
        self.registration = connection.register_object(HELPER_OBJECT, node.interfaces[0], self.call, None, None)

    def caller_uid(self, sender):
        result = self.connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixUser", GLib.Variant("(s)", (sender,)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
        uid = result.unpack()[0]
        if uid != self.owner["uid"]:
            raise PermissionError("This account is not authorized for Wallpaper Sync.")
        return uid

    def active_session(self, sender):
        uid = self.caller_uid(sender)
        sessions = self.connection.call_sync("org.freedesktop.login1", "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager", "ListSessions", None, None,
            Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        for _session, session_uid, _user, seat, path in sessions:
            if session_uid != uid or seat != "seat0":
                continue
            properties = self.connection.call_sync("org.freedesktop.login1", path,
                "org.freedesktop.DBus.Properties", "GetAll",
                GLib.Variant("(s)", ("org.freedesktop.login1.Session",)), None,
                Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
            if (properties.get("Active") and not properties.get("Remote")
                    and properties.get("Type") in {"wayland", "x11"}
                    and properties.get("Class") == "user"):
                return uid
        raise PermissionError("An active local graphical session on seat0 is required.")

    def authorize(self, sender, operation):
        self.active_session(sender)
        subject = Polkit.SystemBusName.new(sender)
        result = Polkit.Authority.get_sync(None).check_authorization_sync(
            subject, POLKIT_PREFIX + "." + operation, None,
            Polkit.CheckAuthorizationFlags.NONE, None)
        if not result.get_is_authorized():
            raise PermissionError("Polkit denied the Wallpaper Sync operation.")

    def call(self, connection, sender, path, interface, method, parameters, invocation):
        self.last_activity = time.monotonic()
        if not self.lock.acquire(blocking=False):
            invocation.return_dbus_error(HELPER_BUS + ".Busy", "Another helper request is running.")
            return
        # Obtain a private descriptor while the incoming D-Bus message is alive.
        descriptor = None
        try:
            if method in {"Apply", "ApplyGrub"}:
                index, raw_metadata = parameters.unpack()
                descriptors = invocation.get_message().get_unix_fd_list()
                if descriptors is None or descriptors.get_length() != 1 or index != 0:
                    raise ValueError("Apply requires exactly one image descriptor.")
                descriptor = descriptors.get(index)
            else:
                raw_metadata = None
        except Exception as error:
            self.lock.release()
            invocation.return_dbus_error(HELPER_BUS + ".Invalid", str(error))
            return

        def execute():
            try:
                uid = self.caller_uid(sender)
                if method == "GetStatus":
                    try:
                        result = self.backend.status()
                    except Exception as error:
                        result = {"error": str(error)}
                    try:
                        result["grub"] = self.grub.status()
                    except Exception as error:
                        result["grub"] = {"enabled": self.grub.enabled, "error": str(error)}
                    result["polkit_rule_installed"] = Path("/etc/polkit-1/rules.d/49-wallpaper-sync.rules").is_file()
                    result["authorized_uid"] = self.owner["uid"]
                elif method in {"Apply", "Restore", "ApplyGrub", "RestoreGrub"}:
                    applying = method in {"Apply", "ApplyGrub"}
                    is_grub = method in {"ApplyGrub", "RestoreGrub"}
                    operation = "apply" if applying else "restore"
                    backend = self.grub if is_grub else self.backend
                    backend_name = "grub" if is_grub else "gdm"
                    self.authorize(sender, operation)
                    if time.monotonic() - self.last_request.get(backend_name, 0) < 2:
                        raise RuntimeError("Please wait two seconds between updates to this backend.")
                    self.last_request[backend_name] = time.monotonic()
                    if applying:
                        if len(raw_metadata) > 8192:
                            raise ValueError("Metadata exceeds size limit.")
                        metadata = json.loads(raw_metadata)
                        if not isinstance(metadata, dict):
                            raise ValueError("Metadata must be an object.")
                        source = metadata.get("source_uri")
                        if not isinstance(source, str) or len(source) > 4096:
                            raise ValueError("Invalid source metadata.")
                        data = read_sealed(descriptor, uid)
                        safe = {"source_uri": source, "image_sha256": digest(data)}
                        if not is_grub:
                            blur = metadata.get("blur")
                            if type(blur) is not int or not 0 <= blur <= 30:
                                raise ValueError("Invalid blur metadata.")
                            safe.update({"blur": blur, "format": inspect_image(data)})
                        log(backend_name + "_update_attempted", uid=uid, source=source)
                        result = backend.apply(data, safe, lambda: self.authorize(sender, operation))
                    else:
                        result = backend.restore(lambda: self.authorize(sender, operation))
                    log(backend_name + ("_rollback_completed" if not applying else "_update_completed"),
                        uid=uid, sha256=result.get("installed_sha256"))
                else:
                    raise ValueError("Unknown operation.")
                GLib.idle_add(lambda: (invocation.return_value(GLib.Variant("(s)", (json.dumps(result),))), False)[1])
            except Exception as error:
                message = str(error)
                log("helper_error", operation=method, error=message)
                GLib.idle_add(lambda: (invocation.return_dbus_error(HELPER_BUS + ".Failed", message), False)[1])
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                self.last_activity = time.monotonic()
                self.lock.release()
        threading.Thread(target=execute, daemon=True).start()

    def idle(self):
        if not self.lock.locked() and time.monotonic() - self.last_activity > 60:
            self.loop.quit()
            return False
        return True


def main():
    if os.geteuid() != 0:
        raise SystemExit("The helper must be started by its system D-Bus service.")
    config = Path("/etc/wallpaper-sync/owner.json")
    info = config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit("Unsafe helper owner configuration.")
    owner = load_json(config)
    if type(owner.get("uid")) is not int or owner["uid"] < 1000:
        raise SystemExit("Invalid authorized user.")
    connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    helper = Helper(connection, owner)
    name = Gio.bus_own_name_on_connection(connection, HELPER_BUS, Gio.BusNameOwnerFlags.NONE,
        None, lambda *_: helper.loop.quit())
    GLib.timeout_add_seconds(15, helper.idle)
    try:
        helper.loop.run()
    finally:
        Gio.bus_unown_name(name)


if __name__ == "__main__":
    main()

"""D-Bus clients used only by unprivileged processes."""

import json
import os

from gi.repository import Gio, GLib

from .common import BUS, OBJECT, HELPER_BUS, HELPER_OBJECT
from .images import sealed_image


def user_call(method, arguments=None):
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    result = connection.call_sync(BUS, OBJECT, BUS, method, arguments,
        GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 10000, None)
    return json.loads(result.unpack()[0])


def helper_call(method, data=None, metadata=None):
    connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    if method not in {"Apply", "ApplyGrub"}:
        result = connection.call_sync(HELPER_BUS, HELPER_OBJECT, HELPER_BUS, method, None,
            GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 120000, None)
    else:
        descriptor = sealed_image(data)
        try:
            descriptors = Gio.UnixFDList.new()
            index = descriptors.append(descriptor)
            result, _fds = connection.call_with_unix_fd_list_sync(
                HELPER_BUS, HELPER_OBJECT, HELPER_BUS, method,
                GLib.Variant("(hs)", (index, json.dumps(metadata))), GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE, 120000, descriptors, None)
        finally:
            os.close(descriptor)
    return json.loads(result.unpack()[0])

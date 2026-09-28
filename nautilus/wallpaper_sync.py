# SPDX-License-Identifier: MIT
"""Nautilus API 4.1: change desktop settings; never call the GDM helper."""
import gi
gi.require_version("Nautilus", "4.1")
from gi.repository import Gio, GObject, Nautilus


class WallpaperSyncMenu(GObject.GObject, Nautilus.MenuProvider):
    MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/avif", "image/bmp", "image/tiff", "image/jxl"}

    def get_file_items(self, files):
        if len(files) != 1:
            return []
        selected = files[0]
        if selected.is_directory() or selected.get_mime_type() not in self.MIME_TYPES:
            return []
        location = selected.get_location()
        if not location.is_native() or location.get_path() is None:
            return []
        item = Nautilus.MenuItem(name="WallpaperSync::SetBackground", label="Set as Background")
        item.connect("activate", self.set_background, location)
        return [item]

    def set_background(self, item, location):
        try:
            info = location.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
            if info.get_file_type() != Gio.FileType.REGULAR:
                return
            settings = Gio.Settings.new("org.gnome.desktop.background")
            if not all(settings.is_writable(key) for key in ("picture-uri", "picture-uri-dark")):
                raise RuntimeError("Desktop wallpaper settings are locked by system policy.")
            settings.delay()
            for key in ("picture-uri", "picture-uri-dark"):
                settings.set_string(key, location.get_uri())
            settings.apply()
            Gio.Settings.sync()
        except Exception as error:
            print(f"Wallpaper Sync Nautilus: {error}")

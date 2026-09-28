# Third-party notices

Wallpaper Sync is MIT licensed. The resource extraction/rebuild approach and
GDM CSS rules in `wallpaper_sync/resource.py` derive from
[kem-a/gnome-gdm-wallpaper](https://github.com/kem-a/gnome-gdm-wallpaper),
commit `ec9fd78308247d951ca731f7520658b4e6777c6b`.

The upstream script's notice is retained in the derived module:

    Copyright (c) 2023-2026 Arnis Kemlers
    License: MIT

The upstream LICENSE has a separate, unnamed notice `Copyright (c) 2018 `.
Its complete text is preserved without alteration in
[licenses/gnome-gdm-wallpaper-MIT.txt](licenses/gnome-gdm-wallpaper-MIT.txt).
Both notices remain applicable; the missing name in the upstream license has
not been filled in or replaced.

Wallpaper Sync's transaction handling, input validation, D-Bus services,
extension, preferences, Nautilus integration, CLI, installer and tests are new
code. The application does not invoke or install the downloaded upstream script.

System dependencies (GNOME, PyGObject, ImageMagick, Polkit and systemd) retain
their own licenses and are used from Fedora packages, not vendored here.
The test image `tests/fixtures/blue.avif` is an original synthetic solid-color
image generated for this project and is covered by the project's MIT license.

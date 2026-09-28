"""GDM resource rebuilding derived from kem-a/gnome-gdm-wallpaper.

Copyright (c) 2023-2026 Arnis Kemlers
License: MIT; see licenses/gnome-gdm-wallpaper-MIT.txt.

Derived portions: extraction/recompilation, embedded wallpaper and CSS rules.
Validation, baseline handling and transactions are original Wallpaper Sync code.
"""

import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import xml.etree.ElementTree as ET

from gi.repository import Gio

PREFIX = "/org/gnome/shell/theme/"
CSS_NAMES = ("gnome-shell.css", "gnome-shell-dark.css", "gnome-shell-light.css",
             "gnome-shell-high-contrast.css")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def entries(path):
    resource = Gio.Resource.load(str(path))
    result = {}
    def walk(prefix):
        for name in resource.enumerate_children(prefix, Gio.ResourceLookupFlags.NONE):
            full = prefix + name
            if name.endswith("/"):
                walk(full)
            else:
                relative = PurePosixPath(full.lstrip("/"))
                if ".." in relative.parts or "\\" in full:
                    raise ValueError("Unsafe path in theme resource.")
                result[full] = resource.lookup_data(full, Gio.ResourceLookupFlags.NONE).get_data()
    walk("/")
    return result


def build(baseline, image, extension, directory):
    if extension not in {"png", "jpg"}:
        raise ValueError("Invalid embedded image format.")
    original = entries(baseline)
    payload = dict(original)
    target = f"wallpaper-sync.{extension}"
    payload[PREFIX + target] = image
    patch = ("\n/* Wallpaper Sync: generated from a verified Fedora baseline. */\n"
             "#lockDialogGroup {\n"
             f'  background: url("{target}");\n'
             "  background-position: center;\n  background-repeat: no-repeat;\n"
             "  background-size: cover;\n}\n.login-dialog { background-color: transparent; }\n")
    changed = []
    for filename in CSS_NAMES:
        key = PREFIX + filename
        if key in payload:
            payload[key] = payload[key] + patch.encode()
            changed.append(key)
    if not changed:
        raise RuntimeError("GNOME theme layout is unsupported: no recognized shell CSS.")
    root = Path(directory)
    tree = ET.Element("gresources")
    node = ET.SubElement(tree, "gresource", {"prefix": "/"})
    # Aliases preserve every original resource path, including resources outside
    # the theme prefix. Physical filenames are generated, never caller-supplied.
    for index, (name, value) in enumerate(sorted(payload.items())):
        filename = f"entry-{index}"
        (root / filename).write_bytes(value)
        ET.SubElement(node, "file", {"alias": name.lstrip("/")}).text = filename
    manifest = root / "theme.xml"
    ET.ElementTree(tree).write(manifest, encoding="utf-8", xml_declaration=True)
    output = root / "theme.gresource"
    process = subprocess.run(["/usr/bin/glib-compile-resources", str(manifest),
                              "--sourcedir=" + str(root), "--target=" + str(output)],
                             capture_output=True, text=True, timeout=45,
                             env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    if process.returncode:
        raise RuntimeError("Resource compiler failed: " + process.stderr[-2000:])
    if entries(output) != payload:
        raise RuntimeError("Rebuilt resource did not preserve the expected contents.")
    return output.read_bytes()

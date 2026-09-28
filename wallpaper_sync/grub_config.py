"""Validate administrator-owned optional GRUB configuration.

Only a theme directory name and a bounded output size are configurable. The
privileged helper never accepts an arbitrary destination path from a client.
"""

import re


MIN_OUTPUT_SIDE = 64
MAX_OUTPUT_SIDE = 8192
MAX_OUTPUT_PIXELS = 3840 * 2160


def validate_grub_size(size):
    """Return a validated (width, height) pair without coercing input values."""
    if not isinstance(size, (tuple, list)) or len(size) != 2:
        raise ValueError("GRUB resolution requires a width and height.")
    width, height = size
    if any(type(value) is not int for value in size):
        raise ValueError("GRUB width and height must be integers.")
    if (not all(MIN_OUTPUT_SIDE <= value <= MAX_OUTPUT_SIDE for value in size)
            or width * height > MAX_OUTPUT_PIXELS):
        raise ValueError("GRUB resolution must have sides between 64 and 8192 pixels and at most 8,294,400 pixels.")
    return width, height


def parse_grub_config(value):
    """Normalize configuration, defaulting to disabled for existing installs."""
    if value is None:
        return {"enabled": False}
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ValueError("GRUB configuration requires an explicit boolean enabled value.")
    if not value["enabled"]:
        if set(value) != {"enabled"}:
            raise ValueError("Disabled GRUB configuration must contain only enabled.")
        return {"enabled": False}
    if set(value) != {"enabled", "theme", "width", "height"}:
        raise ValueError("Enabled GRUB configuration requires only enabled, theme, width and height.")
    theme = value["theme"]
    if not isinstance(theme, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", theme) is None:
        raise ValueError("GRUB theme must be a simple directory name of 1–64 letters, digits, underscores or hyphens.")
    width, height = validate_grub_size((value["width"], value["height"]))
    return {"enabled": True, "theme": theme, "width": width, "height": height}

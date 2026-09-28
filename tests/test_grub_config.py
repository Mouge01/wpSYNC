"""Public GRUB configuration parsing needs neither root nor desktop libraries."""

import unittest

from wallpaper_sync.grub_config import parse_grub_config, validate_grub_size


class GrubConfigTests(unittest.TestCase):
    def config(self, **overrides):
        return {"enabled": True, "theme": "example-theme_2", "width": 1920, "height": 1080, **overrides}

    def test_missing_config_defaults_to_disabled(self):
        self.assertEqual(parse_grub_config(None), {"enabled": False})
        self.assertEqual(parse_grub_config({"enabled": False}), {"enabled": False})

    def test_enabled_config_is_copied_and_normalized(self):
        config = self.config()
        result = parse_grub_config(config)
        self.assertEqual(result, config)
        self.assertIsNot(result, config)

    def test_enabled_config_requires_explicit_theme_and_resolution(self):
        for key in ["theme", "width", "height"]:
            config = self.config()
            del config[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_grub_config(config)

    def test_boolean_and_schema_are_strict(self):
        for config in [True, [], {}, {"enabled": 1}, {"enabled": "true"},
                       {"enabled": False, "theme": "example"},
                       self.config(target="/etc/passwd")]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                parse_grub_config(config)

    def test_theme_is_a_directory_name_never_a_path_or_shell_expression(self):
        for theme in ["", ".", "..", "../example", "/boot/grub2/themes/example", "a/b", "a\\b",
                      " a", "a ", "a\n", "a.jpg", "-a", "_a", "ä", "$(id)", "a;id", "a" * 65, None]:
            with self.subTest(theme=theme), self.assertRaises(ValueError):
                parse_grub_config(self.config(theme=theme))
        for theme in ["A", "0", "a-b_c", "z" * 64]:
            self.assertEqual(parse_grub_config(self.config(theme=theme))["theme"], theme)

    def test_dimensions_reject_coercion_and_oversized_allocations(self):
        for size in [(True, 1080), (1920, False), (1920.0, 1080), ("1920", 1080),
                     (63, 1080), (1920, 63), (8193, 64), (8192, 8192),
                     (3840, 2161), (0, 0), (-1920, 1080), None, (), (1,), (1, 2, 3), "1920x1080"]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                validate_grub_size(size)
        for size in [(64, 64), (1920, 1080), (2880, 1800), (3840, 2160), (8192, 64)]:
            self.assertEqual(validate_grub_size(list(size)), size)


if __name__ == "__main__":
    unittest.main()

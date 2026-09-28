# Contributing to wpSYNC

The first release focuses on Fedora 44, GNOME 50 and Wayland, with optional
wallpaper updates for an existing GRUB theme. Keep changes small and explain the
user-visible behavior they change.

## Development checks

Use a Fedora 44 development environment with the dependencies listed by the
installer, plus Node.js for JavaScript syntax checks. From the source root:

```sh
./install.sh --plan
make check
make dist
```

Local tests use temporary resources, generated images and memory-only settings.
They must not install services, modify the live GDM resource or GRUB files,
change desktop wallpaper, or trigger logout/reboot. See the
[acceptance plan](docs/TEST_PLAN.md) for the separate installed checks.

Add focused regression coverage for image validation, configuration parsing,
privileged paths, authorization, caching or transaction changes. Do not weaken
checks merely to make a synthetic fixture pass. Run only relevant local checks
while developing, then run the complete `make check` before submitting.

## Project layout

| Directory | Responsibility |
|---|---|
| `extension/` | GNOME settings observer and preferences |
| `nautilus/` | Files menu integration |
| `wallpaper_sync/` | User worker, image preparation, CLI and privileged backends |
| `cli/`, `helper/` | Installed entry points |
| `dbus/`, `systemd/`, `polkit/` | Activation and authorization |
| `tools/` | Installer and development/distribution tools |
| `tests/` | Local regression coverage and synthetic fixtures |
| `docs/` | Installation, security, recovery and acceptance guides |

The source distribution uses the explicit allowlist in
`tools/release_manifest.txt`. Add intended new source/documentation files there
so they are included in release archives.

wpSYNC is the public product name. The `wallpaper-sync` executable, extension
UUID, settings schema, D-Bus interfaces and Polkit IDs are stable compatibility
identifiers. Changing them requires an explicit migration design.

## Boundaries to preserve

- Root never receives an arbitrary source or destination path through D-Bus.
- Source image decoding and conversion stay outside root. Independent GRUB
  decoding runs as the validator account, with no fallback to a root decoder.
- GRUB configuration is root-controlled and restricted to an existing theme
  under the supported theme directory.
- No bootloader/theme installation, boot configuration generation, session
  termination, or machine reboot is part of synchronization.
- Resource/image replacement is validated and atomic; failure paths preserve
  recoverability and report uncertainty honestly.
- Diagnostics remain local. Public fixtures and examples must be synthetic or
  licensed for redistribution and must not contain workstation paths or logs.

Review the [security model](docs/SECURITY.md) before changing the helper. Report
sensitive findings through the process in [SECURITY.md](SECURITY.md).

For a pull request, describe the problem, resulting behavior and checks actually
run. State which installed or visual checks remain unperformed. Keep license and
upstream attribution notices intact.

# wpSYNC

Keep your **GNOME desktop and GDM login wallpaper** in sync, with optional
**GRUB wallpaper** support. wpSYNC's first release targets **Fedora 44,
GNOME 50 and Wayland**.

Choose a wallpaper in GNOME Settings or use **Set as Background** in Files.
wpSYNC follows light/dark wallpaper changes, applies adjustable blur to the
login image, and can create an unblurred JPEG for an existing GRUB theme.
Your source images remain unchanged.

## Install from source

Download and extract the source archive, or clone this repository. Open a
terminal in its root directory and run these commands as your normal desktop
user:

```sh
./install.sh --plan
./install.sh
```

A new installation enables GDM synchronization; GRUB is optional. The installer
checks the supported Fedora/GNOME versions, reports required packages, tests the
source, and shows the files it will install. It uses `sudo` for system files.
Read the [installation guide](docs/INSTALLATION.md) for dependencies, updates and
uninstallation.

To add GRUB support for an **already installed** Tela theme:

```sh
./install.sh --plan --grub-theme tela --grub-size 2880x1800
./install.sh --grub-theme tela --grub-size 2880x1800
```

The theme must already use `/boot/grub2/themes/tela/background.jpg`.
Use your installed theme's directory name and desired image dimensions.
wpSYNC does not install or select a GRUB theme or regenerate boot configuration.
See the [GRUB guide](docs/GRUB_SYNC.md) for requirements and rollback.

After installation, log out and back in if GNOME has not yet discovered the
extension. Nautilus also needs a restart to load its menu integration. Open
preferences with:

```sh
gnome-extensions prefs wallpaper-sync@mouge
```

## Everyday use

```sh
wallpaper-sync status            # Current source and backend status
wallpaper-sync sync              # Synchronize now and wait for the result
wallpaper-sync doctor            # Detailed local diagnostics
wallpaper-sync set '/path/to/an image.webp'
wallpaper-sync grub-disable      # Pause GRUB synchronization
wallpaper-sync grub-enable       # Resume configured GRUB synchronization
wallpaper-sync restore-grub      # Disable GRUB sync and restore its previous image
wallpaper-sync restore           # Disable sync and restore Fedora's GDM original
```

Preferences provide a master switch, automatic following, active/light/dark
source selection, login blur from 0 to 30 (default 12), conversion, optional
notifications and Sync Now. GRUB synchronization also requires administrator
configuration through the installer. Turning automatic following off leaves
manual sync available; turning the master switch off blocks both.

GDM's already-running greeter can retain its old theme until a new greeter
starts. GRUB's wallpaper appears at the next normal boot. wpSYNC does not restart
GDM, log you out or reboot. GNOME's session lock screen is separate from GDM.
The login and GRUB images use centered cover cropping; your desktop keeps its
own GNOME scaling preference.

## Scope and safety

- **Supported target:** Fedora 44 with GNOME 50 on Wayland, one configured local
  desktop account and seat0. Other distributions, immutable Fedora variants,
  multiple owners and other GNOME versions are not supported by this release.
- **Input:** local JPEG, PNG, WebP, AVIF, JPEG XL, BMP and TIFF images, subject to
  installed decoder support; at most 64 MiB, 16,384 pixels per side and 64
  megapixels. Remote URLs and slideshow schedules are unsupported. TIFF uses
  its first frame.
- **GDM:** rebuilds a verified Fedora theme resource and retains recovery copies.
  Unknown external theme modifications cause a refusal.
- **GRUB:** replaces only the configured theme's `background.jpg`, with one
  `background.previous.jpg` rollback image. Theme layout, fonts and icons stay
  untouched. No boot configuration, EFI, kernel, initramfs or partition changes.
- **Privileges:** image preparation runs as your user. A narrow, Polkit-gated
  system service installs validated bytes. Services activate on demand and exit
  when idle. wpSYNC does not weaken SELinux or grant user write access to `/boot`.

The command remains `wallpaper-sync`, and the extension UUID and D-Bus names
retain their `wallpaper-sync`/`mouge` namespace for upgrade compatibility.
These identifiers do not select a username; the installer records the installing
account as the owner.

## Documentation and development

- [Installation and upgrades](docs/INSTALLATION.md)
- [GRUB setup and rollback](docs/GRUB_SYNC.md)
- [Architecture and security model](docs/SECURITY.md)
- [Troubleshooting and recovery](docs/RECOVERY.md)
- [Verification and acceptance plan](docs/TEST_PLAN.md)
- [Contributing](CONTRIBUTING.md), [security reporting](SECURITY.md),
  [changelog](CHANGELOG.md) and [release publishing](docs/RELEASING.md)

```sh
make check
make dist
```

Checks use temporary resources and memory-only settings. They do not install
services or change your wallpaper. Live GNOME, Polkit, GDM rendering and GRUB
rendering require separate acceptance checks; a passing unit suite is not a
claim that a reboot has been visually verified.

## License and attribution

wpSYNC is [MIT licensed](LICENSE). Its GDM resource/CSS mechanism derives from
[kem-a/gnome-gdm-wallpaper](https://github.com/kem-a/gnome-gdm-wallpaper).
Upstream notices are preserved in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
and [licenses/gnome-gdm-wallpaper-MIT.txt](licenses/gnome-gdm-wallpaper-MIT.txt).

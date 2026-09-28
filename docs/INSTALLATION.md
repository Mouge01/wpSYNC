# Installation and upgrades

## Supported environment

The initial wpSYNC release targets a conventional Fedora 44 Workstation
installation with GNOME Shell 50 and Wayland. Run installation from the account
that will own synchronization, while logged into its local graphical session.
The system-wide GDM wallpaper can have only one configured owner. Immutable
Fedora variants and other distributions are outside the supported scope.

The installer checks RPM dependencies, including GNOME/GDM, Python/PyGObject,
Pillow, Nautilus's Python provider, ImageMagick, GLib development tools, Polkit
and systemd. The source uses Fedora's system Python; a pip virtual environment
is not an installation method. Dependency installation uses Fedora packages.

Download and extract a source release, or clone the repository, then open a
terminal at the source root. Do not invoke the entire installer with `sudo`:

```sh
./install.sh --help
./install.sh --plan
./install.sh
```

The plan lists system/user file destinations before installation. The installer
runs local tests before the system installation phase. It installs an on-demand
user worker and system helper, a GNOME extension, Files integration, CLI and
narrow authorization policy for the installing account.

For automation, `--yes` skips ordinary installation confirmations. It does not
install missing packages unless `--install-deps` is also supplied:

```sh
./install.sh --yes --install-deps
```

`--install-deps` explicitly authorizes installing missing Fedora packages;
`sudo` authentication may still be required. `--yes` does not restart Nautilus.

The first installation enables GDM synchronization and leaves GRUB unconfigured.
To configure an existing supported GRUB theme, follow [GRUB setup](GRUB_SYNC.md).

## Installed files

| Location | Purpose |
|---|---|
| `/usr/local/bin/wallpaper-sync` | Command-line interface |
| `/usr/local/lib/wallpaper-sync/` | Application modules, notices and documentation |
| `/usr/local/libexec/wallpaper-sync-helper` | Privileged helper entry point |
| `/etc/wallpaper-sync/owner.json` | Root-controlled account and GRUB configuration |
| `/etc/systemd/system/wallpaper-sync-helper.service` | System helper sandbox |
| `/etc/polkit-1/rules.d/49-wallpaper-sync.rules` | Narrow authorization for the owner |
| `/usr/share/polkit-1/actions/` and system D-Bus directories | Application policy and activation files |
| `$XDG_DATA_HOME/gnome-shell/extensions/wallpaper-sync@mouge/` | GNOME Shell extension |
| `$XDG_DATA_HOME/nautilus-python/extensions/wallpaper_sync.py` | Files menu provider |
| `$XDG_DATA_HOME/dbus-1/services/` | User worker activation |
| `$XDG_CONFIG_HOME/systemd/user/wallpaper-sync.service` | User worker service |
| `/var/lib/wallpaper-sync/` | Root-only installation manifest, state and recovery images |
| `$XDG_CACHE_HOME/wallpaper-sync/` | Prepared user images |
| `$XDG_STATE_HOME/wallpaper-sync/` | User status |

Unset XDG directories use the normal `~/.local/share`, `~/.config`, `~/.cache`
and `~/.local/state` defaults. `--plan` prints the resolved paths for your account.

## First use

GNOME may discover a new extension only after logout/login. The installer queues
its activation for that next session if immediate activation is unavailable.
Restart Nautilus when convenient so its Python provider can load. Open:

```sh
gnome-extensions prefs wallpaper-sync@mouge
wallpaper-sync status
wallpaper-sync sync
```

Automatic following starts when the extension is active. A new GDM greeter is
needed to visually inspect a changed login image. Plan logout/reboot around your
own work; installation and synchronization do not initiate either.

## Update an installation

Obtain the new source release and run `./install.sh --plan` followed by
`./install.sh` from the new tree as the same account. Tracked files can be updated;
untracked files at an installation destination are refused. User preferences,
source images and recovery state are retained.

Omitting GRUB options preserves a configured installation. To explicitly remove
GRUB configuration during an update:

```sh
./install.sh --plan --without-grub
./install.sh --without-grub
```

This stops further GRUB writes; it does not restore the previous background.
Run `wallpaper-sync restore-grub` before removing GRUB configuration if rollback
is desired. A recognized pre-publication installation that already had the Tela GRUB
backend keeps its Tela/2880×1800 setup when upgraded without GRUB options.
A legacy GDM-only installation remains GDM-only.

After a Fedora GNOME package update, run `wallpaper-sync sync` or change your
wallpaper. The helper identifies the new package's original resource and builds
against that version. There is no package-manager hook; avoid synchronizing
during a package or theme installation.

## Uninstall

To restore the previous GRUB wallpaper, do so while the configured helper is
still available:

```sh
wallpaper-sync restore-grub
./uninstall.sh
```

Skip the first command if GRUB was never configured or you want to keep its
current wallpaper. Uninstallation offers restoration of Fedora's original GDM
resource, disables the extension, stops wpSYNC's services, and removes tracked
application files. It preserves your wallpaper images and desktop wallpaper
settings. Recovery images, cache and status remain intentionally available.
See [recovery](RECOVERY.md) before deleting retained backups.

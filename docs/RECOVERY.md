# Troubleshooting and recovery

## Check the current state

```sh
wallpaper-sync status
wallpaper-sync doctor
gnome-extensions info wallpaper-sync@mouge
journalctl --user -u wallpaper-sync.service
journalctl -u wallpaper-sync-helper.service
```

Diagnostics and journals can contain usernames, local image paths, source URIs
and image hashes. Review and redact that information before attaching output to
a public issue. wpSYNC does not upload these records.

If the extension was just installed and GNOME does not see it, log out and back
in when convenient. For a missing Files menu entry, restart Nautilus so it loads
the Python provider. The master switch must be on for synchronization; automatic
following may be off while Sync Now remains usable.

A changed GDM resource on disk does not refresh an already-loaded greeter. Check
a newly started greeter during a deliberate logout or reboot. GNOME's session
lock screen is a separate component. GRUB can only be visually checked at boot.

## GDM restoration

```sh
wallpaper-sync restore
```

This disables the master synchronization setting before asking the helper to
restore the verified original for the currently installed Fedora GNOME package.
The master switch stays off if restoration fails. It does not restore GRUB.
Re-enable the master switch in preferences when ready to synchronize again.

Root-only recovery state is retained in `/var/lib/wallpaper-sync/`:

| File | Meaning |
|---|---|
| `original-SHA256.gresource` | Fedora original verified against the installed RPM database |
| `preinstall.gresource` and its metadata | Theme before the first wpSYNC installation transaction |
| `previous.gresource` | Resource immediately before the latest transaction |
| `pending.json` | Durable record for an interrupted transaction, when present |
| `state.json` | Applied checksum, source, blur and package metadata |

Filenames alone do not establish that a backup matches the installed package.
Upstream backups are preserved and used as originals only when their contents
match the installed RPM checksum. A new package version needs its own baseline.

After a GNOME package update, run `wallpaper-sync sync`. Unknown external theme
changes cause a refusal rather than an overwrite. Avoid running synchronization
concurrently with package installation; wpSYNC and the package manager do not
share a transaction lock.

If the helper is unavailable, recovery from a text console requires comparing
the candidate backup with the current theme entry in:

```sh
rpm -q --dump gnome-shell
```

Only after verifying that exact match should an administrator restore it to
`/usr/share/gnome-shell/gnome-shell-theme.gresource`, preserving root ownership
and mode 0644 and running `restorecon` on the restored file. Do not copy a backup
from an older GNOME version simply because its filename looks plausible.

## GRUB restoration

```sh
wallpaper-sync restore-grub
```

This disables GRUB synchronization and restores the configured theme's
`background.previous.jpg` to `background.jpg`. GDM and the desktop remain
unchanged. A missing, invalid or unsafe backup is refused. Re-enable the backend
with `wallpaper-sync grub-enable` after resolving the issue.

The previous image is a single rollback slot, not a wallpaper history. Further
successful changes replace that slot. Restore before uninstalling or removing
GRUB configuration if you want the previous background back. See
[GRUB operation](GRUB_SYNC.md) for exact file boundaries and failure behavior.

If a separate theme installer overwrites the background, run
`wallpaper-sync sync` to apply the selected source again. Do not regenerate boot
configuration as a wpSYNC troubleshooting step.

## Removing wpSYNC

Run `./uninstall.sh` from the source tree. It offers GDM restoration and removes
tracked application files. Source images, desktop settings, upstream backups,
root recovery state and user cache/status remain. Keep the recovery copies until
you have verified the desired GDM and GRUB appearance. A fresh installation plan
lists the relevant locations without requiring you to infer them from a username.

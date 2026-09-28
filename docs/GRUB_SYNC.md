# Optional GRUB wallpaper synchronization

GRUB support is opt-in on a new installation. It requires an existing root-owned
static theme under `/boot/grub2/themes/` whose `theme.txt` uses `background.jpg`.
wpSYNC does not install a theme, select one or regenerate GRUB configuration.

## Configure an existing theme

For a theme installed in `/boot/grub2/themes/tela`:

```sh
./install.sh --plan --grub-theme tela --grub-size 2880x1800
./install.sh --grub-theme tela --grub-size 2880x1800
```

Use the actual theme directory name. A theme name is a single directory
component, never an arbitrary path. Width and height must each be between 64 and
8,192 pixels, with no more than 8,294,400 total pixels (3840×2160). The chosen
size controls the derivative image, not the firmware or GRUB display mode.

The installer stores the configuration in root-owned
`/etc/wallpaper-sync/owner.json` and gives the helper sandbox write access to
that theme directory. A desktop setting cannot choose a different output path
or authorize another theme. Both root configuration and the user-facing GRUB
switch must allow synchronization.

An update without GRUB options preserves existing configuration. To turn the
backend off at installation level, use `./install.sh --without-grub`.
Restore the previous wallpaper before removing configuration if you want it
back; disabling alone leaves the live wallpaper in place.

## Processing

The Shell extension observes `picture-uri`, `picture-uri-dark` and
`color-scheme`, plus wpSYNC preferences. It debounces settings changes and calls
the on-demand user worker. The worker coalesces requests and serializes jobs.
The default **active** mode follows GNOME's selected light/dark image; **light**
and **dark** preferences explicitly select a source for both GDM and GRUB.

The user worker prepares GRUB images independently of GDM blur:

1. Apply EXIF orientation and convert to sRGB.
2. Resize to cover the configured dimensions and crop at the center.
3. Flatten transparency over black, use 8-bit color and strip metadata.
4. Write a quality-90, non-progressive baseline RGB JPEG.
5. Verify dimensions, JPEG framing and full pixel decoding.

The derivative is cached under `$XDG_CACHE_HOME/wallpaper-sync/`. Cache identity
includes source content and processing configuration. The helper compares the
actual installed bytes to avoid unnecessary rewrites and to detect a wallpaper
that a separate theme installer has replaced.

## Files and authorization

For the Tela example:

| Path | Purpose |
|---|---|
| `/boot/grub2/themes/tela/background.jpg` | Live wallpaper |
| `/boot/grub2/themes/tela/background.previous.jpg` | One previous validated image |
| `/var/lib/wallpaper-sync/grub.json` | Backend state |
| `/var/lib/wallpaper-sync/grub.lock` | Transaction lock |

The system helper receives a fully sealed, caller-owned memory descriptor,
never an output path. It validates the incoming JPEG again in a bounded child
process running as `nobody`. Only the configured owner with an active local
graphical session on seat0 can authorize changes. See the
[security model](SECURITY.md) for the account-level boundary.

Incoming images and existing live/backup images are validated before a
transaction. The helper stages files on the destination filesystem, retains the
previous image, flushes the data and atomically replaces the live image. Files
remain root-owned, mode 0644. It refuses symlink destinations and unsafe ownership
or permissions. An identical result skips replacement.

## Controls and rollback

```sh
wallpaper-sync status
wallpaper-sync sync
wallpaper-sync grub-disable
wallpaper-sync grub-enable
wallpaper-sync restore-grub
```

`grub-disable` freezes the current background while GDM remains available.
`grub-enable` resumes a configured backend and requests synchronization when the
master switch is on. It cannot configure a theme. The master switch must also be
on for manual synchronization; automatic following can be off.

`restore-grub` disables GRUB synchronization and restores the one previous
validated image byte for byte. It leaves GRUB synchronization off even if
restoration fails. The backup is retained, not swapped with the live image.
A missing or invalid backup causes a refusal. The backed-up image can have
bounded dimensions different from the configured output size.

GDM and GRUB operations are independent: failure in one does not roll back a
successful change in the other. Input validation failures preserve the existing
live image. A failure after the final atomic replacement can report uncertainty
about state or crash durability even though a valid image is already installed;
inspect status and sync again to reconcile the actual bytes.

## Visibility and limits

GRUB displays the result at the next normal boot. Synchronization does not reboot.
A separate theme installation can replace `background.jpg`; run
`wallpaper-sync sync` afterward. There is no theme-directory watcher or
package-manager hook. Avoid concurrent theme installation and synchronization.

wpSYNC does not modify `theme.txt`, scrims, fonts, icons or selection graphics.
It does not run bootloader generators/installers, change `grub.cfg` or
`/etc/default/grub`, alter EFI variables, kernel entries, initramfs, partitions,
encryption or filesystem configuration.

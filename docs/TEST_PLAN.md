# Verification and acceptance

These are verification requirements, not a record of completed checks. Record
what was actually run, the source version, platform versions and observed
results for each release. A local resource build or checksum change does not
prove that a newly started GDM greeter or GRUB renders correctly.

## Local checks

```sh
make check
make dist
```

Automated tests use temporary destinations, generated GResources, synthetic
images and memory-only GSettings. They do not install services, change the real
desktop wallpaper or write to installed GDM/GRUB targets. Installed integration
and visual checks are a separate stage.

| Area | Expected local evidence |
|---|---|
| Source selection | Active mode follows light/dark style; explicit overrides and empty-dark fallback select correctly |
| Local images | JPEG, PNG, WebP and synthetic AVIF conversion; URI encoding, Unicode, spaces and special characters |
| Resource build | Preserves original resource entries, changes only intended CSS and embeds the prepared image |
| Input bounds | Rejects malformed, missing, oversized, remote and unsupported sources |
| Sealed transfer | Requires a regular caller-owned fully sealed descriptor and bounded metadata |
| GDM baseline | Accepts verified package originals; refuses unknown modifications and mismatched backups |
| Transaction recovery | Handles interrupted transactions and package changes without trusting backup filenames |
| GRUB configuration | Defaults disabled; validates theme components, image dimensions and total pixel bound |
| GRUB normalization | Produces fully decoded 8-bit baseline RGB JPEG at configured dimensions; handles orientation, transparency and aspect ratio |
| Destination safety | Refuses symlinks and untrusted ownership/permissions; validates before replacement |
| GRUB rollback | Retains one previous validated image, restores exact bytes, refuses invalid/missing backup |
| Idempotency | Reuses a valid derivative and skips replacement when installed content matches |
| Independent backends | One backend's failure is reported without undoing a successful other backend |
| Controls | Master off blocks sync; GRUB off leaves GDM available; restore disables relevant synchronization |
| Packaging | Source archive contains required files and notices and excludes local/generated state |

Installer tests should use mocks or temporary destinations. `--plan` is suitable
for inspecting generated destinations; it does not establish that system
installation or authorization succeeds.

## Installed GDM acceptance

Use a supported Fedora 44 / GNOME 50 Wayland session owned by the configured
account. Save the selected wallpaper/preferences, package version and relevant
checksums before testing.

1. Verify installation from a freshly extracted source archive, extension
   activation, Files integration and preferences loading.
2. Verify D-Bus activation and idle exit for the user worker and system helper.
   Check SELinux labels and authorization from the actual systemd user service.
   Confirm a different account and an inactive/nonlocal owner session cannot
   apply or restore changes.
3. Choose JPEG and PNG images, a supported stock WebP, and a synthetic AVIF.
   Include spaces and Unicode in local filenames. Check job results and the
   prepared resource before starting a new greeter.
4. Use distinct light/dark images and change GNOME style. Check active source
   selection and both explicit source overrides.
5. Compare login blur at 0, 12 and another supported value on newly started
   greeters. Confirm desktop wallpaper/scaling remains GNOME-controlled.
6. Disable automatic following and check manual synchronization. Disable the
   master switch and verify the resource remains unchanged on wallpaper events.
7. Restore the Fedora original, compare its checksum to the installed package,
   and verify that synchronization remains disabled. Test package-update recovery
   on a disposable system or controlled temporary copies.
8. Restore the desired final wallpaper/preferences after the fixtures.

Start a new greeter only during a deliberate logout/reboot arranged by the
person using the machine. Do not confuse GNOME's session lock screen with GDM.

## Installed GRUB acceptance

Configure an already installed supported static theme explicitly. Record the
selected theme, output dimensions and checksums before testing. A new GDM-only
installation must neither configure a GRUB target nor write its wallpaper.

| Case | Required evidence |
|---|---|
| Configuration | Plan and installed sandbox refer to the exact chosen theme; unconfigured backend reports disabled |
| Normalization | Decoded image matches configured dimensions, baseline RGB framing and 8-bit depth; blur setting does not affect it |
| Source handling | JPEG, PNG, WebP and AVIF plus URI edge cases work; invalid source leaves live checksum unchanged |
| Atomic apply | Validated image replaces live image; previous validated bytes remain in the single backup |
| Idempotency | Repeated sync leaves matching live checksum/mtime unchanged and does not rotate backup |
| Rapid changes | Final settled output matches the last selected source after queued work completes |
| Independent failures | GDM failure allows GRUB outcome and vice versa; status reports both |
| Controls | GRUB off freezes background; master off blocks both; manual sync works when following is off |
| Rollback | Exact previous bytes restored, GRUB remains off, GDM unchanged; missing/invalid backup refused |
| External replacement | Replacing the wallpaper on a temporary test theme does not fool cache/state into reporting a stale match |
| Upgrade | Public configuration and recognized legacy GRUB setup are preserved; explicit `--without-grub` disables the backend |

Inspect the chosen output with full pixel decoding as well as metadata; an
`identify` dimension check alone does not prove complete decoding. Capture the
source, checksum, dimensions, size, result and relevant logs. Confirm root
ownership, mode 0644 and expected SELinux labels.

Before/after checksums must show the theme layout, scrim, fonts, icons and
selection assets unchanged. Also verify `/boot/grub2/grub.cfg` and
`/etc/default/grub` are unchanged. Do not run a bootloader generator or theme
installer as part of synchronization testing.

A normal boot is the final visual acceptance stage after non-boot checks pass.
Record GRUB's actual displayed result and restore the desired final settings.
Never mark boot rendering as passed based only on the on-disk image.

## Evidence hygiene

Keep local acceptance logs outside the public source distribution. Journals and
status output can contain usernames, image paths and source URIs; redact them
before publishing a bug report. Use synthetic fixtures for reproductions.
Report unperformed checks explicitly, including hosted CI, installation,
greeter rendering and boot rendering.

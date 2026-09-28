# Changelog

## 0.1.0 — initial source release

- Introduces the wpSYNC project and installable source distribution for Fedora
  44 / GNOME 50 on Wayland.
- Follows GNOME light/dark wallpapers into GDM with adjustable blur, Files menu
  integration, preferences and the `wallpaper-sync` CLI.
- Makes GRUB synchronization optional, with a root-controlled existing theme
  and configurable output dimensions. New installations default to GDM only.
- Preserves existing Wallpaper Sync identifiers and installation settings for
  compatible upgrades.
- Uses on-demand user/system services, narrow Polkit authorization, unprivileged
  image preparation, validated atomic replacement and recovery copies.
- Adds installation, recovery, security, contribution and release documentation,
  local checks and source archive tooling.

This changelog describes the source release. It does not assert that a GitHub
repository, release artifact or hosted CI run has been published.

# Security policy

wpSYNC 0.1.x targets Fedora 44 and GNOME 50. Security fixes are developed against
the latest source revision; support for other platforms is not implied.

The [security model](docs/SECURITY.md) explains the image-processing boundary,
local account authorization, root helper sandbox and filesystem transactions.
The helper modifies a GDM resource and an optional configured GRUB wallpaper,
so reports about privilege escalation, arbitrary file access, unsafe decoding,
authorization or interrupted transactions are especially useful.

## Reporting a vulnerability

If this repository has GitHub private vulnerability reporting enabled, use its
**Security → Report a vulnerability** action. Otherwise, ask the maintainer for
a private reporting channel without publishing an exploit or sensitive data in
an ordinary issue. This source distribution does not contain a maintainer email
address or assert that private reporting has already been enabled.

Include the affected version, Fedora/GNOME versions, relevant configuration,
expected and actual behavior, and a minimal reproduction using a synthetic image
where possible. Redact usernames, private image paths and unrelated journal
entries. Do not send original personal wallpapers unless they are necessary and
you intend to share them.

Security-related changes should include a regression test that demonstrates the
boundary being protected. A correction to documentation should distinguish
verified behavior from assumptions and remaining external-write races.

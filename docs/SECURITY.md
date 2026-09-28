# Architecture, security and authorization

The Shell extension and Nautilus provider run without elevated privileges.
Nautilus changes only the two GNOME background URI settings. The extension uses
asynchronous D-Bus calls; it does not launch sudo, decode images or rebuild themes.
The on-demand Python user worker does conversion and blur in a separate process
from Shell, with ImageMagick memory/disk/time limits and fixed staging filenames.
AVIF decoding through GNOME runs as the user. Decoder failures cannot directly
write root-owned resources.

ImageMagick is bounded to 512 MiB memory cache, 1 GiB mapped cache, 4 GiB scratch
disk, four threads and 90 seconds of processing (105-second subprocess timeout).
The user service has a 3 GiB memory ceiling; these are upper limits, not memory
reserved while idle. They accommodate Fedora's 4000×4000 Q16-HDRI processing
without reducing image dimensions. Temporary image scratch files are removed
after a normal success or exception.

The root helper accepts only `Apply(fd, metadata)`, `ApplyGrub(fd, metadata)`,
`Restore()`, `RestoreGrub()` and `GetStatus()`.
It never accepts command strings, resource files, CSS, manifests or output paths.
Image metadata is bounded and only source URI and blur are retained. A source
URI is informational and is never dereferenced by root. Apply accepts exactly
one regular, caller-owned, fully sealed memfd, at most 64 MiB. The seals prevent
the caller changing the data while it is copied. The helper checks PNG/JPEG
headers and dimensions for GDM without invoking image decoders; GDM ultimately decodes
the embedded image as its normal unprivileged greeter user.

When configured, GRUB needs a fully decoded, 8-bit baseline RGB JPEG at exactly
the administrator-configured dimensions. The user
worker generates and validates that derivative. The helper independently validates
the sealed bytes, with image decoding delegated to a bounded subprocess running
as `nobody`; root does not decode pixels. Both image processors use local, bounded
regular-file snapshots, forced image coders and subprocess argument arrays.
Incoming paths are not part of the privileged interface, avoiding a user-path
TOCTOU race at installation.
The existing live/previous JPEGs are also fully decoded before a transaction;
they may retain their original bounded dimensions so the pre-sync theme image
can be restored byte for byte.

The system bus provides the sender's UID; the helper compares it with the
root-owned owner configuration. Before each write it checks that this UID still
has an active, non-remote graphical user session on seat0. Polkit then authorizes
the exact apply/restore action using the sender's unique system-bus name. The
installer-generated local administrator rule grants only these two actions to
the configured username. It does not grant `org.freedesktop.policykit.exec` or
trust an executable path. UID and session checks remain in the helper, even if
an administrator accidentally broadens the Polkit rule.

The rule intentionally does not depend solely on `subject.active`: a systemd
user service may belong to the user manager rather than its graphical session
scope. The helper verifies the owner's active session through logind instead.
This is account-level authorization. Any process running as that account can
request these narrow changes while that account has an active local session,
including a same-account SSH process. It is not an assertion that the calling
process itself originated in the graphical session. Other accounts are denied.
GDM's theme is global; this release assigns one owner and one local seat.

The helper executes installed Python in isolated mode (`-I`) and only adds its
root-owned installation directory to the module path. It receives no user
environment through D-Bus. Its systemd sandbox makes the filesystem read-only
except the GDM theme directory, the configured GRUB theme directory (when
enabled) and its private state/runtime directories, hides home
directories, and limits memory/tasks. Atomic resource replacement requires write
access to the parent theme directory; application code restricts destinations to
the fixed target and generated `.wallpaper-sync-*` siblings. It does not weaken
SELinux, chmod user images or make system files writable by users.

The installer owns the GRUB configuration. New installations leave it disabled
unless a theme name and image dimensions are explicitly supplied. The theme
name is restricted to a single component under `/boot/grub2/themes/`, and the
sandbox is generated for that exact directory. Each dimension is bounded to
64–8,192 pixels, with at most 8,294,400 total pixels. A user setting can pause
synchronization but cannot change the privileged destination or its dimensions.

The GRUB backend restricts writes to `background.jpg`, its single
`background.previous.jpg` backup, generated temporary siblings and private state.
It checks trusted directory/file ownership and permissions, refuses symbolic-link
destinations, validates before replacement and serializes its transactions. It
stages files on the destination filesystem, flushes them and replaces the live
image atomically; it never generates directly into the live path. The theme
directory stays root-owned, and installed wallpapers use mode 0644. An identical
image skips replacement. External theme installers do not share this lock, so
avoid running one concurrently with synchronization.

No helper operation runs the GRUB/theme installer or regenerates configuration.
GRUB configuration, EFI variables, kernels, initramfs and partition layout are
outside its interface. GDM and GRUB transactions are independent; failure of one
does not undo a successful update to the other.

Resource building starts from a SHA-256-verified Fedora original. All original
resource names/bytes are preserved except the known shell CSS entries; a fixed
image entry is added. XML is generated with escaping. The completed resource is
re-read and compared entry by entry before replacement. A file lock serializes
transactions. Root-owned snapshots and a durable pending record allow recovery
after interruption. A change made by another tool or package operation during
preparation causes refusal. There remains a small external-write race around the
final rename because the package manager and other tools do not share this lock.

The helper never restarts GDM. No user is logged out by synchronization or restore.
Disabling synchronization cancels queued/preparing work; an operation already
committed cannot be cancelled retroactively. Failed restore leaves automatic
synchronization disabled so it cannot overwrite recovery work.
`RestoreGrub()` uses only the fixed previous-image path. Its user-facing command
disables GRUB synchronization before requesting it, leaving GDM available.

The system helper retains `CAP_SETUID` through `AmbientCapabilities=CAP_SETUID`
because Fedora systemd otherwise drops it for an explicit root service using
seccomp protection. This permits the validator child to become `nobody`; its
UID transition clears effective, permitted and ambient capabilities.
`NoNewPrivileges=true` remains enabled. There is no decoder fallback to root.

## Local data and reporting

wpSYNC has no telemetry or remote synchronization service. Prepared derivatives
and job results live in the user's XDG cache/state directories; root recovery
state lives in `/var/lib/wallpaper-sync`. State and journal records can include
source image URIs, usernames, hashes and timestamps. The GDM resource and GRUB
wallpaper are system-wide artifacts, so an image selected for either is not
private to its original file's permissions. Review and redact diagnostics before
sharing them publicly.

[Security reporting](../SECURITY.md) covers responsible disclosure;
[recovery](RECOVERY.md) describes backup verification and restoration.

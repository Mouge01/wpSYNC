# Preparing and publishing a release

## Prepare the source

Update `VERSION` and `CHANGELOG.md` together. Keep extension metadata compatible
with the supported GNOME version. Run:

```sh
make check
make dist
```

For version 0.1.0, the outputs are `dist/wpSYNC-0.1.0.tar.gz` and
`dist/wpSYNC-0.1.0.tar.gz.sha256`. Review them before sharing them. Confirm
that the archive contains executable installer/CLI entry points, source,
synthetic fixtures, documentation and all license notices. It must not include
local logs, installed images, recovery state, compiled caches, private paths,
credentials or generated development output.

Automated checks validate local behavior. Record separate installed acceptance
results using [TEST_PLAN.md](TEST_PLAN.md), including exact platform versions.
Do not label GDM or GRUB rendering as tested until someone has inspected a new
greeter and a normal boot respectively.

## Publish on GitHub

Create a repository in the intended owner's GitHub account. Inspect the source
and commit history before pushing. Add the actual repository URL as `origin`
and push the selected branch; this guide intentionally does not prescribe an
account name or fabricate a remote URL.

After the repository exists:

1. Confirm its license and description: **wpSYNC — GNOME-to-GDM wallpaper sync
   for Fedora 44 / GNOME 50, with optional GRUB support**.
2. Enable GitHub private vulnerability reporting if available, and update
   `SECURITY.md` if a different private reporting channel is preferred.
3. Let the included checks workflow run; inspect its actual result. Manual
   dispatch of the release workflow also builds and checks the source.
4. Tag the reviewed release commit consistently with `VERSION` (for example,
   `v0.1.0` for `0.1.0`). A matching pushed version tag triggers the release
   workflow, which creates a **draft** release after checks pass.
5. Review the draft, its attached source archive and checksum. Add concise
   changes, supported platform, installation instructions and any outstanding
   visual acceptance limits before publishing.
6. Test installation from a newly extracted archive, rather than relying only
   on the development tree.

GitHub hosting, tags, release publication and any upstream extension-directory
submission are separate from preparing this source tree. A configured workflow
file is not evidence of a successful hosted CI run. The Shell extension alone
is insufficient: installation also requires the user worker and privileged
helper described in [INSTALLATION.md](INSTALLATION.md).

"""Fixed-target resource transactions, verified baselines and recovery."""

import fcntl
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from .common import atomic_json, load_json, timestamp
from .resource import build, digest

THEME = Path("/usr/share/gnome-shell/gnome-shell-theme.gresource")
STATE = Path("/var/lib/wallpaper-sync")


def packaged_theme(target=THEME):
    owner = subprocess.run(["/usr/bin/rpm", "-qf", str(target)], check=True,
                           capture_output=True, text=True, timeout=15).stdout.strip()
    dump = subprocess.run(["/usr/bin/rpm", "-q", "--dump", owner], check=True,
                          capture_output=True, text=True, timeout=15).stdout
    for line in dump.splitlines():
        fields = line.split()
        if fields and fields[0] == str(target):
            checksum = fields[3]
            if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
                raise RuntimeError("The installed RPM does not supply a SHA-256 theme checksum.")
            return {"package": owner, "sha256": checksum}
    raise RuntimeError("Cannot find the original theme checksum in the RPM database.")


class Backend:
    def __init__(self, target=THEME, state_dir=STATE, package_reader=packaged_theme,
                 initial_hash=None, preserve_label=True):
        self.target = Path(target)
        self.directory = Path(state_dir)
        self.package_reader = package_reader
        self.initial_hash = initial_hash
        self.preserve_label = preserve_label

    def _read_theme(self):
        info = self.target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise RuntimeError("Theme must be a regular file without group/world write access.")
        if self.preserve_label and info.st_uid != 0:
            raise RuntimeError("Theme is not owned by root.")
        return self.target.read_bytes()

    def _write(self, path, data, mode=0o600, theme=False):
        fd, temporary = tempfile.mkstemp(prefix=".wallpaper-sync-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            if theme and self.preserve_label:
                # Preserve the existing target's SELinux context before rename.
                # Only fixed root-owned paths reach this command.
                subprocess.run(["/usr/bin/chcon", "--reference=" + str(self.target), temporary],
                               check=True, capture_output=True, timeout=10)
            os.replace(temporary, path)
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _recover(self):
        pending = load_json(self.directory / "pending.json")
        if not pending:
            return
        current_hash = digest(self._read_theme())
        if current_hash == pending["after_sha256"]:
            atomic_json(self.directory / "state.json", pending["state"])
        elif current_hash != pending["before_sha256"]:
            raise RuntimeError("Interrupted transaction conflicts with another theme change; manual recovery required.")
        (self.directory / "pending.json").unlink()

    def _baseline(self, package, current):
        expected = package["sha256"]
        baseline = self.directory / ("original-" + expected + ".gresource")
        if baseline.exists():
            if digest(baseline.read_bytes()) != expected:
                raise RuntimeError("Saved Fedora original failed checksum validation.")
            return baseline
        candidates = [self.target, *sorted(self.target.parent.glob(self.target.name + ".bak-*"))]
        for path in candidates:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                continue
            if self.preserve_label and info.st_uid != 0:
                continue
            data = current if path == self.target else path.read_bytes()
            if digest(data) == expected:
                self._write(baseline, data)
                return baseline
        raise RuntimeError("No theme matching the installed Fedora RPM was found. No GDM files were changed.")

    def _transaction(self, operation, image=None, metadata=None, still_authorized=lambda: None):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(self.directory / "transaction.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._recover()
            current = self._read_theme()
            before = digest(current)
            package = self.package_reader(self.target)
            saved = load_json(self.directory / "state.json")
            allowed = {package["sha256"]}
            if saved:
                allowed.add(saved["installed_sha256"])
            elif self.initial_hash:
                allowed.add(self.initial_hash)
            if before not in allowed:
                raise RuntimeError("GDM theme was changed outside Wallpaper Sync. Refusing to overwrite it.")
            baseline = self._baseline(package, current)
            if not (self.directory / "preinstall.gresource").exists():
                self._write(self.directory / "preinstall.gresource", current)
                atomic_json(self.directory / "preinstall.json", {"preinstall_sha256": before, **package})
            if operation == "restore":
                output = baseline.read_bytes()
                details = {}
            else:
                with tempfile.TemporaryDirectory(prefix="build-", dir=self.directory) as directory:
                    output = build(baseline, image, metadata["format"], directory)
                details = dict(metadata)
            state = {"operation": operation, "modified": operation == "apply",
                     "installed_sha256": digest(output), "original_sha256": package["sha256"],
                     "package": package["package"], "updated_at": timestamp(), **details}
            # Check for package upgrades or external changes during compilation.
            if self.package_reader(self.target) != package or digest(self._read_theme()) != before:
                raise RuntimeError("GNOME theme changed during preparation; retry after updates finish.")
            still_authorized()
            self._write(self.directory / "previous.gresource", current)
            atomic_json(self.directory / "pending.json", {
                "before_sha256": before, "after_sha256": state["installed_sha256"], "state": state})
            self._write(self.target, output, 0o644, theme=True)
            atomic_json(self.directory / "state.json", state)
            (self.directory / "pending.json").unlink()
            return state

    def apply(self, image, metadata, still_authorized=lambda: None):
        return self._transaction("apply", image, metadata, still_authorized)

    def restore(self, still_authorized=lambda: None):
        return self._transaction("restore", still_authorized=still_authorized)

    def status(self):
        current = digest(self._read_theme())
        package = self.package_reader(self.target)
        saved = load_json(self.directory / "state.json")
        baseline = self.directory / ("original-" + package["sha256"] + ".gresource")
        return {**saved, "current_sha256": current, "rpm": package,
                "modified": current != package["sha256"],
                "source_uri": saved.get("source_uri") if current == saved.get("installed_sha256") else None,
                "matches_fedora_original": current == package["sha256"],
                "matches_last_operation": current == saved.get("installed_sha256"),
                "backup_available": baseline.exists() and digest(baseline.read_bytes()) == package["sha256"],
                "preinstall_backup": (self.directory / "preinstall.gresource").exists(),
                "transaction_pending": (self.directory / "pending.json").exists(),
                "greeter_refresh": "Resource is loaded when a new GDM greeter starts."}

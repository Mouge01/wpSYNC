"""Bounded JPEG transactions for an explicitly configured GRUB background.

The production destination comes from administrator-owned configuration, never
RPC arguments. ImageMagick always runs unprivileged. Restore copies the last
valid background without swapping backups.
"""

import contextlib
import datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from .grub_config import parse_grub_config, validate_grub_size

THEMES = Path("/boot/grub2/themes")
STATE = Path("/var/lib/wallpaper-sync/grub.json")
MAX_BYTES = 64 * 1024 * 1024
MAX_SIDE = 16384
MAX_PIXELS = 64 * 1024 * 1024
SIZE = (2880, 1800)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _event(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def inspect_jpeg(data, exact=True, size=SIZE):
    """Parse markers without decoding, enforcing one baseline RGB-sized frame."""
    if exact:
        size = validate_grub_size(size)
    if not isinstance(data, bytes) or not 4 <= len(data) <= MAX_BYTES:
        raise ValueError("GRUB requires a JPEG of at most 64 MiB.")
    if data[:2] != b"\xff\xd8":
        raise ValueError("GRUB image must be a baseline JPEG.")
    position, frame, scans, entropy = 2, None, 0, False
    while position < len(data):
        if entropy:
            position = data.find(b"\xff", position)
            if position < 0:
                break
        if data[position] != 255:
            raise ValueError("Invalid JPEG marker framing.")
        while position < len(data) and data[position] == 255:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if entropy and (marker == 0 or 0xD0 <= marker <= 0xD7):
            continue
        entropy = False
        if marker == 0xD9:
            if frame is None or not scans or position != len(data):
                raise ValueError("JPEG is incomplete or has trailing data.")
            return frame
        if marker in {0, 0xD8, 1} or 0xD0 <= marker <= 0xD7:
            raise ValueError("Unexpected JPEG marker.")
        if position + 2 > len(data):
            break
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            raise ValueError("Truncated JPEG segment.")
        segment = data[position + 2:position + length]
        position += length
        if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
            if marker != 0xC0 or frame is not None:
                raise ValueError("GRUB requires baseline, non-progressive JPEG.")
            if len(segment) != 15 or segment[0] != 8 or segment[5] != 3:
                raise ValueError("GRUB requires 8-bit, three-component JPEG.")
            height = int.from_bytes(segment[1:3], "big")
            width = int.from_bytes(segment[3:5], "big")
            if not min(width, height) or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
                raise ValueError("JPEG dimensions exceed the safe decoding limit.")
            if exact and (width, height) != size:
                raise ValueError(f"GRUB candidate must be exactly {size[0]}×{size[1]}.")
            if len({segment[6], segment[9], segment[12]}) != 3:
                raise ValueError("JPEG component identifiers must be unique.")
            frame = (width, height)
        elif marker == 0xDA:
            if frame is None or not segment:
                raise ValueError("JPEG scan precedes its frame.")
            count = segment[0]
            if not 1 <= count <= 3 or len(segment) != 1 + 2 * count + 3 or segment[-3:] != b"\x00\x3f\x00":
                raise ValueError("Invalid baseline JPEG scan.")
            scans += 1
            entropy = True
    raise ValueError("JPEG has no complete end marker.")


def validate_jpeg(data, exact=True, size=SIZE):
    """Fully decode only in a child which cannot retain root privileges."""
    dimensions = inspect_jpeg(data, exact, size)
    arguments = {
        "input": data, "capture_output": True, "timeout": 25,
        "env": {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                "HOME": "/nonexistent", "MAGICK_TEMPORARY_PATH": "/tmp"},
        "cwd": "/", "close_fds": True,
    }
    if os.geteuid() == 0:
        arguments.update(user=65534, group=65534, extra_groups=[])
    command = ["/usr/bin/magick", "-regard-warnings",
               "-limit", "memory", "256MiB", "-limit", "map", "512MiB",
               "-limit", "disk", "0", "-limit", "width", str(MAX_SIDE),
               "-limit", "height", str(MAX_SIDE), "-limit", "area", str(MAX_PIXELS),
               "-limit", "thread", "1", "-limit", "time", "20",
               "JPEG:-", "-format", "%#", "info:"]
    try:
        process = subprocess.run(command, **arguments)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Unprivileged GRUB JPEG validation could not finish: " + str(error)) from error
    # Computing a pixel signature forces full decoding. Some ImageMagick JPEG
    # warnings return exit code zero even with -regard-warnings; reject them.
    if process.returncode or process.stderr.strip() or re.fullmatch(rb"[0-9a-f]{64}", process.stdout) is None:
        raise ValueError("GRUB JPEG failed full unprivileged decoding.")
    return dimensions


class GrubBackend:
    def __init__(self, uid=1000, *, config=None, target=None, state_file=None, test_mode=False):
        if type(uid) is not int or not 0 < uid < 2 ** 31:
            raise ValueError("A valid originating user UID is required.")
        if (target is not None or state_file is not None) and not test_mode:
            raise ValueError("Production GRUB paths cannot be overridden.")
        if test_mode and os.geteuid() == 0:
            raise ValueError("Test path overrides must run without root privileges.")
        if test_mode and (target is None or state_file is None):
            raise ValueError("Test mode requires both temporary paths.")
        if test_mode and config is None:
            config = {"enabled": True, "theme": "test-theme", "width": SIZE[0], "height": SIZE[1]}
        self.config = parse_grub_config(config)
        self.enabled = self.config["enabled"]
        self.size = (self.config["width"], self.config["height"]) if self.enabled else None
        self.uid = uid
        destination = THEMES / self.config["theme"] / "background.jpg" if self.enabled else None
        self.target = Path(os.path.abspath(target or destination)) if target or destination else None
        self.previous = self.target.with_name("background.previous.jpg") if self.target else None
        self.theme = self.target.with_name("theme.txt") if self.target else None
        self.state_file = Path(os.path.abspath(state_file or STATE))
        self.lock_file = self.state_file.with_name("grub.lock")
        self.test_mode = test_mode
        self.owner = os.geteuid() if test_mode else 0
        self.anchor = Path(os.path.commonpath([self.target.parent, self.state_file.parent])) if test_mode else Path("/")

    def _check_info(self, path, info, directory=False):
        valid_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid_kind or info.st_uid != self.owner or info.st_mode & 0o022:
            raise RuntimeError(f"Unsafe GRUB path (type, ownership or permissions): {path}")

    def _check_ancestors(self, path):
        current = Path(path)
        while True:
            self._check_info(current, current.lstat(), directory=True)
            if current == self.anchor:
                return
            if current.parent == current:
                raise RuntimeError("GRUB path lies outside its trusted directory.")
            current = current.parent

    def _read(self, path, limit=MAX_BYTES, optional=False):
        self._check_ancestors(path.parent)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if optional:
                return None
            raise
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            self._check_info(path, info)
            if info.st_size > limit:
                raise RuntimeError(f"GRUB file exceeds its size limit: {path}")
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError(f"GRUB file grew beyond its size limit: {path}")
        return data

    def _check_theme(self):
        content = self._read(self.theme, 128 * 1024).decode("utf-8")
        lines = [line for line in content.splitlines() if re.match(r"\s*desktop-image\s*:", line)]
        if len(lines) != 1 or not re.fullmatch(r'\s*desktop-image\s*:\s*"background\.jpg"\s*(?:#.*)?', lines[0]):
            raise RuntimeError("Installed theme must reference the existing background.jpg.")

    def _check_state_directory(self, create=False):
        directory = self.state_file.parent
        try:
            self._check_ancestors(directory)
        except FileNotFoundError:
            self._check_ancestors(directory.parent)
            if not create:
                return False
            directory.mkdir(mode=0o700)
            self._check_ancestors(directory)
        return True

    @contextlib.contextmanager
    def _lock(self):
        self._check_state_directory(create=True)
        descriptor = os.open(self.lock_file, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            self._check_info(self.lock_file, os.fstat(descriptor))
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _saved(self):
        if not self._check_state_directory():
            return {}
        data = self._read(self.state_file, 64 * 1024, optional=True)
        if data is None:
            return {}
        try:
            result = json.loads(data)
        except (UnicodeError, ValueError) as error:
            raise RuntimeError("GRUB state is invalid JSON.") from error
        if not isinstance(result, dict):
            raise RuntimeError("GRUB state is not an object.")
        return result

    def _stage(self, path, data, mode, label_from=None):
        self._check_ancestors(path.parent)
        # Existing destination must also satisfy the same trust checks.
        self._read(path, MAX_BYTES, optional=True)
        descriptor, name = tempfile.mkstemp(prefix=".wallpaper-sync-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                if not self.test_mode:
                    os.fchown(stream.fileno(), 0, 0)
                os.fchmod(stream.fileno(), mode)
                if label_from is not None and not self.test_mode:
                    try:
                        label = os.getxattr(label_from, "security.selinux", follow_symlinks=False)
                    except OSError as error:
                        if error.errno not in {errno.ENODATA, errno.ENOTSUP}:
                            raise
                    else:
                        os.setxattr(stream.fileno(), "security.selinux", label)
                os.fsync(stream.fileno())
            return temporary
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _commit(self, temporary, destination):
        self._check_ancestors(destination.parent)
        self._read(destination, MAX_BYTES, optional=True)
        os.replace(temporary, destination)
        try:
            descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            # Rename already completed; do not report that the old file remains.
            return f"Updated {destination.name}, but directory durability could not be confirmed."
        return None

    def _snapshot(self):
        self._check_theme()
        current = self._read(self.target)
        previous = self._read(self.previous, optional=True)
        validate_jpeg(current, exact=False)
        if previous is not None:
            validate_jpeg(previous, exact=False)
        return current, previous

    def _details(self, current, previous, saved, changed=False):
        current_hash = digest(current)
        matching = current_hash == saved.get("installed_sha256")
        info = self.target.stat(follow_symlinks=False)
        return {"enabled": True, "available": True, "theme": self.config["theme"],
                "output_width": self.size[0], "output_height": self.size[1],
                "target": str(self.target), "current_sha256": current_hash,
                "installed_sha256": saved.get("installed_sha256"),
                "matches_last_operation": matching,
                "source_uri": saved.get("source_uri") if matching else None,
                "image_sha256": saved.get("image_sha256") if matching else None,
                "updated_at": saved.get("updated_at"), "operation": saved.get("operation"),
                "backup_available": previous is not None,
                "previous_sha256": digest(previous) if previous is not None else None,
                "changed": changed, "width": inspect_jpeg(current, exact=False)[0],
                "height": inspect_jpeg(current, exact=False)[1],
                "mtime_ns": info.st_mtime_ns, "uid": info.st_uid, "gid": info.st_gid,
                "mode": oct(stat.S_IMODE(info.st_mode)),
                "refresh": "The background is read at the next GRUB menu."}

    def status(self):
        if not self.enabled:
            return {"enabled": False, "available": False, "configuration_required": True,
                    "message": "Optional GRUB synchronization is disabled. Configure an existing theme and output resolution during installation to enable it."}
        current, previous = self._snapshot()
        return self._details(current, previous, self._saved())

    def apply(self, data, metadata, authorize=lambda: None):
        self._require_enabled()
        validate_jpeg(data, size=self.size)
        if not isinstance(metadata, dict):
            raise ValueError("GRUB metadata must be an object.")
        source_uri = metadata.get("source_uri")
        if source_uri is not None and (not isinstance(source_uri, str) or len(source_uri) > 8192):
            raise ValueError("Invalid source URI metadata.")
        if metadata.get("image_sha256", digest(data)) != digest(data):
            raise ValueError("GRUB image checksum does not match its metadata.")
        return self._transaction(data, {"source_uri": source_uri, "image_sha256": digest(data)}, "apply", authorize)

    def restore(self, authorize=lambda: None):
        self._require_enabled()
        return self._transaction(None, {}, "restore", authorize)

    def _require_enabled(self):
        if not self.enabled:
            raise RuntimeError("Optional GRUB synchronization is disabled; configure an existing theme and output resolution during installation.")

    def _transaction(self, output, metadata, operation, authorize):
        # Validate all existing image inputs before creating transaction state.
        current, previous = self._snapshot()
        saved = self._saved()
        if operation == "restore":
            if previous is None:
                raise RuntimeError("No previous GRUB background is available.")
            output = previous
        identical = current == output
        state = {"operation": operation, "installed_sha256": digest(output),
                 "image_sha256": digest(output), "source_uri": None, **metadata}
        if identical and all(saved.get(key) == value for key, value in state.items()):
            _event("grub_unchanged", operation=operation, image_sha256=digest(current))
            return self._details(current, previous, saved)
        authorize()
        with self._lock():
            now, backup = self._snapshot()
            if now != current or backup != previous:
                raise RuntimeError("GRUB background changed during preparation; retry the operation.")
            # Reject unexpected state/permissions changes before any image commit.
            self._saved()
            state["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            staged = []
            warnings = []
            try:
                target_temp = None
                if not identical:
                    target_temp = self._stage(self.target, output, 0o644, label_from=self.target)
                    staged.append(target_temp)
                backup_temp = None
                if operation == "apply" and not identical:
                    backup_temp = self._stage(self.previous, current, 0o644, label_from=self.target)
                    staged.append(backup_temp)
                state_temp = self._stage(self.state_file, (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode(), 0o600)
                staged.append(state_temp)
                self._check_theme()
                if self._read(self.target) != current or self._read(self.previous, optional=True) != previous:
                    raise RuntimeError("GRUB background changed before commit; retry the operation.")
                authorize()
                if backup_temp is not None:
                    warnings.append(self._commit(backup_temp, self.previous))
                if target_temp is not None:
                    warnings.append(self._commit(target_temp, self.target))
                try:
                    warnings.append(self._commit(state_temp, self.state_file))
                except OSError:
                    warnings.append("GRUB background is installed; state metadata could not be committed.")
            finally:
                for temporary in staged:
                    temporary.unlink(missing_ok=True)
            _event("grub_unchanged" if identical else "grub_background_" + operation,
                   uid=self.uid, image_sha256=digest(output), metadata_updated=True)
            actual_previous = current if operation == "apply" and not identical else previous
            result = self._details(output, actual_previous, state, changed=not identical)
            if any(warnings):
                result["warning"] = " ".join(warning for warning in warnings if warning)
                _event("grub_commit_warning", warning=result["warning"], image_sha256=digest(output))
            return result

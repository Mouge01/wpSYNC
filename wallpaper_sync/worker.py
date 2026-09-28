"""On-demand user service: asynchronous image jobs, coalescing and status."""

import json
import threading
import time
import uuid

from gi.repository import Gio, GLib

from .client import helper_call
from .common import (BUS, OBJECT, USER_XML, atomic_json, desktop_source, load_json,
                     log, settings, timestamp, user_state_path)
from .images import prepare
from .grub_images import prepare_grub


def synchronize(source, still_current):
    """Serial backends share a source and cancellation check, but fail independently."""
    outcomes = {}
    errors = {}
    for name in ("gdm", "grub"):
        if name == "grub" and not source["grub_enabled"]:
            outcomes[name] = {"disabled": True}
            continue
        try:
            still_current()
            if name == "gdm":
                data, metadata = prepare(source["uri"], source["blur"], source["convert"])
                still_current()
                current = helper_call("GetStatus")
            else:
                current = helper_call("GetStatus").get("grub", {})
                if not current.get("enabled"):
                    raise RuntimeError("GRUB is not configured; use the installer's --grub-theme and --grub-size options.")
                if current.get("error"):
                    raise RuntimeError(current["error"])
                data, metadata = prepare_grub(source["uri"],
                    size=(current["output_width"], current["output_height"]))
            still_current()
            matches = current.get("matches_last_operation")
            if (matches and current.get("image_sha256") == metadata["sha256"]
                    and current.get("source_uri") == source["uri"]
                    and (name == "grub" or current.get("blur") == source["blur"])):
                result = {k: v for k, v in current.items() if k != "grub"}
                result["skipped"] = True
                log(name + "_unchanged", source=source["uri"])
            else:
                still_current()
                result = helper_call("ApplyGrub" if name == "grub" else "Apply", data, metadata)
            outcomes[name] = result
        except Exception as error:
            errors[name] = str(error)
            log(name + "_sync_failed", error=str(error))
    messages = []
    for name in ("gdm", "grub"):
        if name in errors:
            messages.append(name.upper() + " failed: " + errors[name])
        elif outcomes[name].get("disabled"):
            messages.append("GRUB synchronization is disabled.")
        elif outcomes[name].get("skipped") or outcomes[name].get("changed") is False:
            messages.append(name.upper() + " wallpaper unchanged; skipped.")
        else:
            messages.append(name.upper() + " wallpaper synchronized.")
        if outcomes.get(name, {}).get("warning"):
            messages.append(outcomes[name]["warning"])
    return {"success": not errors, "message": " ".join(messages), "errors": errors, **outcomes}


class Worker:
    def __init__(self, connection):
        self.connection = connection
        self.config = settings()
        self.result = load_json(user_state_path())
        self.completed = {}
        self.pending = None
        self.active = None
        self.timer = 0
        self.next_start = 0
        self.last_activity = time.monotonic()
        self.loop = GLib.MainLoop()
        self.generation = 0
        self.gdm = self.result.get("gdm", {})
        self.grub = self.result.get("grub", {})
        self.helper_error = None
        self.probing = False
        self.probed_at = 0
        self.config.connect("changed", self.settings_changed)
        node = Gio.DBusNodeInfo.new_for_xml(USER_XML)
        self.registration = connection.register_object(OBJECT, node.interfaces[0], self.call, None, None)

    def settings_changed(self, config, key):
        if key != "show-notifications":
            self.generation += 1

    def status(self):
        if not self.active and not self.pending and not self.probing and time.monotonic() - self.probed_at > 10:
            self.probing = True
            def probe():
                try:
                    result, error = helper_call("GetStatus"), None
                except Exception as failure:
                    result, error = self.gdm, str(failure)
                    if "ServiceUnknown" in error or "not activatable" in error:
                        error = "The Wallpaper Sync helper is not installed or available."
                GLib.idle_add(self.probe_finished, result, error)
            threading.Thread(target=probe, daemon=True).start()
        return {"backend_status": "working" if self.active else "ready",
                "gdm": self.gdm, "grub": self.grub, "helper_error": self.helper_error, "helper_checked": bool(self.probed_at),
                "active_job": self.active, "pending_job": self.pending,
                "desktop": desktop_source(self.config), "last_result": self.result,
                "completed": self.completed,
                "synchronize": self.config.get_boolean("synchronize"),
                "follow_current": self.config.get_boolean("follow-current")}

    def probe_finished(self, result, error):
        self.grub = result.get("grub", self.grub)
        self.gdm = {k: v for k, v in result.items() if k != "grub"}
        self.helper_error = error
        self.probing = False
        self.probed_at = time.monotonic()
        return False

    def call(self, connection, sender, path, interface, method, parameters, invocation):
        self.last_activity = time.monotonic()
        try:
            if method == "GetStatus":
                reply = self.status()
            elif method in {"Sync", "Restore", "RestoreGrub"}:
                force = method in {"Restore", "RestoreGrub"} or parameters.unpack()[0]
                if method == "Restore":
                    self.config.set_boolean("synchronize", False)
                    Gio.Settings.sync()
                    self.generation += 1
                elif method == "RestoreGrub":
                    self.config.set_boolean("grub-enabled", False)
                    Gio.Settings.sync()
                    self.generation += 1
                elif not self.config.get_boolean("synchronize"):
                    raise RuntimeError("Synchronization is disabled. Enable it in Wallpaper Sync preferences.")
                elif not force and not self.config.get_boolean("follow-current"):
                    reply = {"skipped": "Automatic following is disabled."}
                    invocation.return_value(GLib.Variant("(s)", (json.dumps(reply),)))
                    return
                if (method == "Sync" and not force and self.pending
                        and self.pending["method"] in {"Restore", "RestoreGrub"}):
                    reply = {"skipped": "An explicit rollback is queued."}
                    invocation.return_value(GLib.Variant("(s)", (json.dumps(reply),)))
                    return
                requested_source = desktop_source(self.config)
                if method == "Sync" and not force:
                    # The extension can deliver the same desktop event just
                    # after a manual request. Join that job instead of cancelling
                    # it and making the manual caller report a false failure.
                    same_pending = (self.pending and self.pending["method"] == "Sync"
                                    and self.pending.get("source") == requested_source)
                    same_active = (not self.pending and self.active
                                   and self.active["method"] == "Sync"
                                   and self.active.get("source") == requested_source
                                   and self.active.get("generation") == self.generation)
                    existing = self.pending if same_pending else self.active if same_active else None
                    if existing:
                        invocation.return_value(GLib.Variant("(s)", (json.dumps({"job_id": existing["id"]}),)))
                        return
                job = {"id": uuid.uuid4().hex, "method": method, "force": force,
                       "source": requested_source}
                if self.pending:
                    self.completed[self.pending["id"]] = {"success": False, "message": "Superseded by a newer request."}
                self.pending = job
                self.generation += 1
                if self.timer:
                    GLib.source_remove(self.timer)
                self.timer = GLib.timeout_add(750, self.start)
                reply = {"job_id": job["id"]}
            else:
                raise ValueError("Unknown operation.")
            invocation.return_value(GLib.Variant("(s)", (json.dumps(reply),)))
        except Exception as error:
            invocation.return_dbus_error(BUS + ".Failed", str(error))

    def start(self):
        self.timer = 0
        if self.active or not self.pending:
            return False
        remaining = self.next_start - time.monotonic()
        if remaining > 0:
            self.timer = GLib.timeout_add(max(1, int(remaining * 1000) + 1), self.start)
            return False
        if self.probing:
            self.timer = GLib.timeout_add(100, self.start)
            return False
        self.active, self.pending = self.pending, None
        job = self.active
        generation = self.generation
        source = desktop_source(self.config)
        job.update({"source": source, "generation": generation})
        log("wallpaper_change_detected", source=source["uri"], operation=job["method"])

        def execute():
            try:
                if job["method"] == "Restore":
                    result = helper_call("Restore")
                    outcome = {"success": True, "gdm": result,
                               "message": "Fedora's original GDM theme restored; master synchronization is off."}
                elif job["method"] == "RestoreGrub":
                    result = helper_call("RestoreGrub")
                    outcome = {"success": True, "grub": result,
                               "message": "Previous GRUB wallpaper restored; GRUB synchronization is off."}
                else:
                    def still_current():
                        if (generation != self.generation or not self.config.get_boolean("synchronize")
                                or desktop_source(self.config) != source):
                            raise RuntimeError("Superseded or disabled before applying.")
                    outcome = synchronize(source, still_current)
            except Exception as error:
                outcome = {"success": False, "message": str(error)}
            outcome.update({"job_id": job["id"], "operation": job["method"],
                            "time": timestamp(), "source_uri": source["uri"]})
            GLib.idle_add(self.finish, outcome)
        threading.Thread(target=execute, daemon=True).start()
        return False

    def finish(self, outcome):
        if "grub" in outcome:
            self.grub = outcome["grub"]
        if "gdm" in outcome:
            self.gdm = outcome["gdm"]
            self.helper_error = None
            self.probed_at = time.monotonic()
        elif self.gdm:
            outcome["gdm"] = self.gdm
        self.result = outcome
        self.completed[outcome["job_id"]] = outcome
        while len(self.completed) > 20:
            self.completed.pop(next(iter(self.completed)))
        try:
            atomic_json(user_state_path(), outcome)
        except Exception as error:
            log("status_write_error", error=str(error))
        log("sync_result", **outcome)
        self.connection.emit_signal(None, OBJECT, BUS, "Completed",
            GLib.Variant("(bs)", (outcome["success"], outcome["message"])))
        self.active = None
        self.last_activity = time.monotonic()
        self.next_start = self.last_activity + 2.2
        if self.pending:
            if self.timer:
                GLib.source_remove(self.timer)
            # Always let the previous backend request age past its rate limit,
            # even when an event arrived near the end of the active job.
            self.timer = GLib.timeout_add(2200, self.start)
        return False

    def idle(self):
        if not self.active and not self.pending and time.monotonic() - self.last_activity > 60:
            self.loop.quit()
            return False
        return True


def main():
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    worker = Worker(connection)
    name = Gio.bus_own_name_on_connection(connection, BUS, Gio.BusNameOwnerFlags.NONE,
        None, lambda *_: worker.loop.quit())
    GLib.timeout_add_seconds(15, worker.idle)
    try:
        worker.loop.run()
    finally:
        Gio.bus_unown_name(name)


if __name__ == "__main__":
    main()

// SPDX-License-Identifier: MIT
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS = 'io.github.mouge.WallpaperSync';
const OBJECT = '/io/github/mouge/WallpaperSync';

export default class WallpaperSync extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._background = new Gio.Settings({schema_id: 'org.gnome.desktop.background'});
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._cancellable = new Gio.Cancellable();
        this._signals = [];
        this._timer = 0;
        for (const key of ['picture-uri', 'picture-uri-dark'])
            this._connect(this._background, `changed::${key}`);
        this._connect(this._interface, 'changed::color-scheme');
        for (const key of ['synchronize', 'blur', 'follow-current', 'convert-unsupported', 'wallpaper-mode', 'grub-enabled'])
            this._connect(this._settings, `changed::${key}`);
        this._subscription = Gio.DBus.session.signal_subscribe(
            BUS, BUS, 'Completed', OBJECT, null, Gio.DBusSignalFlags.NONE,
            (_bus, _sender, _path, _interface, _signal, parameters) => {
                const [success, message] = parameters.deepUnpack();
                if (this._settings?.get_boolean('show-notifications'))
                    Main.notify(success ? 'Wallpaper Sync' : 'Wallpaper Sync failed', message);
            });
        this._queue();
    }

    _connect(object, signal) {
        this._signals.push([object, object.connect(signal, () => this._queue())]);
    }

    _queue() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        if (!this._settings.get_boolean('synchronize') || !this._settings.get_boolean('follow-current'))
            return;
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, () => {
            this._timer = 0;
            // Determine the active source here; the worker reads the same settings
            // again before processing so stale queued requests cannot set old images.
            const mode = this._settings.get_string('wallpaper-mode');
            const dark = mode === 'dark' || (mode === 'active' &&
                this._interface.get_string('color-scheme') === 'prefer-dark');
            const lightUri = this._background.get_string('picture-uri');
            const uri = dark ? (this._background.get_string('picture-uri-dark') || lightUri) : lightUri;
            if (!uri)
                return GLib.SOURCE_REMOVE;
            Gio.DBus.session.call(BUS, OBJECT, BUS, 'Sync', new GLib.Variant('(b)', [false]),
                new GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 10000, this._cancellable,
                (connection, result) => {
                    try {
                        connection.call_finish(result);
                    } catch (error) {
                        if (!error.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED)) {
                            console.error(`Wallpaper Sync: ${error.message}`);
                            if (this._settings?.get_boolean('show-notifications'))
                                Main.notify('Wallpaper Sync unavailable', error.message);
                        }
                    }
                });
            return GLib.SOURCE_REMOVE;
        });
    }

    disable() {
        if (this._timer)
            GLib.source_remove(this._timer);
        for (const [object, signal] of this._signals ?? [])
            object.disconnect(signal);
        if (this._subscription)
            Gio.DBus.session.signal_unsubscribe(this._subscription);
        this._cancellable?.cancel();
        this._signals = [];
        this._timer = 0;
        this._subscription = 0;
        this._settings = null;
        this._background = null;
        this._interface = null;
        this._cancellable = null;
    }
}

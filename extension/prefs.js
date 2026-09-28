// SPDX-License-Identifier: MIT
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const BUS = 'io.github.mouge.WallpaperSync';
const OBJECT = '/io/github/mouge/WallpaperSync';

export default class WallpaperSyncPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const cancellable = new Gio.Cancellable();
        window.set_default_size(680, 760);
        const page = new Adw.PreferencesPage({title: 'Wallpaper Sync', icon_name: 'preferences-desktop-wallpaper-symbolic'});
        window.add(page);
        const group = (title, description = '') => {
            const item = new Adw.PreferencesGroup({title, description});
            page.add(item);
            return item;
        };
        const toggle = (parent, title, key, subtitle = '') => {
            const row = new Adw.SwitchRow({title, subtitle});
            settings.bind(key, row, 'active', Gio.SettingsBindFlags.DEFAULT);
            parent.add(row);
            return row;
        };
        const synchronization = group('Synchronization');
        toggle(synchronization, 'Synchronize wallpapers', 'synchronize');
        toggle(synchronization, 'Follow current desktop wallpaper', 'follow-current',
            'When off, use Sync Now to update the login and boot wallpapers.');

        toggle(synchronization, 'Sync GRUB boot wallpaper', 'grub-enabled',
            'Requires GRUB setup during installation; takes effect at the next boot.');

        const appearance = group('Appearance');
        const blurRow = new Adw.ActionRow({title: 'Login screen blur'});
        const adjustment = new Gtk.Adjustment({lower: 0, upper: 30, step_increment: 1, page_increment: 5});
        const scale = new Gtk.Scale({orientation: Gtk.Orientation.HORIZONTAL, adjustment,
            digits: 0, draw_value: true, value_pos: Gtk.PositionType.RIGHT, width_request: 250,
            valign: Gtk.Align.CENTER});
        scale.set_value(settings.get_int('blur'));
        scale.connect('value-changed', () => settings.set_int('blur', Math.round(scale.get_value())));
        const blurSignal = settings.connect('changed::blur', () => scale.set_value(settings.get_int('blur')));
        blurRow.add_suffix(scale);
        appearance.add(blurRow);

        const source = group('Wallpaper source');
        let first = null;
        const radios = [];
        for (const [value, title] of [['active', 'Follow wallpaper currently active'],
            ['dark', 'Always use dark wallpaper'], ['light', 'Always use light wallpaper']]) {
            const row = new Adw.ActionRow({title});
            const radio = new Gtk.CheckButton({valign: Gtk.Align.CENTER});
            if (first)
                radio.set_group(first);
            else
                first = radio;
            radio.set_active(settings.get_string('wallpaper-mode') === value);
            radio.connect('toggled', () => {
                if (radio.active)
                    settings.set_string('wallpaper-mode', value);
            });
            row.add_prefix(radio);
            row.activatable_widget = radio;
            source.add(row);
            radios.push([value, radio]);
        }
        const modeSignal = settings.connect('changed::wallpaper-mode', () => {
            for (const [value, radio] of radios)
                radio.set_active(value === settings.get_string('wallpaper-mode'));
        });
        const compatibility = group('Compatibility');
        toggle(compatibility, 'Convert unsupported formats', 'convert-unsupported',
            'Prepare WebP, AVIF, JPEG XL, BMP and TIFF images as PNG.');
        toggle(compatibility, 'Show synchronization notifications', 'show-notifications');

        const status = group('Status', 'Changes appear when a new login screen starts.');
        const rows = {};
        for (const [key, title] of [['desktop', 'Current desktop wallpaper'], ['gdm', 'Current GDM wallpaper'],
            ['grub', 'Current GRUB wallpaper'], ['last', 'Last synchronization'], ['backend', 'Backend status']]) {
            rows[key] = new Adw.ActionRow({title, subtitle: 'Checking…', subtitle_lines: 3});
            status.add(rows[key]);
        }
        const syncRow = new Adw.ActionRow({title: 'Update wallpapers'});
        const button = new Gtk.Button({label: 'Sync Now', valign: Gtk.Align.CENTER, css_classes: ['suggested-action']});
        settings.bind('synchronize', button, 'sensitive', Gio.SettingsBindFlags.GET);
        syncRow.add_suffix(button);
        status.add(syncRow);

        const call = (method, arguments_, callback) => {
            Gio.DBus.session.call(BUS, OBJECT, BUS, method, arguments_, new GLib.VariantType('(s)'),
                Gio.DBusCallFlags.NONE, 10000, cancellable, (connection, result) => {
                    try {
                        const [text] = connection.call_finish(result).deepUnpack();
                        callback(JSON.parse(text));
                    } catch (error) {
                        if (!error.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                            rows.backend.subtitle = `Unavailable: ${error.message}`;
                    }
                });
        };
        const display = uri => {
            if (!uri)
                return 'Unknown';
            const file = Gio.File.new_for_uri(uri);
            return file.get_parse_name();
        };
        const refresh = () => {
            call('GetStatus', null, data => {
                rows.desktop.subtitle = display(data.desktop?.active_uri);
                const last = data.last_result ?? {};
                const gdm = data.gdm ?? {};
                rows.gdm.subtitle = gdm.matches_fedora_original === true ||
                    (gdm.matches_fedora_original === undefined && gdm.operation === 'restore') ? 'Fedora default' :
                    (gdm.matches_last_operation === false ? 'Changed outside Wallpaper Sync; source unknown' :
                        display(gdm.source_uri));
                rows.grub.subtitle = !data.desktop?.grub_enabled ? 'GRUB synchronization is off' :
                    (data.grub?.error ? data.grub.error :
                        (data.grub?.configuration_required ? data.grub.message : display(data.grub?.source_uri)));
                rows.last.subtitle = last.time ? `${last.time}\n${last.message}` : 'No synchronization recorded';
                rows.backend.subtitle = data.active_job ? 'Synchronizing…' :
                    (data.helper_error ? `Unavailable: ${data.helper_error}` :
                        (!data.helper_checked ? 'Checking helper…' :
                    (last.success === false ? `Last operation failed: ${last.message}` :
                        (data.synchronize ? 'Ready' : 'Synchronization is off'))));
            });
            return GLib.SOURCE_CONTINUE;
        };
        button.connect('clicked', () => {
            rows.backend.subtitle = 'Synchronization requested…';
            call('Sync', new GLib.Variant('(b)', [true]), () => refresh());
        });
        refresh();
        const timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 3, refresh);
        window.connect('close-request', () => {
            GLib.source_remove(timer);
            settings.disconnect(blurSignal);
            settings.disconnect(modeSignal);
            cancellable.cancel();
            return false;
        });
    }
}

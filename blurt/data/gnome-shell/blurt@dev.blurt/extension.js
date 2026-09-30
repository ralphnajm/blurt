import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const BUS_NAME = 'dev.blurt.Daemon';
const OBJECT_PATH = '/dev/blurt/Daemon';
const INTERFACE = 'dev.blurt.Daemon';
const APP_ID = 'dev.blurt.Blurt';
const KEYBINDING_SCHEMA = 'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding';
const KEYBINDING_PATH = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/blurt-toggle/';

const STATUS = {
    idle: 'Ready',
    recording: 'Listening…',
    processing: 'Transcribing…',
};

function acceleratorLabel(accel) {
    if (!accel)
        return '';
    const mods = [...accel.matchAll(/<(\w+)>/g)].map(m => (m[1] === 'Primary' ? 'Ctrl' : m[1]));
    const key = accel.replace(/<\w+>/g, '');
    return [...mods, key.length === 1 ? key.toUpperCase() : key].join('+');
}

const BlurtIndicator = GObject.registerClass(
class BlurtIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.5, 'Blurt');
        this._extension = extension;
        this._state = 'idle';

        this._icon = new St.Icon({style_class: 'system-status-icon'});
        this.add_child(this._icon);

        this._statusItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._statusItem.label.add_style_class_name('blurt-status');
        this.menu.addMenuItem(this._statusItem);

        this._toggleItem = new PopupMenu.PopupMenuItem('Start dictation');
        this._toggleItem.connect('activate', () => this._call('Toggle'));
        this.menu.addMenuItem(this._toggleItem);

        this._cancelItem = new PopupMenu.PopupMenuItem('Cancel dictation');
        this._cancelItem.connect('activate', () => this._call('Cancel'));
        this.menu.addMenuItem(this._cancelItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const settingsItem = new PopupMenu.PopupMenuItem('Settings…');
        settingsItem.connect('activate', () => this._openSettings());
        this.menu.addMenuItem(settingsItem);

        const quitItem = new PopupMenu.PopupMenuItem('Quit Blurt');
        quitItem.connect('activate', () => this._call('Quit'));
        this.menu.addMenuItem(quitItem);

        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._refreshShortcut();
        });

        this._signalId = Gio.DBus.session.signal_subscribe(
            BUS_NAME, INTERFACE, 'StateChanged', OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE,
            (_conn, _sender, _path, _iface, _signal, params) => this._setState(params.deep_unpack()[0]));
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this._onAppeared(),
            () => this._onVanished());

        this.visible = false;
        this._shortcut = '';
        this._setState('idle');
    }

    _onAppeared() {
        this.visible = true;
        Gio.DBus.session.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, 'Status', null, new GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 2000, null,
            (conn, result) => {
                try {
                    this._setState(conn.call_finish(result).deep_unpack()[0]);
                } catch (e) {
                    logError(e, 'Blurt: status');
                }
            });
    }

    _onVanished() {
        this.menu.close();
        this.visible = false;
        this._setState('idle');
    }

    _setState(state) {
        this._state = state in STATUS ? state : 'idle';
        const suffix = {idle: '', recording: '-recording', processing: '-processing'}[this._state];
        this._icon.gicon = Gio.icon_new_for_string(
            `${this._extension.path}/icons/${APP_ID}${suffix}-symbolic.svg`);
        if (this._state === 'recording')
            this._icon.add_style_class_name('blurt-recording');
        else
            this._icon.remove_style_class_name('blurt-recording');

        const hint = this._state === 'idle' && this._shortcut ? ` · ${this._shortcut}` : '';
        this._statusItem.label.text = `${STATUS[this._state]}${hint}`;
        this._toggleItem.label.text = this._state === 'recording' ? 'Stop and type' : 'Start dictation';
        this._toggleItem.setSensitive(this._state !== 'processing');
        this._cancelItem.visible = this._state !== 'idle';
    }

    _refreshShortcut() {
        try {
            const settings = new Gio.Settings({schema_id: KEYBINDING_SCHEMA, path: KEYBINDING_PATH});
            this._shortcut = acceleratorLabel(settings.get_string('binding'));
        } catch (e) {
            this._shortcut = '';
        }
        this._setState(this._state);
    }

    _call(method) {
        Gio.DBus.session.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, method, null, null,
            Gio.DBusCallFlags.NONE, 5000, null,
            (conn, result) => {
                try {
                    conn.call_finish(result);
                } catch (e) {
                    logError(e, `Blurt: ${method}`);
                }
            });
    }

    _openSettings() {
        const app = Gio.DesktopAppInfo.new(`${APP_ID}.desktop`);
        if (app) {
            app.launch([], global.create_app_launch_context(0, -1));
            return;
        }
        GLib.spawn_command_line_async('blurt settings');
    }

    destroy() {
        if (this._signalId) {
            Gio.DBus.session.signal_unsubscribe(this._signalId);
            this._signalId = 0;
        }
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        super.destroy();
    }
});

export default class BlurtExtension extends Extension {
    enable() {
        this._indicator = new BlurtIndicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}

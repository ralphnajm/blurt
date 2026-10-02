import Clutter from 'gi://Clutter';
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
const SHELL_BUS_NAME = 'dev.blurt.Shell';
const SHELL_OBJECT_PATH = '/dev/blurt/Shell';
const SHELL_INTERFACE_XML = `
<node>
  <interface name="dev.blurt.Shell">
    <method name="Insert">
      <arg type="s" name="text" direction="in"/>
      <arg type="s" name="method" direction="out"/>
    </method>
  </interface>
</node>`;
const KEYBINDING_SCHEMA = 'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding';
const KEYBINDING_PATH = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/blurt-toggle/';

const POLL_MS = 40;
const HOLD_MASKS = {
    ctrl: Clutter.ModifierType.CONTROL_MASK,
    alt: Clutter.ModifierType.MOD1_MASK,
    shift: Clutter.ModifierType.SHIFT_MASK,
};
const HOLD_LABELS = {ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift'};
const MODIFIER_MASK = Clutter.ModifierType.CONTROL_MASK | Clutter.ModifierType.MOD1_MASK |
    Clutter.ModifierType.SHIFT_MASK | Clutter.ModifierType.SUPER_MASK | Clutter.ModifierType.MOD4_MASK;
const BUTTON_MASK = Clutter.ModifierType.BUTTON1_MASK | Clutter.ModifierType.BUTTON2_MASK |
    Clutter.ModifierType.BUTTON3_MASK | Clutter.ModifierType.BUTTON4_MASK |
    Clutter.ModifierType.BUTTON5_MASK;

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

        this._messageItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._messageItem.label.clutter_text.line_wrap = true;
        this._messageItem.label.add_style_class_name('blurt-message');
        this.menu.addMenuItem(this._messageItem);

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
            if (!open)
                return;
            this._unseen = false;
            this._refreshShortcut();
        });

        this._signalId = Gio.DBus.session.signal_subscribe(
            BUS_NAME, INTERFACE, 'StateChanged', OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE,
            (_conn, _sender, _path, _iface, _signal, params) => this._setState(params.deep_unpack()[0]));
        this._holdSignalId = Gio.DBus.session.signal_subscribe(
            BUS_NAME, INTERFACE, 'HoldSettingsChanged', OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE,
            (_conn, _sender, _path, _iface, _signal, params) => this._setHold(...params.deep_unpack()));
        this._messageSignalId = Gio.DBus.session.signal_subscribe(
            BUS_NAME, INTERFACE, 'MessageChanged', OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE,
            (_conn, _sender, _path, _iface, _signal, params) => this._setMessage(params.deep_unpack()[0]));
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            (_conn, _name, owner) => this._onAppeared(owner),
            () => this._onVanished());

        this.visible = false;
        this._shortcut = '';
        this._holdKey = 'off';
        this._holdDelay = 1;
        this._holdSince = 0;
        this._talking = false;
        this._pollId = 0;
        this._message = '';
        this._unseen = false;
        this._daemonOwner = null;
        // Older GNOME Shell releases have no stage context.
        const backend = global.stage.context?.get_backend() ?? Clutter.get_default_backend();
        this._keyboard = backend.get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._exported = Gio.DBusExportedObject.wrapJSObject(SHELL_INTERFACE_XML, this);
        this._exported.export(Gio.DBus.session, SHELL_OBJECT_PATH);
        this._shellNameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session, SHELL_BUS_NAME, Gio.BusNameOwnerFlags.REPLACE, null, null);
        this.connect('destroy', () => this._onDestroy());
        this._setState('idle');
    }

    _onAppeared(owner) {
        this._daemonOwner = owner;
        this.visible = true;
        Gio.DBus.session.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, 'Message', null, new GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 2000, null,
            (conn, result) => {
                try {
                    this._setMessage(conn.call_finish(result).deep_unpack()[0]);
                } catch (e) {
                    logError(e, 'Blurt: message');
                }
            });
        Gio.DBus.session.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, 'HoldSettings', null, new GLib.VariantType('(sd)'),
            Gio.DBusCallFlags.NONE, 2000, null,
            (conn, result) => {
                try {
                    this._setHold(...conn.call_finish(result).deep_unpack());
                } catch (e) {
                    logError(e, 'Blurt: hold settings');
                }
            });
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
        this._daemonOwner = null;
        this.menu.close();
        this._setMessage('');
        this.visible = false;
        this._setHold('off', this._holdDelay);
        this._setState('idle');
    }

    _setState(state) {
        this._state = state in STATUS ? state : 'idle';
        const suffix = {idle: '', recording: '-recording', processing: '-processing'}[this._state];
        this._icon.gicon = Gio.icon_new_for_string(
            `${this._extension.path}/icons/${APP_ID}${suffix}-symbolic.svg`);
        for (const name of ['blurt-recording', 'blurt-attention'])
            this._icon.remove_style_class_name(name);
        if (this._state === 'recording')
            this._icon.add_style_class_name('blurt-recording');
        else if (this._unseen)
            this._icon.add_style_class_name('blurt-attention');
        this._messageItem.label.text = this._message;
        this._messageItem.visible = this._message !== '';

        const ways = [];
        if (this._holdKey in HOLD_LABELS)
            ways.push(`hold ${HOLD_LABELS[this._holdKey]}`);
        if (this._shortcut)
            ways.push(this._shortcut);
        const hint = this._state === 'idle' && ways.length ? ` · ${ways.join(' or ')}` : '';
        this._statusItem.label.text = `${STATUS[this._state]}${hint}`;
        this._toggleItem.label.text = this._state === 'recording' ? 'Stop and type' : 'Start dictation';
        this._toggleItem.setSensitive(this._state !== 'processing');
        this._cancelItem.visible = this._state !== 'idle';
    }

    _setMessage(message) {
        this._message = message;
        this._unseen = message !== '' && !this.menu.isOpen;
        this._setState(this._state);
    }

    _setHold(key, delay) {
        this._holdKey = key in HOLD_MASKS ? key : 'off';
        this._holdDelay = Math.max(0.2, delay);
        if (this._holdKey === 'off')
            this._stopHoldWatch();
        else if (!this._pollId)
            this._pollId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, POLL_MS, () => {
                this._pollHold();
                return GLib.SOURCE_CONTINUE;
            });
        this._setState(this._state);
    }

    _stopHoldWatch() {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        if (this._talking)
            this._call('Stop');
        this._talking = false;
        this._holdSince = 0;
    }

    _pollHold() {
        const mask = HOLD_MASKS[this._holdKey];
        const [, , mods] = global.get_pointer();
        const held = (mods & mask) !== 0;
        if (this._talking) {
            if (!held) {
                this._talking = false;
                this._holdSince = 0;
                this._call('Stop');
            }
            return;
        }
        const alone = held && (mods & MODIFIER_MASK & ~mask) === 0 && (mods & BUTTON_MASK) === 0;
        if (!alone) {
            this._holdSince = 0;
            return;
        }
        const now = GLib.get_monotonic_time() / 1e6;
        if (!this._holdSince)
            this._holdSince = now;
        if (this._state === 'idle' && now - this._holdSince >= this._holdDelay) {
            this._talking = true;
            this._call('HoldStart');
        }
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

    InsertAsync([text], invocation) {
        if (!this._daemonOwner || invocation.get_sender() !== this._daemonOwner) {
            invocation.return_dbus_error('dev.blurt.Shell.Error.AccessDenied',
                'Only the Blurt service can insert text');
            return;
        }
        try {
            invocation.return_value(new GLib.Variant('(s)', [this._insert(text)]));
        } catch (e) {
            invocation.return_dbus_error('dev.blurt.Shell.Error.Failed', e.message);
        }
    }

    _insert(text) {
        if (Main.inputMethod?.currentFocus) {
            Main.inputMethod.commit(text);
            return 'commit';
        }
        const keyboard = this._keyboard;
        const plain = text.normalize('NFD').replace(/\p{M}/gu, '')
            .replace(/[\u2018\u2019]/g, "'").replace(/[\u201c\u201d]/g, '"')
            .replace(/[\u2013\u2014]/g, '-').replace(/\u2026/g, '...');
        for (const char of plain) {
            const keyval = Clutter.unicode_to_keysym(char.codePointAt(0));
            keyboard.notify_keyval(GLib.get_monotonic_time(), keyval, Clutter.KeyState.PRESSED);
            keyboard.notify_keyval(GLib.get_monotonic_time(), keyval, Clutter.KeyState.RELEASED);
        }
        return 'keys';
    }

    _onDestroy() {
        this._stopHoldWatch();
        if (this._shellNameId) {
            Gio.bus_unown_name(this._shellNameId);
            this._shellNameId = 0;
        }
        this._exported?.unexport();
        this._exported = null;
        this._keyboard = null;
        if (this._messageSignalId) {
            Gio.DBus.session.signal_unsubscribe(this._messageSignalId);
            this._messageSignalId = 0;
        }
        if (this._holdSignalId) {
            Gio.DBus.session.signal_unsubscribe(this._holdSignalId);
            this._holdSignalId = 0;
        }
        if (this._signalId) {
            Gio.DBus.session.signal_unsubscribe(this._signalId);
            this._signalId = 0;
        }
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
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

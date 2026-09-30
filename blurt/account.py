import ast
import subprocess
from dataclasses import dataclass

from blurt.daemon import BUS_NAME, INTERFACE, OBJECT_PATH

SERVICE = "blurt.service"
EXTENSION_UUID = "blurt@dev.blurt"
KEYBINDINGS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEYBINDING_SCHEMA = f"{KEYBINDINGS_SCHEMA}.custom-keybinding"
KEYBINDING_ROOT = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
SHORTCUT_NAMES = {"toggle": "Blurt: start/stop", "cancel": "Blurt: cancel"}
SHORTCUT_METHODS = {"toggle": "Toggle", "cancel": "Cancel"}
DEFAULT_SHORTCUTS = {"toggle": "<Super>d", "cancel": "<Super><Shift>d"}


@dataclass
class Status:
    service_enabled: bool
    service_active: bool
    extension_enabled: bool
    extension_loaded: bool
    shortcuts: dict[str, str]

    @property
    def complete(self) -> bool:
        return self.service_enabled and self.extension_enabled and all(self.shortcuts.values())


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


def gsettings(*args: str) -> str:
    return subprocess.run(
        ["gsettings", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def keybinding_paths() -> list[str]:
    raw = gsettings("get", KEYBINDINGS_SCHEMA, "custom-keybindings")
    return list(ast.literal_eval(raw.removeprefix("@as "))) if raw else []


def keybinding_path(name: str) -> str:
    return f"{KEYBINDING_ROOT}/blurt-{name}/"


def shortcut(name: str) -> str:
    if keybinding_path(name) not in keybinding_paths():
        return ""
    raw = gsettings("get", f"{KEYBINDING_SCHEMA}:{keybinding_path(name)}", "binding")
    return raw.strip("'")


def gdbus_command(method: str) -> str:
    return (
        f"gdbus call --session --dest {BUS_NAME} --object-path {OBJECT_PATH} "
        f"--method {INTERFACE}.{method}"
    )


def set_shortcut(name: str, binding: str) -> None:
    path = keybinding_path(name)
    schema = f"{KEYBINDING_SCHEMA}:{path}"
    gsettings("set", schema, "name", SHORTCUT_NAMES[name])
    gsettings("set", schema, "command", gdbus_command(SHORTCUT_METHODS[name]))
    gsettings("set", schema, "binding", binding)
    paths = keybinding_paths()
    if path not in paths:
        gsettings("set", KEYBINDINGS_SCHEMA, "custom-keybindings", str([*paths, path]))


def remove_shortcuts() -> None:
    paths = keybinding_paths()
    ours = [keybinding_path(name) for name in SHORTCUT_NAMES]
    for path in ours:
        run("gsettings", "reset-recursively", f"{KEYBINDING_SCHEMA}:{path}")
    gsettings(
        "set", KEYBINDINGS_SCHEMA, "custom-keybindings", str([p for p in paths if p not in ours])
    )


def extension_state() -> tuple[bool, bool]:
    info = run("gnome-extensions", "info", EXTENSION_UUID).stdout
    enabled = "Enabled: Yes" in info
    loaded = "State: ACTIVE" in info or "State: ENABLED" in info
    return enabled, loaded


def status() -> Status:
    extension_enabled, extension_loaded = extension_state()
    return Status(
        service_enabled=run("systemctl", "--user", "is-enabled", SERVICE).stdout.strip()
        == "enabled",
        service_active=run("systemctl", "--user", "is-active", SERVICE).stdout.strip() == "active",
        extension_enabled=extension_enabled,
        extension_loaded=extension_loaded,
        shortcuts={name: shortcut(name) for name in SHORTCUT_NAMES},
    )


def set_up(bindings: dict[str, str] | None = None) -> Status:
    bindings = bindings or {}
    for name in SHORTCUT_NAMES:
        set_shortcut(name, bindings.get(name) or shortcut(name) or DEFAULT_SHORTCUTS[name])
    run("systemctl", "--user", "daemon-reload")
    run("systemctl", "--user", "enable", "--now", SERVICE)
    run("gnome-extensions", "enable", EXTENSION_UUID)
    return status()


def tear_down() -> None:
    run("systemctl", "--user", "disable", "--now", SERVICE)
    run("gnome-extensions", "disable", EXTENSION_UUID)
    remove_shortcuts()

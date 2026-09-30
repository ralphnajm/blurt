import subprocess
import time

from gi.repository import Gio

from blurt.config import Config
from blurt.portal import RemoteKeyboard

TYPOGRAPHY = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": " - ",
        "…": "...",
        " ": " ",
    }
)
SPECIAL_KEYSYMS = {"\n": 0xFF0D, "\t": 0xFF09}
NAMED_KEYSYMS = {
    "ctrl": 0xFFE3,
    "shift": 0xFFE1,
    "alt": 0xFFE9,
    "super": 0xFFEB,
    "insert": 0xFF63,
    "enter": 0xFF0D,
}


def is_typeable(text: str) -> bool:
    return all(ch in SPECIAL_KEYSYMS or 0x20 <= ord(ch) <= 0x7E for ch in text)


def char_keysym(ch: str) -> int:
    if ch in SPECIAL_KEYSYMS:
        return SPECIAL_KEYSYMS[ch]
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x01000000 + cp


def combo_keysyms(combo: str) -> list[int]:
    keys = []
    for part in combo.lower().split("+"):
        part = part.strip()
        keys.append(NAMED_KEYSYMS.get(part, ord(part) if len(part) == 1 else 0))
    if 0 in keys:
        raise ValueError(f"Unknown key in paste_keys: {combo!r}")
    return keys


class Injector:
    def __init__(self, config: Config, bus: Gio.DBusConnection) -> None:
        self._config = config
        self._bus = bus

    def authorize(self) -> None:
        with RemoteKeyboard(self._bus) as keyboard:
            keyboard.keysym(NAMED_KEYSYMS["shift"], True)
            keyboard.keysym(NAMED_KEYSYMS["shift"], False)

    def inject(self, text: str) -> str:
        text = text.translate(TYPOGRAPHY)
        mode = self._config.inject
        if mode == "auto":
            mode = "type" if is_typeable(text) else "paste"
        with RemoteKeyboard(self._bus) as keyboard:
            if mode == "type":
                self._type(keyboard, text)
            else:
                self._paste(keyboard, text)
        return mode

    def copy(self, text: str) -> None:
        subprocess.run(["wl-copy", "--", text], check=False, timeout=5)

    def _type(self, keyboard: RemoteKeyboard, text: str) -> None:
        delay = self._config.type_delay_ms / 1000
        for ch in text:
            keysym = char_keysym(ch)
            keyboard.keysym(keysym, True)
            keyboard.keysym(keysym, False)
            if delay:
                time.sleep(delay)

    def _paste(self, keyboard: RemoteKeyboard, text: str) -> None:
        previous = self._read_clipboard()
        subprocess.run(["wl-copy", "--", text], check=True, timeout=5)
        time.sleep(0.05)
        keys = combo_keysyms(self._config.paste_keys)
        for key in keys:
            keyboard.keysym(key, True)
        for key in reversed(keys):
            keyboard.keysym(key, False)
        time.sleep(0.3)
        if previous is not None:
            subprocess.run(["wl-copy", "--", previous], check=False, timeout=5)

    def _read_clipboard(self) -> str | None:
        try:
            types = subprocess.run(
                ["wl-paste", "--list-types"], capture_output=True, text=True, timeout=2
            )
            if "text/plain" not in types.stdout:
                return None
            result = subprocess.run(
                ["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=2
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout if result.returncode == 0 else None

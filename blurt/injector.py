import re
import subprocess

from gi.repository import Gio, GLib

SHELL_BUS_NAME = "dev.blurt.Shell"
SHELL_OBJECT_PATH = "/dev/blurt/Shell"
SHELL_INTERFACE = "dev.blurt.Shell"
LINE_BREAKS = re.compile(r"\s*[\r\n]+\s*")


class ShellUnavailable(RuntimeError):
    pass


def single_line(text: str) -> str:
    return LINE_BREAKS.sub(" ", text)


class Injector:
    def __init__(self, bus: Gio.DBusConnection) -> None:
        self._bus = bus

    def inject(self, text: str) -> str:
        try:
            reply = self._bus.call_sync(
                SHELL_BUS_NAME,
                SHELL_OBJECT_PATH,
                SHELL_INTERFACE,
                "Insert",
                GLib.Variant("(s)", (single_line(text),)),
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
        except GLib.Error as exc:
            if "ServiceUnknown" in exc.message or "not provided" in exc.message:
                raise ShellUnavailable(
                    "The Blurt top bar extension isn't running. Log out and back in once."
                ) from exc
            raise
        return str(reply.unpack()[0])

    def copy(self, text: str) -> None:
        subprocess.run(["wl-copy", "--", text], check=False, timeout=5)

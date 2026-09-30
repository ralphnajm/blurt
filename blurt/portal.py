import threading
import uuid
from pathlib import Path
from typing import Any

from gi.repository import Gio, GLib

from blurt.config import STATE_DIR

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
KEYBOARD = 1
PERSIST_UNTIL_REVOKED = 2
TOKEN_PATH = STATE_DIR / "portal_restore_token"


class PortalError(RuntimeError):
    pass


class RemoteKeyboard:
    def __init__(self, bus: Gio.DBusConnection, token_path: Path = TOKEN_PATH) -> None:
        self._bus = bus
        self._token_path = token_path
        self._session: str | None = None

    def __enter__(self) -> "RemoteKeyboard":
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def open(self) -> None:
        created = self._request(
            "CreateSession",
            lambda opts: GLib.Variant("(a{sv})", (opts,)),
            {"session_handle_token": self._variant_token()},
        )
        self._session = str(created["session_handle"])
        select: dict[str, GLib.Variant] = {
            "types": GLib.Variant("u", KEYBOARD),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        restore = self._read_token()
        if restore:
            select["restore_token"] = GLib.Variant("s", restore)
        self._request(
            "SelectDevices", lambda opts: GLib.Variant("(oa{sv})", (self._session, opts)), select
        )
        started = self._request(
            "Start",
            lambda opts: GLib.Variant("(osa{sv})", (self._session, "", opts)),
            {},
            timeout=300,
        )
        if int(started.get("devices", 0)) & KEYBOARD == 0:
            raise PortalError("Keyboard access was not granted")
        token = started.get("restore_token")
        if token:
            self._write_token(str(token))

    def close(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        try:
            self._bus.call_sync(
                BUS_NAME, session, SESSION, "Close", None, None, Gio.DBusCallFlags.NONE, 2000, None
            )
        except GLib.Error:
            pass

    def keysym(self, keysym: int, pressed: bool) -> None:
        if self._session is None:
            raise PortalError("Remote desktop session is not open")
        self._bus.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            REMOTE_DESKTOP,
            "NotifyKeyboardKeysym",
            GLib.Variant("(oa{sv}iu)", (self._session, {}, keysym, 1 if pressed else 0)),
            None,
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )

    def _request(
        self,
        method: str,
        build: Any,
        options: dict[str, GLib.Variant],
        timeout: float = 30,
    ) -> dict[str, Any]:
        token = f"blurt_{uuid.uuid4().hex}"
        sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        path = f"{OBJECT_PATH}/request/{sender}/{token}"
        done = threading.Event()
        reply: dict[str, Any] = {}

        def on_response(
            _c: object, _s: object, _p: object, _i: object, _n: object, params: GLib.Variant
        ) -> None:
            code, results = params.unpack()
            reply["code"] = code
            reply["results"] = results
            done.set()

        sub = self._bus.signal_subscribe(
            BUS_NAME, REQUEST, "Response", path, None, Gio.DBusSignalFlags.NONE, on_response
        )
        try:
            self._bus.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                REMOTE_DESKTOP,
                method,
                build({**options, "handle_token": GLib.Variant("s", token)}),
                None,
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            if not done.wait(timeout):
                raise PortalError(f"Portal {method} timed out")
        finally:
            self._bus.signal_unsubscribe(sub)
        if reply["code"] != 0:
            raise PortalError(f"Portal {method} was denied or failed (response {reply['code']})")
        return reply["results"]

    def _variant_token(self) -> GLib.Variant:
        return GLib.Variant("s", f"blurt_{uuid.uuid4().hex}")

    def _read_token(self) -> str | None:
        try:
            return self._token_path.read_text().strip() or None
        except FileNotFoundError:
            return None

    def _write_token(self, token: str) -> None:
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(token)
        self._token_path.chmod(0o600)

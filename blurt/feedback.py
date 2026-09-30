import subprocess

from blurt.config import Config

SOUNDS = {"start": "device-added", "stop": "device-removed", "error": "dialog-error"}
ICON = "dev.blurt.Blurt"


class Feedback:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._notification_id: str | None = None

    def sound(self, name: str) -> None:
        if not self._config.sounds:
            return
        try:
            subprocess.Popen(
                ["canberra-gtk-play", "-i", SOUNDS[name]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    def notify(
        self, summary: str, body: str = "", timeout_ms: int = 0, urgent: bool = False
    ) -> None:
        if not self._config.notifications:
            return
        args = [
            "notify-send",
            "--app-name=Blurt",
            f"--icon={ICON}",
            "--print-id",
            "--hint=int:transient:1",
            f"--urgency={'critical' if urgent else 'low'}",
        ]
        if timeout_ms:
            args.append(f"--expire-time={timeout_ms}")
        if self._notification_id:
            args.append(f"--replace-id={self._notification_id}")
        args += ["--", summary, body]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return
        self._notification_id = result.stdout.strip() or self._notification_id

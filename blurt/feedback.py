import subprocess

from blurt.config import Config

SOUNDS = {"start": "device-added", "stop": "device-removed", "error": "dialog-error"}


class Feedback:
    def __init__(self, config: Config) -> None:
        self._config = config

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

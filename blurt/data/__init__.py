from pathlib import Path

DATA_DIR = Path(__file__).parent
ICONS_DIR = DATA_DIR / "icons"
APP_ICON = ICONS_DIR / "dev.blurt.Blurt.png"
EXTENSION_UUID = "blurt@dev.blurt"
EXTENSION_DIR = DATA_DIR / "gnome-shell" / EXTENSION_UUID

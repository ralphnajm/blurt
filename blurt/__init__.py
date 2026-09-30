from importlib.metadata import PackageNotFoundError, version

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Secret", "1")
gi.require_version("PangoCairo", "1.0")

try:
    __version__ = version("blurt")
except PackageNotFoundError:
    __version__ = "0+unknown"

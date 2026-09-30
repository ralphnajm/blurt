import sys
import tomllib
from pathlib import Path


def main() -> None:
    lock = tomllib.loads(Path(sys.argv[1]).read_text())
    for package in sorted(lock["package"], key=lambda p: p["name"]):
        if "main" not in package.get("groups", ["main"]):
            continue
        markers = package.get("markers")
        if isinstance(markers, dict):
            markers = markers.get("main")
        line = f"{package['name']}=={package['version']}"
        print(f"{line} ; {markers}" if markers else line)


if __name__ == "__main__":
    main()

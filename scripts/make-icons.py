from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "blurt/data/icons/dev.blurt.Blurt.png"
OUT = ROOT / "packaging/icons/hicolor"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def main() -> None:
    master = Image.open(MASTER).convert("RGBA")
    for size in SIZES:
        target = OUT / f"{size}x{size}/apps/dev.blurt.Blurt.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        master.resize((size, size), Image.Resampling.LANCZOS).save(target, optimize=True)
        print(target.relative_to(ROOT))


if __name__ == "__main__":
    main()

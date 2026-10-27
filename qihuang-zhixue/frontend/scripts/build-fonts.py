"""Build self-hosted Chinese font subsets for the story module.

Scans every character that can reach the screen (frontend sources plus the
backend strings that are rendered verbatim), then subsets the installed
Noto variable fonts into woff2 files under ``frontend/public/fonts``.

Run from the ``frontend`` directory:

    python scripts/build-fonts.py

The output is committed so the app never depends on a CDN or on the viewer's
machine having a Chinese font installed.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

FRONTEND = pathlib.Path(__file__).resolve().parents[1]
PROJECT = FRONTEND.parent
OUT_DIR = FRONTEND / "public" / "fonts"

SCAN_SUFFIXES = {".vue", ".ts", ".scss", ".css", ".html", ".mjs"}
SCAN_EXTRA = (
    FRONTEND / "index.html",
)
BACKEND_GLOBS = (
    "backend/data/herbs/*.json",
    "backend/app/profile/modes.py",
    "backend/app/profile/schema.py",
    "backend/app/profile/narrative.py",
)

# Fonts that ship with Windows. Noto is OFL, so subsetting and redistributing
# the derived woff2 is allowed.
FAMILIES = (
    ("NotoSerifSC-VF.ttf", "noto-serif-sc-subset.woff2"),
    ("NotoSansSC-VF.ttf", "noto-sans-sc-subset.woff2"),
)
FONT_DIRS = (
    pathlib.Path("C:/Windows/Fonts"),
    pathlib.Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    pathlib.Path("/usr/share/fonts"),
    pathlib.Path("/System/Library/Fonts"),
)

# Always keep the full punctuation / symbol ranges the UI relies on.
ALWAYS_RANGES = (
    (0x0020, 0x007E),  # ASCII
    (0x00A0, 0x00FF),  # Latin-1 supplement
    (0x2010, 0x203B),  # dashes, quotes, ellipsis, bullets
    (0x2460, 0x24FF),  # circled numbers
    (0x3000, 0x303F),  # CJK punctuation
    (0xFF00, 0xFFEF),  # fullwidth forms
    (0x25A0, 0x25FF),  # geometric shapes (carets, diamonds)
)


def collect_chars() -> set[str]:
    chars: set[str] = set()
    for path in FRONTEND.joinpath("src").rglob("*"):
        if path.suffix in SCAN_SUFFIXES and path.is_file():
            chars |= set(path.read_text(encoding="utf-8"))
    for path in SCAN_EXTRA:
        if path.is_file():
            chars |= set(path.read_text(encoding="utf-8"))
    for pattern in BACKEND_GLOBS:
        for path in PROJECT.glob(pattern):
            try:
                chars |= set(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return chars


def find_font(name: str) -> pathlib.Path | None:
    for directory in FONT_DIRS:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def build_unicodes(chars: set[str]) -> str:
    points: set[int] = set()
    for char in chars:
        points.add(ord(char))
    for start, end in ALWAYS_RANGES:
        points.update(range(start, end + 1))
    points.discard(0)
    return ",".join(f"U+{point:04X}" for point in sorted(points))


def run_subset(source: pathlib.Path, target: pathlib.Path, unicodes: str) -> None:
    command = [
        sys.executable,
        "-m",
        "fontTools.subset",
        str(source),
        f"--unicodes={unicodes}",
        "--layout-features=*",
        "--flavor=woff2",
        "--no-hinting",
        "--desubroutinize",
        f"--output-file={target}",
    ]
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    chars = collect_chars()
    cjk = {char for char in chars if "\u4e00" <= char <= "\u9fff"}
    unicodes = build_unicodes(chars)
    print(f"scanned {len(chars)} unique chars ({len(cjk)} CJK)")

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    missing = []
    for source_name, target_name in FAMILIES:
        source = find_font(source_name)
        if source is None:
            missing.append(source_name)
            continue
        target = OUT_DIR / target_name
        run_subset(source, target, unicodes)
        print(f"{source_name} -> {target.relative_to(FRONTEND)} ({target.stat().st_size / 1024:.0f} KB)")

    if missing:
        print(f"missing source fonts: {', '.join(missing)}", file=sys.stderr)
        return 1
    if shutil.which("python") is None:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build a ready-to-configure ZIP distribution for Windows and Linux."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = f"jira-analytics"
DIST = ROOT / "dist"
STAGE = DIST / NAME
INCLUDE = [
    "libs/__init__.py",
    "libs/main.py",
    "libs/utils",
    "config/autorun.bat",
    "config/autorun.sh",
    "config/build.py",
    "config.example.json",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    ".gitignore",
]


def main() -> None:
    """Create dist/jira-analytics-1.1.0.zip with app, config and launch scripts."""
    DIST.mkdir(parents=True, exist_ok=True)
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    for item in INCLUDE:
        source = ROOT / item
        target = STAGE / item
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    shutil.copy2(STAGE / "config.example.json", STAGE / "config.json")
    archive = DIST / f"{NAME}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(DIST))
    print(f"Distribution created: {archive}")


if __name__ == "__main__":
    main()

"""PyInstaller build automation for individual Jaiho Super Simple Office executables."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.version import (  # noqa: E402
    APP_REGISTRY,
    BUILD_YEAR,
    ORGANIZATION_NAME,
    SUITE_NAME,
    SUITE_VERSION,
)


@dataclass(frozen=True, slots=True)
class BuildTarget:
    """Build specification for a single executable."""

    app_id: str
    display_name: str
    exe_name: str
    entry_script: Path
    heavy_excludes: tuple[str, ...]


# Heavy libraries excluded from lightweight utilities to keep startup fast and footprint small
_HEAVY_DOC_AND_VISION_MODULES: tuple[str, ...] = (
    "cv2",
    "fitz",
    "pypdf",
    "docx",
    "openpyxl",
    "pptx",
    "formulas",
    "matplotlib",
    "scipy",
    "pytesseract",
    "PIL",
    "win32com",
    "comtypes",
    "icalendar",
)

_APP_EXCLUDES: dict[str, tuple[str, ...]] = {
    "foundation_demo": _HEAVY_DOC_AND_VISION_MODULES,
    "jaiho_office": _HEAVY_DOC_AND_VISION_MODULES,
    "simple_calculator": _HEAVY_DOC_AND_VISION_MODULES,
    "simple_units": _HEAVY_DOC_AND_VISION_MODULES,
    "simple_notes": _HEAVY_DOC_AND_VISION_MODULES,
    "simple_focus": _HEAVY_DOC_AND_VISION_MODULES,
    "simple_tasks": _HEAVY_DOC_AND_VISION_MODULES,
    "simple_calendar": tuple(m for m in _HEAVY_DOC_AND_VISION_MODULES if m != "icalendar"),
    "simple_writer": (
        "cv2",
        "openpyxl",
        "pptx",
        "formulas",
        "matplotlib",
        "pytesseract",
        "win32com",
        "comtypes",
        "icalendar",
    ),
    "simple_sheets": (
        "cv2",
        "fitz",
        "pypdf",
        "docx",
        "pptx",
        "pytesseract",
        "win32com",
        "comtypes",
        "icalendar",
    ),
    "simple_slides": (
        "cv2",
        "openpyxl",
        "docx",
        "formulas",
        "matplotlib",
        "pytesseract",
        "win32com",
        "comtypes",
        "icalendar",
    ),
    "simple_pdf": (
        "cv2",
        "docx",
        "openpyxl",
        "pptx",
        "formulas",
        "matplotlib",
        "pytesseract",
        "win32com",
        "comtypes",
        "icalendar",
    ),
    "simple_scan": (
        "docx",
        "openpyxl",
        "pptx",
        "formulas",
        "matplotlib",
        "icalendar",
    ),
    "simple_ocr": (
        "docx",
        "openpyxl",
        "pptx",
        "formulas",
        "matplotlib",
        "win32com",
        "comtypes",
        "icalendar",
    ),
    "simple_convert": (
        "win32com",
        "comtypes",
        "icalendar",
    ),
}


def resolve_build_target(app_id: str) -> BuildTarget:
    """Resolve entry script, executable name, and dependency exclusions for an app_id."""
    if app_id == "foundation_demo":
        return BuildTarget(
            app_id="foundation_demo",
            display_name="Jaiho Foundation Verification",
            exe_name="FoundationDemo",
            entry_script=ROOT_DIR / "apps" / "foundation_demo" / "main.py",
            heavy_excludes=_APP_EXCLUDES["foundation_demo"],
        )

    if app_id not in APP_REGISTRY:
        valid = ", ".join(["foundation_demo", *APP_REGISTRY.keys()])
        raise ValueError(f"Unknown app_id '{app_id}'. Valid targets: {valid}")

    meta = APP_REGISTRY[app_id]
    exe_stem = Path(meta.executable).stem
    entry_script = (
        ROOT_DIR / "launcher" / "main.py"
        if app_id == "jaiho_office"
        else ROOT_DIR / "apps" / app_id / "main.py"
    )
    return BuildTarget(
        app_id=app_id,
        display_name=meta.display_name,
        exe_name=exe_stem,
        entry_script=entry_script,
        heavy_excludes=_APP_EXCLUDES.get(app_id, ()),
    )


def _version_tuple(version_str: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in version_str.split(".") if p.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def _write_windows_version_file(target: BuildTarget, work_dir: Path) -> Path:
    """Generate a PyInstaller VSVersionInfo file so the .exe has valid Windows Properties."""
    v1, v2, v3, v4 = _version_tuple(SUITE_VERSION)
    version_file = work_dir / f"{target.exe_name}_version_info.txt"
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v1}, {v2}, {v3}, {v4}),
    prodvers=({v1}, {v2}, {v3}, {v4}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [
            StringStruct(u'CompanyName', u'{ORGANIZATION_NAME}'),
            StringStruct(u'FileDescription', u'{target.display_name}'),
            StringStruct(u'FileVersion', u'{SUITE_VERSION}'),
            StringStruct(u'InternalName', u'{target.exe_name}'),
            StringStruct(u'LegalCopyright', u'Copyright (C) {BUILD_YEAR} {ORGANIZATION_NAME}. All rights reserved.'),
            StringStruct(u'OriginalFilename', u'{target.exe_name}.exe'),
            StringStruct(u'ProductName', u'{SUITE_NAME}'),
            StringStruct(u'ProductVersion', u'{SUITE_VERSION}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    version_file.write_text(content, encoding="utf-8")
    return version_file


def _folder_size_mb(folder: Path) -> float:
    total = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def build_single_app(app_id: str, clean: bool = True) -> Path:
    """Package a single application into dist/individual/<ExeName>/<ExeName>.exe."""
    target = resolve_build_target(app_id)
    if not target.entry_script.exists():
        raise FileNotFoundError(f"Entry script not found: {target.entry_script}")

    dist_root = ROOT_DIR / "dist" / "individual"
    work_dir = ROOT_DIR / "build" / "individual" / target.exe_name
    spec_dir = ROOT_DIR / "build" / "specs"

    if clean:
        target_out = dist_root / target.exe_name
        if target_out.exists():
            shutil.rmtree(target_out)
        if work_dir.exists():
            shutil.rmtree(work_dir)

    dist_root.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    version_file = _write_windows_version_file(target, work_dir)
    resources_dir = ROOT_DIR / "resources"
    add_data_arg = f"{resources_dir}{os.pathsep}resources"

    cmd: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        f"--name={target.exe_name}",
        f"--distpath={dist_root}",
        f"--workpath={work_dir}",
        f"--specpath={spec_dir}",
        f"--paths={ROOT_DIR}",
        f"--add-data={add_data_arg}",
        f"--version-file={version_file}",
        # Exclude test frameworks & unused Qt bindings
        "--exclude-module=pytest",
        "--exclude-module=pytestqt",
        "--exclude-module=tkinter",
        "--exclude-module=PyQt5",
        "--exclude-module=PyQt6",
    ]

    for module_name in target.heavy_excludes:
        cmd.append(f"--exclude-module={module_name}")

    # Attach .ico if present
    for icon_candidate in (
        resources_dir / "icons" / f"{target.app_id}.ico",
        resources_dir / "icons" / "jaiho_office.ico",
    ):
        if icon_candidate.exists():
            cmd.append(f"--icon={icon_candidate}")
            break

    cmd.append(str(target.entry_script))

    print(f"\n=== Building {target.display_name} ({target.exe_name}.exe) ===")
    result = subprocess.run(cmd, cwd=str(ROOT_DIR), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller failed for '{app_id}' with exit code {result.returncode}.")

    exe_path = dist_root / target.exe_name / f"{target.exe_name}.exe"
    if not exe_path.exists():
        raise FileNotFoundError(f"Expected executable was not produced: {exe_path}")

    size_mb = _folder_size_mb(exe_path.parent)
    print(f"SUCCESS: {exe_path}  (Bundle size: {size_mb:.1f} MB)")
    return exe_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build standalone Windows executables for Jaiho Super Simple Office."
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Application IDs to build (e.g., foundation_demo simple_calculator) or 'all'.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Reuse existing PyInstaller build cache instead of cleaning first.",
    )
    args = parser.parse_args(argv)

    requested = list(APP_REGISTRY.keys()) if "all" in args.targets else args.targets
    for app_id in requested:
        build_single_app(app_id=app_id, clean=not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
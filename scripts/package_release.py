"""Release packager for Jaiho Super Simple Office — creates ZIP archives & SHA256SUMS.txt."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.version import (  # noqa: E402
    APP_REGISTRY,
    ORGANIZATION_NAME,
    SUITE_NAME,
    SUITE_VERSION,
)
from scripts.build_app import build_single_app  # noqa: E402


def _sha256_file(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with file_path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _zip_directory(source_dir: Path, zip_path: Path, arc_prefix: str = "") -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in sorted(source_dir.rglob("*")):
            if item.is_file():
                rel = item.relative_to(source_dir)
                arcname = f"{arc_prefix}/{rel.as_posix()}" if arc_prefix else rel.as_posix()
                zf.write(item, arcname=arcname)
    return zip_path


def package_releases(
    build_missing: bool = False,
    clean: bool = True,
    platform_tag: str = "win64",
) -> Path:
    """Create individual app ZIPs, full-suite ZIP, release_manifest.json, and SHA256SUMS.txt."""
    dist_individual = ROOT_DIR / "dist" / "individual"
    release_dir = ROOT_DIR / "dist" / "release"

    if clean and release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    # Optionally build any missing targets first
    if build_missing:
        for app_id, meta in APP_REGISTRY.items():
            exe_stem = Path(meta.executable).stem
            expected_exe = dist_individual / exe_stem / meta.executable
            if not expected_exe.exists():
                build_single_app(app_id=app_id, clean=True)

    archived_entries: list[dict[str, object]] = []
    built_dirs: list[tuple[str, Path]] = []

    for app_id, meta in APP_REGISTRY.items():
        exe_stem = Path(meta.executable).stem
        app_folder = dist_individual / exe_stem
        exe_file = app_folder / meta.executable
        if not exe_file.exists():
            print(f"SKIP: {meta.executable} not built yet ({app_folder})")
            continue

        built_dirs.append((exe_stem, app_folder))
        zip_name = f"JaihoSuperSimpleOffice-{exe_stem}-v{SUITE_VERSION}-{platform_tag}.zip"
        zip_path = release_dir / zip_name
        _zip_directory(app_folder, zip_path, arc_prefix=exe_stem)

        digest = _sha256_file(zip_path)
        size_mb = round(zip_path.stat().st_size / (1024 * 1024), 2)
        archived_entries.append(
            {
                "app_id": app_id,
                "display_name": meta.display_name,
                "executable": meta.executable,
                "category": meta.category,
                "archive": zip_name,
                "size_mb": size_mb,
                "sha256": digest,
            }
        )
        print(f"PACKAGED: {zip_name} ({size_mb:.2f} MB)")

    if not archived_entries:
        raise FileNotFoundError(
            "No built executables found in dist/individual/. "
            "Run `python scripts/build_app.py all` or pass `--build-missing`."
        )

    # Build unified Full-Suite ZIP containing all built folders side-by-side
    suite_zip_name = f"JaihoSuperSimpleOffice-FullSuite-v{SUITE_VERSION}-{platform_tag}.zip"
    suite_zip_path = release_dir / suite_zip_name
    with zipfile.ZipFile(
        suite_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as zf:
        readme_file = ROOT_DIR / "README.md"
        if readme_file.exists():
            zf.write(readme_file, arcname="JaihoSuperSimpleOffice/README.md")

        for exe_stem, app_folder in built_dirs:
            for item in sorted(app_folder.rglob("*")):
                if item.is_file():
                    rel = item.relative_to(app_folder)
                    zf.write(
                        item,
                        arcname=f"JaihoSuperSimpleOffice/{exe_stem}/{rel.as_posix()}",
                    )

    suite_digest = _sha256_file(suite_zip_path)
    suite_size_mb = round(suite_zip_path.stat().st_size / (1024 * 1024), 2)
    print(f"PACKAGED: {suite_zip_name} ({suite_size_mb:.2f} MB)")

    # Write SHA256SUMS.txt
    checksum_lines = [f"{suite_digest}  {suite_zip_name}"]
    for entry in archived_entries:
        checksum_lines.append(f"{entry['sha256']}  {entry['archive']}")
    (release_dir / "SHA256SUMS.txt").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )

    # Write release_manifest.json
    manifest = {
        "suite_name": SUITE_NAME,
        "organization": ORGANIZATION_NAME,
        "version": SUITE_VERSION,
        "platform": platform_tag,
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "full_suite_archive": {
            "archive": suite_zip_name,
            "size_mb": suite_size_mb,
            "sha256": suite_digest,
        },
        "applications": archived_entries,
    }
    (release_dir / "release_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"\nRelease artifacts ready in: {release_dir}")
    return release_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Package Jaiho Super Simple Office builds into release ZIPs and SHA256SUMS."
    )
    parser.add_argument(
        "--build-missing",
        action="store_true",
        help="Automatically build any missing .exe targets before packaging.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Keep existing files in dist/release/ instead of cleaning first.",
    )
    args = parser.parse_args(argv)

    package_releases(build_missing=args.build_missing, clean=not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
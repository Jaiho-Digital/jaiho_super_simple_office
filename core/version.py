"""Centralized organization, suite, and application metadata for Jaiho Super Simple Office."""

from __future__ import annotations

from dataclasses import dataclass

ORGANIZATION_NAME: str = "Jaiho Digital"
ORGANIZATION_TAGLINE: str = "Jaiho Digital — Tech for Earth, Tech for All."

SUITE_NAME: str = "Jaiho Super Simple Office"
SUITE_TAGLINE: str = "Everything you need. Nothing you don't."
SUITE_VERSION: str = "0.1.0"
BUILD_YEAR: int = 2026

# Directory names used under %LOCALAPPDATA%
ORG_DATA_DIR_NAME: str = "JaihoDigital"
SUITE_DATA_DIR_NAME: str = "JaihoSuperSimpleOffice"


@dataclass(frozen=True, slots=True)
class AppMetadata:
    """Immutable metadata describing a single executable in the ecosystem."""

    app_id: str
    display_name: str
    executable: str
    version: str
    category: str
    description: str


APP_REGISTRY: dict[str, AppMetadata] = {
    "jaiho_office": AppMetadata(
        app_id="jaiho_office",
        display_name="Jaiho Super Simple Office",
        executable="JaihoOffice.exe",
        version=SUITE_VERSION,
        category="Hub",
        description="Central application launcher and workspace hub for Jaiho Super Simple Office.",
    ),
    "simple_pdf": AppMetadata(
        app_id="simple_pdf",
        display_name="Super Simple PDF",
        executable="SimplePDF.exe",
        version=SUITE_VERSION,
        category="Documents",
        description="Fast PDF viewer, annotator, and page organization tool.",
    ),
    "simple_writer": AppMetadata(
        app_id="simple_writer",
        display_name="SimpleWriter",
        executable="SimpleWriter.exe",
        version=SUITE_VERSION,
        category="Office",
        description="Clean, distraction-free rich-text word processor.",
    ),
    "simple_sheets": AppMetadata(
        app_id="simple_sheets",
        display_name="SimpleSheets",
        executable="SimpleSheets.exe",
        version=SUITE_VERSION,
        category="Office",
        description="Lightweight spreadsheet editor with formulas, filtering, and charts.",
    ),
    "simple_slides": AppMetadata(
        app_id="simple_slides",
        display_name="SimpleSlides",
        executable="SimpleSlides.exe",
        version=SUITE_VERSION,
        category="Office",
        description="Streamlined presentation designer and full-screen slideshow tool.",
    ),
    "simple_scan": AppMetadata(
        app_id="simple_scan",
        display_name="SimpleScan",
        executable="SimpleScan.exe",
        version=SUITE_VERSION,
        category="Documents",
        description="Windows scanner utility with multi-page PDF capture and optional OCR.",
    ),
    "simple_notes": AppMetadata(
        app_id="simple_notes",
        display_name="SimpleNotes",
        executable="SimpleNotes.exe",
        version=SUITE_VERSION,
        category="Productivity",
        description="Instant local-first notes with tags, pinning, and autosave.",
    ),
    "simple_tasks": AppMetadata(
        app_id="simple_tasks",
        display_name="SimpleTasks",
        executable="SimpleTasks.exe",
        version=SUITE_VERSION,
        category="Productivity",
        description="Focused task manager with priorities, deadlines, and reminders.",
    ),
    "simple_calendar": AppMetadata(
        app_id="simple_calendar",
        display_name="SimpleCalendar",
        executable="SimpleCalendar.exe",
        version=SUITE_VERSION,
        category="Productivity",
        description="Offline calendar and event scheduler with recurring reminders.",
    ),
    "simple_focus": AppMetadata(
        app_id="simple_focus",
        display_name="SimpleFocus",
        executable="SimpleFocus.exe",
        version=SUITE_VERSION,
        category="Productivity",
        description="Accurate focus and break timer with daily session statistics.",
    ),
    "simple_calculator": AppMetadata(
        app_id="simple_calculator",
        display_name="SimpleCalculator",
        executable="SimpleCalculator.exe",
        version=SUITE_VERSION,
        category="Utilities",
        description="Standard and scientific calculator with safe expression parsing.",
    ),
    "simple_units": AppMetadata(
        app_id="simple_units",
        display_name="SimpleUnits",
        executable="SimpleUnits.exe",
        version=SUITE_VERSION,
        category="Utilities",
        description="High-precision unit converter across length, mass, energy, data, and more.",
    ),
    "simple_convert": AppMetadata(
        app_id="simple_convert",
        display_name="SimpleConvert",
        executable="SimpleConvert.exe",
        version=SUITE_VERSION,
        category="Utilities",
        description="Batch file converter powered by verified local conversion engines.",
    ),
    "simple_ocr": AppMetadata(
        app_id="simple_ocr",
        display_name="SimpleOCR",
        executable="SimpleOCR.exe",
        version=SUITE_VERSION,
        category="Documents",
        description="Offline optical character recognition for images and scanned pages.",
    ),
}


def get_app_metadata(app_id: str) -> AppMetadata:
    """Return metadata for a registered application ID, or a fallback descriptor."""
    if app_id in APP_REGISTRY:
        return APP_REGISTRY[app_id]
    return AppMetadata(
        app_id=app_id,
        display_name=app_id.replace("_", " ").title(),
        executable=f"{app_id}.exe",
        version=SUITE_VERSION,
        category="Utility",
        description="Jaiho Super Simple Office application.",
    )
# Jaiho Super Simple Office

**Calm, fast, 100% offline-first desktop productivity & office suite for Windows.**

Built by **Jaiho Digital** with Python and **PySide6**, **Jaiho Super Simple Office** delivers 14 standalone, independently installable Windows executables (`.exe`) plus a unified **JaihoOffice** launcher—designed in a warm **Soft Modern Pastel** visual language with zero cloud subscriptions, zero telemetry, and atomic local-first file persistence.

---

## Applications in the Suite

| Application | Executable | Category | Core Capabilities |
| :--- | :--- | :--- | :--- |
| **JaihoOffice** | `JaihoOffice.exe` | Suite Launcher | Unified launcher with category filtering, instant search, and process orchestration for every app in the suite. |
| **SimpleWriter** | `SimpleWriter.exe` | Documents | Multi-page paginated document editor (`.docx` default, `.md`, `.html`, `.txt`, `.pdf` export) with outline sidebar, floating selection toolbar, tables, and Zen mode. |
| **SimpleSheets** | `SimpleSheets.exe` | Documents | Multi-sheet spreadsheet studio (`.xlsx`, `.csv`, `.tsv`) with formula evaluation (`SUM`, `AVERAGE`, `IF`, `VLOOKUP`, etc.), conditional styling, and chart previews. |
| **SimpleSlides** | `SimpleSlides.exe` | Documents | Slide deck creator (`.pptx`, `.pdf` export) with pastel slide themes, speaker notes, reorderable filmstrip, and full-screen presenter mode. |
| **SimplePDF** | `SimplePDF.exe` | Documents | Offline PDF reader, page organizer (drag-reorder, rotate, duplicate, split), full-text search, and non-destructive highlight/note/stamp annotations. |
| **SimpleScan** | `SimpleScan.exe` | Documents | Document scanner studio supporting Windows WIA flatbed scanners, document cameras, image imports, auto-deskew, drag-to-crop, Magic Color cleanup, and multi-page PDF export. |
| **SimpleOCR** | `SimpleOCR.exe` | Documents | Offline text extraction from images and multi-page PDFs with custom ROI selection boxes, contrast boost, local OCR engines (NanoOCR / Windows Native WinRT / Tesseract), and `.txt`/`.md`/`.docx` export. |
| **SimpleNotes** | `SimpleNotes.exe` | Productivity | Fast local-first Markdown & rich note workspace backed by SQLite WAL with tags, instant full-text search, pinning, and archive management. |
| **SimpleTasks** | `SimpleTasks.exe` | Productivity | Keyboard-driven task & project planner backed by SQLite WAL with priorities, due dates, tags, subtasks, and smart views (*Today*, *Upcoming*, *Completed*). |
| **SimpleCalendar** | `SimpleCalendar.exe` | Productivity | Offline monthly/weekly/agenda calendar with recurring events, color categories, reminders, and `.ics` (iCalendar) import/export. |
| **SimpleFocus** | `SimpleFocus.exe` | Productivity | Pomodoro focus timer & deep-work session tracker with customizable intervals, ambient session logs, and daily streak statistics. |
| **SimpleCalculator** | `SimpleCalculator.exe` | Utilities | Standard, scientific, and programmer calculator with expression history tape, keyboard shortcuts, and instant result copy. |
| **SimpleUnits** | `SimpleUnits.exe` | Utilities | Instant offline converter across Length, Mass, Temperature, Area, Volume, Speed, Time, Digital Storage, Pressure, Energy, and Currency rates. |
| **SimpleConvert** | `SimpleConvert.exe` | Utilities | Drag-and-drop batch file converter across Documents (`.docx`, `.md`, `.html`, `.txt`, `.pdf`), Spreadsheets (`.xlsx`, `.csv`, `.tsv`, `.json`), and Images (`.png`, `.jpg`, `.webp`, `.bmp`, `.ico`). |
| **SimpleChat** | `SimpleChat.exe` | Office / LAN | Local same-network (LAN) chat, voice clip, and file sharing app with salted PBKDF2 username + password login, subnet/network policy checks, 1-on-1 DMs, and group rooms. |

---

## Architecture & Design Principles

* **100% Offline & Local-First:** All documents, SQLite databases (running in **WAL** mode), settings, and LAN relays stay strictly on your machine or local subnet. Zero external API keys or internet connections are required.
* **Atomic Crash-Safe File Writes (`core/file_manager/file_ops.py`):** Every save writes to a temporary file in the target directory, flushes to disk (`os.fsync`), creates an automatic timestamped backup in `%LOCALAPPDATA%\JaihoSuperSimpleOffice\backups`, and atomically replaces the target file (`os.replace`).
* **Unified `BaseWindow` & Soft Modern Pastel Design System (`core/ui/`, `core/themes/`):**
  * Warm alabaster/snow paper surfaces (`#F8F7F4` canvas, `#FFFEFC` surface) paired with soft pastel accents (Lavender `#E8E0F7`, Sage `#DDEFE8`, Peach `#F4E5D9`, Butter `#F8EBCF`, Blush `#F7DFE7`) and a matching dark theme.
  * Integrated frameless-ready header bar, toast notifications (`core/notifications/notifier.py`), persistent per-app window geometry/settings (`core/settings/`), and structured rotating logs (`core/logging/`).

---

## Repository Layout

```text
jaiho_super_simple_office/
├── apps/
│   ├── foundation_demo/      # Shared UI & theme verification harness
│   ├── simple_calculator/    # Standard, scientific & programmer calculator
│   ├── simple_calendar/      # Local calendar & .ics import/export
│   ├── simple_chat/          # Same-network LAN chat, groups, voice & file sharing
│   ├── simple_convert/       # Batch document, spreadsheet & image converter
│   ├── simple_focus/         # Pomodoro timer & deep-work session logger
│   ├── simple_notes/         # SQLite-backed local notes & Markdown studio
│   ├── simple_ocr/           # Offline image & PDF text extractor
│   ├── simple_pdf/           # PDF viewer, page organizer & annotator
│   ├── simple_scan/          # WIA/camera/image document scanner & PDF assembler
│   ├── simple_sheets/        # Multi-sheet spreadsheet & formula engine
│   ├── simple_slides/        # Presentation builder & presenter view
│   ├── simple_tasks/         # SQLite-backed task & project manager
│   ├── simple_units/         # Multi-category unit & currency converter
│   └── simple_writer/        # Paginated .docx / Markdown / PDF word processor
├── core/
│   ├── file_manager/         # Atomic writes, path validation, backup rotation
│   ├── logging/              # Rotating file & console logging setup
│   ├── notifications/        # Non-intrusive pastel toast banner system
│   ├── settings/             # JSON per-app settings persistence
│   ├── themes/               # Soft Modern Pastel light/dark tokens & QSS engine
│   ├── ui/                   # BaseWindow, dialogs, icons & reusable widgets
│   └── version.py            # Suite version & central APP_REGISTRY metadata
├── launcher/
│   └── main.py               # JaihoOffice.exe unified suite launcher
├── resources/
│   └── icons/                # Custom generated .ico icons for every executable
├── scripts/
│   ├── build_app.py          # PyInstaller standalone .exe build automation
│   └── package_release.py    # Release ZIP & SHA256SUMS.txt packager
├── site/
│   └── index.html            # Standalone static storefront & documentation page
└── tests/
    └── apps/                 # Automated pytest + pytest-qt unit & GUI suites
```

## Quick Start (Development)

### 1. Prerequisites
- Windows 10 / 11 (64-bit)
- Python 3.11+

### 2. Environment Setup
```
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Launch Any Application from Source
```
# Launch the unified suite hub
python -m launcher.main

# Or launch any individual app directly
python -m apps.simple_writer.main
python -m apps.simple_sheets.main
python -m apps.simple_slides.main
python -m apps.simple_pdf.main
python -m apps.simple_scan.main
python -m apps.simple_ocr.main
python -m apps.simple_notes.main
python -m apps.simple_tasks.main
python -m apps.simple_calendar.main
python -m apps.simple_focus.main
python -m apps.simple_calculator.main
python -m apps.simple_units.main
python -m apps.simple_convert.main
python -m apps.simple_chat.main
```

## Testing & Code Quality
Every application is covered by automated unit and GUI tests (`pytest + pytest-qt`) and linted with **Ruff:**
```
# Run the full automated test suite
pytest

# Run linter checks
ruff check .
```

## Building Standalone Windows Executables & Releases

### Build Individual `.exe` Bundles
```
# Build a single application into dist/individual/<AppName>/<AppName>.exe
python scripts/build_app.py simple_chat

# Build the launcher and all 14 standalone applications
python scripts/build_app.py all
```

## Package Distribution ZIP Archives & Checksums
```
# Package built executables into dist/release/ with SHA256SUMS.txt
python scripts/package_release.py
```

## License & Copyright
MIT. Copyright © 2026 Jaiho Digital. All rights reserved. 
<!--------------
Jaiho-Super-Simple-Office/
│
├── apps/
│   ├── foundation_demo/          # Phase 1 verification app (validates core + PyInstaller)
│   │   ├── __init__.py
│   │   └── main.py
│   ├── simple_pdf/
│   ├── simple_writer/
│   ├── simple_sheets/
│   ├── simple_slides/
│   ├── simple_scan/
│   ├── simple_notes/
│   ├── simple_tasks/
│   ├── simple_calendar/
│   ├── simple_focus/
│   ├── simple_calculator/
│   ├── simple_units/
│   ├── simple_convert/
│   └── simple_ocr/
│
├── core/
│   ├── __init__.py
│   ├── version.py                # Centralized suite & app version metadata
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── base_window.py        # Standard QMainWindow with menu, toolbar, statusbar & geometry persistence
│   │   ├── widgets.py            # Styled buttons, inputs, cards, section headers, empty-state views
│   │   ├── dialogs.py            # Standardized error, confirmation, about, and progress dialogs
│   │   └── toast.py              # Non-intrusive inline status/toast notifications
│   ├── themes/
│   │   ├── __init__.py
│   │   ├── tokens.py             # Light & Dark color palettes, spacing, radius, and font scales
│   │   └── theme_manager.py      # Dynamic QSS compiler and runtime theme switcher
│   ├── database/
│   │   ├── __init__.py
│   │   ├── db_manager.py         # SQLite connection factory (WAL, foreign keys, transactions)
│   │   └── migrations.py         # Schema version tracking and forward migration runner
│   ├── settings/
│   │   ├── __init__.py
│   │   └── settings_manager.py   # Thread-safe JSON settings & recent-files registry in %LOCALAPPDATA%
│   ├── logging/
│   │   ├── __init__.py
│   │   └── logger.py             # Rotating file logs + global sys.excepthook crash capture
│   ├── file_manager/
│   │   ├── __init__.py
│   │   └── file_ops.py           # Atomic saves, temp file lifecycle, path validation, backup rotation
│   ├── notifications/
│   │   ├── __init__.py
│   │   └── notifier.py           # System tray balloon/toast notifications & audio alert helper
│   └── utilities/
│       ├── __init__.py
│       ├── paths.py              # Frozen-aware resource resolver (PyInstaller _MEIPASS vs source)
│       └── workers.py            # Reusable QRunnable background task & cancellation wrapper
│
├── launcher/
│   ├── __init__.py
│   ├── main.py                   # JaihoOffice.exe entry point
│   ├── registry.py               # Metadata & executable discovery for all 13 apps
│   └── windows/
│       └── hub_window.py
│
├── resources/
│   ├── icons/                    # Monochrome/duotone SVG icon set + .ico app icons
│   ├── fonts/                    # Bundled Inter / JetBrains Mono TTFs (fallback to Segoe UI)
│   ├── images/                   # Jaiho Digital branding assets
│   └── styles/
│       └── base.qss              # Parameterized Qt Style Sheet template
│
├── installers/
│   ├── individual/               # Per-app Inno Setup (.iss) templates
│   └── bundle/                   # JaihoSuperSimpleOfficeSetup.iss (Complete/Custom installer)
│
├── scripts/
│   ├── build_app.py              # PyInstaller builder for single apps & Phase 1 demo
│   ├── build_bundle.py           # PyInstaller MERGE shared-runtime builder for all 14 EXEs
│   ├── package_installers.py     # Automates Inno Setup compilation
│   └── test.py                   # Pytest + Ruff + Mypy quality gate runner
│
├── tests/
│   ├── core/                     # Unit & pytest-qt tests for core infrastructure
│   └── apps/                     # Per-app functional & UI tests
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SUPPORTED_FORMATS.md
│   ├── APPLICATION_FEATURES.md
│   ├── BUILDING.md
│   ├── INSTALLATION.md
│   ├── DEVELOPMENT.md
│   ├── TROUBLESHOOTING.md
│   └── THIRD_PARTY_LICENSES.md
│
├── requirements.txt
├── pyproject.toml
├── README.md
└── CHANGELOG.md---------------->

# Jaiho Super Simple Office

> **Everything you need. Nothing you don't.**  
> A local-first, distraction-free suite of 13 lightweight Windows desktop tools + 1 unified launcher crafted by **Jaiho Digital**.

---

## Why We Built This

Modern office software has become bloated, slow to launch, locked behind recurring subscriptions, and dependent on cloud accounts just to edit a note, convert a unit, or merge a PDF.

**Jaiho Super Simple Office** takes the opposite approach:
- **100% Local-First & Offline:** Every file, note, calculation, and database stays locally on your machine in `%LOCALAPPDATA%\JaihoDigital\JaihoSuperSimpleOffice\`. Zero cloud sync required, zero accounts, zero telemetry.
- **Modular by Design:** Install the complete office bundle (`JaihoSuperSimpleOfficeSetup.exe`) with the central `JaihoOffice.exe` launcher, or download only the single `.exe` tool you actually need.
- **Handcrafted Visual Calm:** Built around our **Archival Paper** (Light) and **Obsidian Ink** (Dark) design system—warm paper surfaces, crisp hairline borders, high-contrast typography, and hardware-inspired tactile controls.
- **Fast & Crash-Safe:** Backed by SQLite in Write-Ahead Logging (WAL) mode, atomic file writes with automatic backup rotation, and non-blocking background worker threads.

---

## What Makes It Different

| Aspect | Traditional Office Suites | Jaiho Super Simple Office |
| :--- | :--- | :--- |
| **Architecture** | Massive all-in-one monoliths | 13 independent single-purpose `.exe` apps + unified launcher |
| **Privacy** | Mandatory sign-ins & cloud telemetry | 100% offline, zero tracking, local SQLite & standard files |
| **Pricing** | Monthly/annual subscriptions | Free & open-source |
| **Visual Design** | Cluttered ribbons & nested menus | Swiss-editorial minimalism with Archival Paper & Obsidian Ink themes |
| **Startup Speed** | Heavy background updaters & services | Instant launch; zero background daemons when closed |

---

## Application Catalog & Development Progress

We are building and shipping the suite sequentially. Status legend:
- **Available** — Feature-complete, tested with `pytest`, and ready to build as a standalone `.exe`.
- **In Progress** — Currently under active development.
- **Planned** — Scheduled in upcoming milestones.

| # | Application | Executable | Purpose | Status |
| :-: | :--- | :--- | :--- | :--- |
| 0 | **Jaiho Office Hub** | `JaihoOffice.exe` | Central launcher, recent files hub, and quick-access dock for all 13 apps | Planned (Phase 5) |
| 1 | **SimpleCalculator** | `SimpleCalculator.exe` | Standard & scientific calculator with safe AST math engine, `DEG`/`RAD` trig, memory registers, and calculation tape | **Available** |
| 2 | **SimpleUnits** | `SimpleUnits.exe` | Precision unit converter across 13 physical & digital categories with instant search, pinned pairs, and equivalence tables | **Available** |
| 3 | **SimpleNotes** | `SimpleNotes.exe` | Distraction-free Markdown notebook with SQLite FTS5 full-text search, tags, pinning, live preview, and `.md`/`.txt` export | **Available** |
| 4 | **SimpleFocus** | `SimpleFocus.exe` | Minimalist Braun-inspired Pomodoro timer with 4 built-in acoustic chimes, custom `.wav` alarm import, continuous alarm stop, and background system tray mode | **Available** |
| 5 | **SimpleTasks** | `SimpleTasks.exe` | Keyboard-first task manager with lists, due dates, priorities, and subtasks | **In Progress** |
| 6 | **SimpleCalendar** | `SimpleCalendar.exe` | Local offline schedule & event planner with `.ics` import/export | Planned (Phase 2) |
| 7 | **SimpleWriter** | `SimpleWriter.exe` | Clean rich-text & document processor with `.docx`, `.md`, and `.pdf` export | Planned (Phase 3) |
| 8 | **SimpleSheets** | `SimpleSheets.exe` | Fast spreadsheet & CSV grid with formula evaluation and `.xlsx` support | Planned (Phase 3) |
| 9 | **SimpleSlides** | `SimpleSlides.exe` | Minimalist presentation builder with clean slide layouts and PDF/PPTX export | Planned (Phase 3) |
| 10 | **SimplePDF** | `SimplePDF.exe` | Fast PDF viewer, merger, splitter, page rotator, and annotator | Planned (Phase 4) |
| 11 | **SimpleScan** | `SimpleScan.exe` | Document scanner & image deskew/enhancement utility for clean multi-page PDFs | Planned (Phase 4) |
| 12 | **SimpleConvert** | `SimpleConvert.exe` | Offline batch format converter for documents, images, and structured data | Planned (Phase 4) |
| 13 | **SimpleOCR** | `SimpleOCR.exe` | Offline optical character recognition to extract searchable text from images & PDFs | Planned (Phase 4) |

---

## How to Access & Use

### Option A: Download Pre-Built Windows Executables (End Users)
1. Visit the **Releases** page of this repository (or the official Jaiho Super Simple Office download portal).
2. Choose either:
   - **Full Suite Bundle (`JaihoSuperSimpleOfficeSetup.exe`)** — Installs `JaihoOffice.exe` and all available apps with shared runtime libraries.
   - **Standalone Individual App (e.g., `SimpleCalculator.exe`, `SimpleUnits.exe`, `SimpleNotes.exe`)** — Portable single-app folder requiring zero installation.
3. Launch any tool directly. Press **`Ctrl+Shift+T`** inside any window at any time to switch between **Light Mode (Archival Paper)** and **Dark Mode (Obsidian Ink)**.

### Option B: Run or Build from Source (Developers)

#### Prerequisites
- Windows 10 or Windows 11 (64-bit)
- Python **3.12+**

#### 1. Clone & Set Up Virtual Environment
```powershell
git clone [https://github.com/Jaiho-Digital/jaiho-super-simple-office.git](https://github.com/Jaiho-Digital/jaiho-super-simple-office.git)
cd jaiho-super-simple-office

python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```
#### 2. Generate App Icons (SVG + Multi-Size Windows .ico)
`python scripts/generate_icons.py`

#### 3. Run Any Available App Directly
```
python -m apps.simple_calculator.main
python -m apps.simple_units.main
python -m apps.simple_notes.main
python -m apps.simple_focus.main
```
#### 4. Package Standalone Windows .exe Bundles
```
# Build a single app
python scripts/build_app.py simple_calculator

# Build multiple apps at once
python scripts/build_app.py simple_calculator simple_units simple_notes simple_focus

Compiled executables are output to dist/individual/<AppName>/<AppName>.exe.
```

#### 5. Run Automated Tests & Linter
```
pytest
ruff check .
```

### Repository Architecture
jaiho-super-simple-office/
├── apps/                   # Independent desktop applications (1 package per app)
│   ├── foundation_demo/    # Phase 1 core infrastructure verification harness
│   ├── simple_calculator/  # AST-safe standard & scientific calculator
│   ├── simple_units/       # 13-category precision unit converter
│   ├── simple_notes/       # SQLite FTS5 Markdown notebook
|   ├── simple_tasks/       # Local SQLite task & subtask manager       
│   └── simple_focus/       # Braun-inspired Pomodoro & multi-sound alarm timer
├── core/                   # Shared foundation used by all 13 apps & launcher
│   ├── database/           # Isolated SQLite WAL manager, migrations & hot backups
│   ├── file_manager/       # Atomic temp-file writes, backup rotation & validation
│   ├── logging/            # Rotating per-app logs & global crash hooks
│   ├── notifications/      # Windows system tray notifications & reminder scheduler
│   ├── settings/           # Persistent JSON preferences & window geometry
│   ├── themes/             # Archival Paper & Obsidian Ink tokens + QSS compiler
│   ├── ui/                 # BaseWindow, ToastManager, modal dialogs & shared widgets
│   └── utilities/          # Frozen-safe resource paths & QThreadPool workers
├── launcher/               # JaihoOffice.exe central hub
├── resources/              # Parameterized base.qss & generated SVG/ICO icon assets
├── scripts/                # Build automation (build_app.py) & icon generator
└── tests/                  # Automated pytest & pytest-qt test suites

### Contributing & Forking
- Contributions, bug reports, and forks are welcome:

1. Fork the repository on GitHub and create a feature branch (`git checkout -b feature/your-improvement`).

2. Follow our core engineering standards:

- All applications must subclass core.ui.base_window.BaseWindow and style exclusively through core.themes.tokens / resources/styles/base.qss.

- Never use eval() or blocking I/O on the main GUI thread—use core.utilities.workers.run_in_background.

- Write files safely via core.file_manager.file_ops.atomic_write_text or atomic_write_json.

- Add unit and pytest-qt GUI tests under tests/apps/ for any new functionality.

3. Verify before opening a Pull Request:
```
pytest
ruff check .
```

### License & Attribution
Developed by **Jaiho Digital** - <i>Thoughtful desktop software built for clarity, speed, and ownership.</i>
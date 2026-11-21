"""JaihoOffice — Unified Soft Modern Pastel launcher for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.notifications.notifier import NotificationLevel
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import show_error_dialog
from core.ui.widgets import (
    CardFrame,
    HorizontalDivider,
    StatusBadge,
    make_button,
)
from core.version import APP_REGISTRY, SUITE_VERSION, AppMetadata

logger = logging.getLogger(__name__)

APP_ID = "jaiho_office"

_CATEGORY_BADGE_VARIANT: dict[str, str] = {
    "Documents": "lavender",
    "Productivity": "sage",
    "Utilities": "peach",
    "Office": "butter",
}


def resolve_app_launch_command(meta: AppMetadata) -> list[str]:
    """Resolve the standalone .exe path if packaged, otherwise fall back to `python -m`."""
    exe_stem = Path(meta.executable).stem
    repo_root = Path(__file__).resolve().parents[1]

    candidates: list[Path] = [
        # Sibling folder when packaged under dist/individual/JaihoOffice/JaihoOffice.exe
        Path(sys.executable).resolve().parent.parent / exe_stem / meta.executable,
        # Same folder if all executables are copied together
        Path(sys.executable).resolve().parent / meta.executable,
        # Repository dist/individual output
        repo_root / "dist" / "individual" / exe_stem / meta.executable,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return [str(candidate)]

    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", f"apps.{meta.app_id}.main"]

    raise FileNotFoundError(
        f"Could not locate '{meta.executable}'. Build it first using `python scripts/build_app.py {meta.app_id}`."
    )


class JaihoOfficeLauncherWindow(BaseWindow):
    """Main unified launcher window for all 13 Jaiho Super Simple Office applications."""

    def __init__(self) -> None:
        super().__init__(app_id=APP_ID, min_width=940, min_height=640)
        if self.settings.get("window_width") is None:
            self.resize(1180, 760)

        self._selected_category: str = "All"
        self._category_buttons: dict[str, QPushButton] = {}
        self._apps: list[AppMetadata] = [
            meta for aid, meta in APP_REGISTRY.items() if aid != APP_ID
        ]

        self._build_header_actions()
        self._build_workspace()
        self._render_app_grid()

    def _build_header_actions(self) -> None:
        """Mount instant search box in the integrated header."""
        self._search_input = QLineEdit(self)
        self._search_input.setPlaceholderText("Filter apps (e.g. Writer, PDF, Scan, Chat)...")
        self._search_input.setFixedWidth(260)
        self._search_input.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self._search_input.textChanged.connect(self._render_app_grid)
        self.header_actions_layout.addWidget(self._search_input)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top Category Strip
        cat_bar = QFrame(central)
        cat_bar.setProperty("surface", "app-header")
        cat_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        cat_row = QHBoxLayout(cat_bar)
        cat_row.setContentsMargins(20, 8, 20, 8)
        cat_row.setSpacing(6)

        categories = ["All", "Documents", "Productivity", "Utilities", "Office"]
        for cat in categories:
            btn = QPushButton(cat, cat_bar)
            btn.setProperty("variant", "segmented")
            btn.setCheckable(True)
            btn.setChecked(cat == "All")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
            btn.clicked.connect(lambda _=False, c=cat: self._select_category(c))
            cat_row.addWidget(btn)
            self._category_buttons[cat] = btn

        cat_row.addStretch(1)

        count_lbl = QLabel(f"{len(self._apps)} Offline Desktop Apps  •  v{SUITE_VERSION}", cat_bar)
        count_lbl.setProperty("role", "caption")
        cat_row.addWidget(count_lbl)

        outer.addWidget(cat_bar)

        # Scrollable App Card Grid
        self._scroll = QScrollArea(central)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._grid_host = QWidget(self._scroll)
        self._grid_layout = QGridLayout(self._grid_host)
        self._grid_layout.setContentsMargins(24, 20, 24, 24)
        self._grid_layout.setSpacing(16)
        self._scroll.setWidget(self._grid_host)
        outer.addWidget(self._scroll, 1)

        # Quiet Footer Bar
        footer = QFrame(central)
        footer.setProperty("surface", "footer-bar")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer.setFixedHeight(34)

        f_row = QHBoxLayout(footer)
        f_row.setContentsMargins(16, 4, 16, 4)
        f_row.setSpacing(10)

        f_row.addWidget(StatusBadge("100% Local & Offline", variant="sage", parent=footer))
        f_lbl = QLabel("Zero cloud accounts  •  Standalone Windows executables", footer)
        f_lbl.setProperty("role", "muted")
        f_row.addWidget(f_lbl)
        f_row.addStretch(1)
        outer.addWidget(footer)

    def _select_category(self, category: str) -> None:
        self._selected_category = category
        for cat_name, btn in self._category_buttons.items():
            btn.setChecked(cat_name == category)
        self._render_app_grid()

    def _render_app_grid(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()

        query = self._search_input.text().strip().lower()
        filtered: list[AppMetadata] = []
        for meta in self._apps:
            if self._selected_category != "All" and meta.category != self._selected_category:
                continue
            if query and (
                query not in meta.display_name.lower()
                and query not in meta.description.lower()
                and query not in meta.category.lower()
            ):
                continue
            filtered.append(meta)

        cols = 3
        for idx, meta in enumerate(filtered):
            card = CardFrame(padding=18, spacing=10, parent=self._grid_host)

            top_row = QHBoxLayout()
            top_row.setSpacing(8)
            title_lbl = QLabel(meta.display_name, card)
            title_lbl.setProperty("role", "heading")
            top_row.addWidget(title_lbl, 1)

            badge_var = _CATEGORY_BADGE_VARIANT.get(meta.category, "lavender")
            top_row.addWidget(StatusBadge(meta.category, variant=badge_var, parent=card))
            card.body_layout.addLayout(top_row)

            desc_lbl = QLabel(meta.description, card)
            desc_lbl.setProperty("role", "muted")
            desc_lbl.setWordWrap(True)
            card.body_layout.addWidget(desc_lbl, 1)

            card.body_layout.addWidget(HorizontalDivider(card))

            bot_row = QHBoxLayout()
            bot_row.setSpacing(8)
            exe_lbl = QLabel(meta.executable, card)
            exe_lbl.setProperty("role", "caption")
            bot_row.addWidget(exe_lbl, 1)

            open_btn = make_button(
                text="Open App",
                variant="pastel",
                on_click=lambda _=False, m=meta: self.launch_app(m),
                parent=card,
            )
            open_btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
            bot_row.addWidget(open_btn)
            card.body_layout.addLayout(bot_row)

            self._grid_layout.addWidget(card, idx // cols, idx % cols)

        self._grid_layout.setRowStretch((len(filtered) // cols) + 1, 1)

    def launch_app(self, meta: AppMetadata) -> bool:
        """Launch the selected application as a separate local process."""
        try:
            cmd = resolve_app_launch_command(meta)
            subprocess.Popen(cmd)
        except Exception as exc:
            show_error_dialog(self, f"Could Not Launch {meta.display_name}", str(exc))
            return False

        self.notify(
            f"Launched {meta.display_name}",
            f"Started {meta.executable} locally.",
            level=NotificationLevel.SUCCESS,
        )
        return True


def main() -> int:
    """Application entry point for JaihoOffice.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=JaihoOfficeLauncherWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())
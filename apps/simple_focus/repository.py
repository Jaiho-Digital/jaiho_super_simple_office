"""SQLite WAL session persistence and focus analytics repository for SimpleFocus."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from core.database.db_manager import DatabaseError, DatabaseManager, Migration

APP_ID = "simple_focus"


class FocusPhase(StrEnum):
    """Supported timer phases in SimpleFocus."""

    FOCUS = "focus"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"

    @property
    def display_name(self) -> str:
        """Human-readable label for UI headers and notifications."""
        if self == FocusPhase.FOCUS:
            return "Deep Focus"
        if self == FocusPhase.SHORT_BREAK:
            return "Short Break"
        return "Long Break"


@dataclass(frozen=True, slots=True)
class FocusSessionRecord:
    """Immutable record of a logged focus or break session."""

    session_id: int
    task_label: str
    phase: FocusPhase
    planned_minutes: int
    actual_seconds: int
    completed: bool
    started_at: str
    completed_at: str
    session_date: str

    @property
    def duration_minutes(self) -> int:
        """Return rounded active minutes (minimum 1 if at least 30 seconds elapsed)."""
        if self.actual_seconds <= 0:
            return 0
        return max(1, round(self.actual_seconds / 60))


@dataclass(frozen=True, slots=True)
class DailyFocusStats:
    """Aggregated focus metrics for a single calendar date."""

    date_str: str
    completed_focus_sessions: int
    total_focus_minutes: int
    completed_break_sessions: int


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Create focus_sessions table and date index."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS focus_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_label TEXT NOT NULL,
            phase_type TEXT NOT NULL,
            planned_minutes INTEGER NOT NULL,
            actual_seconds INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 1,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            session_date TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_focus_sessions_date
            ON focus_sessions (session_date DESC, id DESC);
        """
    )


FOCUS_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="Create focus_sessions table for Pomodoro and deep-work history",
        up=_migrate_v1,
    ),
)


class FocusRepository:
    """Data-access layer for logging focus intervals and computing daily/streak analytics."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or DatabaseManager(app_id=APP_ID)
        self.db.apply_migrations(FOCUS_MIGRATIONS)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FocusSessionRecord:
        raw_phase = str(row["phase_type"] or FocusPhase.FOCUS.value)
        try:
            phase = FocusPhase(raw_phase)
        except ValueError:
            phase = FocusPhase.FOCUS

        return FocusSessionRecord(
            session_id=int(row["id"]),
            task_label=str(row["task_label"]),
            phase=phase,
            planned_minutes=int(row["planned_minutes"]),
            actual_seconds=int(row["actual_seconds"]),
            completed=bool(row["completed"]),
            started_at=str(row["started_at"]),
            completed_at=str(row["completed_at"]),
            session_date=str(row["session_date"]),
        )

    def record_session(
        self,
        *,
        task_label: str,
        phase: FocusPhase,
        planned_minutes: int,
        actual_seconds: int,
        completed: bool = True,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> FocusSessionRecord:
        """Persist a completed or manually logged session and return the saved record."""
        end_dt = completed_at or datetime.now()
        start_dt = started_at or (end_dt - timedelta(seconds=max(0, int(actual_seconds))))
        clean_label = task_label.strip() or (
            "Focused Work Session" if phase == FocusPhase.FOCUS else phase.display_name
        )
        date_iso = end_dt.strftime("%Y-%m-%d")

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO focus_sessions (
                    task_label,
                    phase_type,
                    planned_minutes,
                    actual_seconds,
                    completed,
                    started_at,
                    completed_at,
                    session_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    clean_label,
                    phase.value,
                    max(1, int(planned_minutes)),
                    max(0, int(actual_seconds)),
                    int(completed),
                    start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    date_iso,
                ),
            )
            new_id = cursor.lastrowid
            if new_id is None:
                raise DatabaseError("Failed to retrieve inserted focus session ID.")

        saved = self.get_session(int(new_id))
        if saved is None:
            raise DatabaseError(f"Inserted focus session #{new_id} could not be loaded.")
        return saved

    def get_session(self, session_id: int) -> FocusSessionRecord | None:
        """Fetch a single focus session by ID."""
        with self.db.read_only() as conn:
            row = conn.execute(
                """
                SELECT id, task_label, phase_type, planned_minutes, actual_seconds,
                       completed, started_at, completed_at, session_date
                FROM focus_sessions
                WHERE id = ?;
                """,
                (int(session_id),),
            ).fetchone()
            return self._row_to_record(row) if row is not None else None

    def list_recent_sessions(
        self,
        limit: int = 50,
        only_focus: bool = False,
    ) -> list[FocusSessionRecord]:
        """Return most recent logged sessions ordered newest first."""
        where_sql = "WHERE phase_type = 'focus'" if only_focus else ""
        sql = f"""
            SELECT id, task_label, phase_type, planned_minutes, actual_seconds,
                   completed, started_at, completed_at, session_date
            FROM focus_sessions
            {where_sql}
            ORDER BY completed_at DESC, id DESC
            LIMIT ?;
        """
        with self.db.read_only() as conn:
            rows = conn.execute(sql, (max(1, int(limit)),)).fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_daily_stats(self, target_date: date | None = None) -> DailyFocusStats:
        """Aggregate focus minutes and completed sessions for `target_date` (defaults to today)."""
        day_str = (target_date or date.today()).strftime("%Y-%m-%d")
        with self.db.read_only() as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN phase_type = 'focus' AND completed = 1 THEN 1 ELSE 0 END) AS focus_count,
                    SUM(CASE WHEN phase_type = 'focus' THEN actual_seconds ELSE 0 END) AS focus_seconds,
                    SUM(CASE WHEN phase_type != 'focus' AND completed = 1 THEN 1 ELSE 0 END) AS break_count
                FROM focus_sessions
                WHERE session_date = ?;
                """,
                (day_str,),
            ).fetchone()

        if row is None:
            return DailyFocusStats(
                date_str=day_str,
                completed_focus_sessions=0,
                total_focus_minutes=0,
                completed_break_sessions=0,
            )

        total_secs = int(row["focus_seconds"] or 0)
        return DailyFocusStats(
            date_str=day_str,
            completed_focus_sessions=int(row["focus_count"] or 0),
            total_focus_minutes=round(total_secs / 60),
            completed_break_sessions=int(row["break_count"] or 0),
        )

    def get_current_streak_days(self, reference_date: date | None = None) -> int:
        """Compute consecutive calendar days (ending today or yesterday) with >=1 completed focus session."""
        ref = reference_date or date.today()
        with self.db.read_only() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT session_date
                FROM focus_sessions
                WHERE phase_type = 'focus' AND completed = 1
                ORDER BY session_date DESC;
                """
            ).fetchall()

        active_days: set[date] = set()
        for row in rows:
            try:
                active_days.add(date.fromisoformat(str(row["session_date"])))
            except ValueError:
                continue

        if not active_days:
            return 0

        cursor_day = ref if ref in active_days else (ref - timedelta(days=1))
        streak = 0
        while cursor_day in active_days:
            streak += 1
            cursor_day -= timedelta(days=1)
        return streak

    def delete_session(self, session_id: int) -> bool:
        """Remove a single session record by ID."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM focus_sessions WHERE id = ?;", (int(session_id),))
            return cursor.rowcount > 0

    def clear_all_sessions(self) -> None:
        """Delete all recorded focus sessions."""
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM focus_sessions;")
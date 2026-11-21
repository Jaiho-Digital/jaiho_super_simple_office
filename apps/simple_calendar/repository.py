"""SQLite WAL event persistence and RFC 5545 (.ics) import/export repository for SimpleCalendar."""

from __future__ import annotations

import calendar
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from core.database.db_manager import DatabaseError, DatabaseManager, Migration
from core.file_manager.file_ops import atomic_write_text, validate_input_file

APP_ID = "simple_calendar"


@dataclass(frozen=True, slots=True)
class CalendarEventRecord:
    """Immutable domain representation of a single calendar event."""

    event_id: int
    title: str
    event_date: str  # ISO YYYY-MM-DD
    start_time: str | None  # HH:MM (24-hour) or None for all-day events
    end_time: str | None  # HH:MM (24-hour) or None
    location: str
    notes: str
    created_at: str
    updated_at: str

    @property
    def is_all_day(self) -> bool:
        """Return True when the event has no specific start time."""
        return self.start_time is None

    @property
    def time_span_label(self) -> str:
        """Return a clean human-readable time span such as 'All Day' or '09:30 – 10:30'."""
        if not self.start_time:
            return "All Day"
        if self.end_time:
            return f"{self.start_time} – {self.end_time}"
        return self.start_time


def normalize_event_date(raw_date: str | date) -> str:
    """Validate and return an ISO YYYY-MM-DD date string."""
    if isinstance(raw_date, date):
        return raw_date.isoformat()
    cleaned = str(raw_date).strip()
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError as exc:
        raise DatabaseError(f"Invalid event date '{cleaned}'. Expected YYYY-MM-DD.") from exc


def normalize_event_time(raw_time: str | time | None) -> str | None:
    """Validate and return a 24-hour HH:MM time string, or None."""
    if raw_time is None:
        return None
    if isinstance(raw_time, time):
        return f"{raw_time.hour:02d}:{raw_time.minute:02d}"
    cleaned = str(raw_time).strip()
    if not cleaned:
        return None

    parts = cleaned.split(":")
    if len(parts) not in {2, 3}:
        raise DatabaseError(f"Invalid time '{cleaned}'. Expected HH:MM.")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise DatabaseError(f"Invalid time '{cleaned}'. Expected HH:MM.") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise DatabaseError(f"Time '{cleaned}' is out of valid 24-hour range.")
    return f"{hour:02d}:{minute:02d}"


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Create events table and date index."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            event_date TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            location TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_events_date_time
            ON events (event_date ASC, CASE WHEN start_time IS NULL THEN 0 ELSE 1 END ASC, start_time ASC, id ASC);
        """
    )


CALENDAR_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="Create events table for local offline calendar scheduling",
        up=_migrate_v1,
    ),
)


def _escape_ics_text(text: str) -> str:
    """Escape special characters per RFC 5545 TEXT property rules."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _unescape_ics_text(text: str) -> str:
    """Unescape RFC 5545 TEXT property escapes."""
    return (
        text.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _unfold_ics_lines(raw_text: str) -> list[str]:
    """Unfold CRLF/LF + single-whitespace continuation lines per RFC 5545 Section 3.1."""
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    unfolded: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _parse_ics_datetime(raw_val: str) -> tuple[str, str | None]:
    """Parse an ICS DTSTART/DTEND value into (YYYY-MM-DD, HH:MM | None)."""
    token = raw_val.strip().rstrip("Z")
    if "T" in token:
        date_part, time_part = token.split("T", 1)
        if len(date_part) != 8 or len(time_part) < 4:
            raise ValueError(f"Unsupported ICS datetime '{raw_val}'")
        iso_date = f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]}"
        date.fromisoformat(iso_date)
        hh_mm = f"{int(time_part[0:2]):02d}:{int(time_part[2:4]):02d}"
        normalize_event_time(hh_mm)
        return iso_date, hh_mm

    if len(token) != 8:
        raise ValueError(f"Unsupported ICS date '{raw_val}'")
    iso_date = f"{token[0:4]}-{token[4:6]}-{token[6:8]}"
    date.fromisoformat(iso_date)
    return iso_date, None


class CalendarRepository:
    """Data-access layer for managing local calendar events and .ics files."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or DatabaseManager(app_id=APP_ID)
        self.db.apply_migrations(CALENDAR_MIGRATIONS)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> CalendarEventRecord:
        return CalendarEventRecord(
            event_id=int(row["id"]),
            title=str(row["title"]),
            event_date=str(row["event_date"]),
            start_time=str(row["start_time"]) if row["start_time"] else None,
            end_time=str(row["end_time"]) if row["end_time"] else None,
            location=str(row["location"] or ""),
            notes=str(row["notes"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def create_event(
        self,
        *,
        title: str,
        event_date: str | date,
        start_time: str | time | None = None,
        end_time: str | time | None = None,
        location: str = "",
        notes: str = "",
    ) -> CalendarEventRecord:
        """Insert a new calendar event and return the hydrated record."""
        clean_title = title.strip()
        if not clean_title:
            raise DatabaseError("Event title cannot be empty.")

        norm_date = normalize_event_date(event_date)
        norm_start = normalize_event_time(start_time)
        norm_end = normalize_event_time(end_time) if norm_start is not None else None
        if norm_start and norm_end and norm_end < norm_start:
            raise DatabaseError("End time cannot be earlier than start time.")

        now = self._now_iso()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (
                    title, event_date, start_time, end_time, location, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    clean_title,
                    norm_date,
                    norm_start,
                    norm_end,
                    location.strip(),
                    notes.strip(),
                    now,
                    now,
                ),
            )
            new_id = cursor.lastrowid
            if new_id is None:
                raise DatabaseError("Failed to retrieve inserted event ID.")

        created = self.get_event(int(new_id))
        if created is None:
            raise DatabaseError(f"Inserted event #{new_id} could not be loaded.")
        return created

    def get_event(self, event_id: int) -> CalendarEventRecord | None:
        """Return a single event by ID, or None if not found."""
        with self.db.read_only() as conn:
            row = conn.execute(
                """
                SELECT id, title, event_date, start_time, end_time, location, notes, created_at, updated_at
                FROM events
                WHERE id = ?;
                """,
                (int(event_id),),
            ).fetchone()
            return self._row_to_event(row) if row is not None else None

    def update_event(
        self,
        event_id: int,
        *,
        title: str | None = None,
        event_date: str | date | None = None,
        start_time: str | time | None = ...,  # type: ignore[assignment]
        end_time: str | time | None = ...,  # type: ignore[assignment]
        location: str | None = None,
        notes: str | None = None,
    ) -> CalendarEventRecord:
        """Update selected fields on an existing calendar event."""
        existing = self.get_event(event_id)
        if existing is None:
            raise DatabaseError(f"Cannot update missing event #{event_id}.")

        next_title = (title.strip() or existing.title) if title is not None else existing.title
        next_date = (
            normalize_event_date(event_date)
            if event_date is not None
            else existing.event_date
        )
        next_start = (
            existing.start_time
            if start_time is ...
            else normalize_event_time(start_time)
        )
        raw_end = existing.end_time if end_time is ... else normalize_event_time(end_time)
        next_end = raw_end if next_start is not None else None

        if next_start and next_end and next_end < next_start:
            raise DatabaseError("End time cannot be earlier than start time.")

        next_location = location.strip() if location is not None else existing.location
        next_notes = notes.strip() if notes is not None else existing.notes
        now = self._now_iso()

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE events
                SET title = ?,
                    event_date = ?,
                    start_time = ?,
                    end_time = ?,
                    location = ?,
                    notes = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    next_title,
                    next_date,
                    next_start,
                    next_end,
                    next_location,
                    next_notes,
                    now,
                    int(event_id),
                ),
            )

        updated = self.get_event(event_id)
        if updated is None:
            raise DatabaseError(f"Updated event #{event_id} could not be loaded.")
        return updated

    def delete_event(self, event_id: int) -> bool:
        """Permanently delete a calendar event by ID."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM events WHERE id = ?;", (int(event_id),))
            return cursor.rowcount > 0

    def list_events_for_date(self, target_date: str | date) -> list[CalendarEventRecord]:
        """Return all events scheduled on `target_date` (all-day first, then chronological)."""
        iso_day = normalize_event_date(target_date)
        with self.db.read_only() as conn:
            rows = conn.execute(
                """
                SELECT id, title, event_date, start_time, end_time, location, notes, created_at, updated_at
                FROM events
                WHERE event_date = ?
                ORDER BY CASE WHEN start_time IS NULL THEN 0 ELSE 1 END ASC, start_time ASC, id ASC;
                """,
                (iso_day,),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]

    def list_upcoming_events(
        self,
        *,
        from_date: str | date | None = None,
        limit: int = 30,
    ) -> list[CalendarEventRecord]:
        """Return upcoming events on or after `from_date`."""
        start_iso = normalize_event_date(from_date or date.today())
        with self.db.read_only() as conn:
            rows = conn.execute(
                """
                SELECT id, title, event_date, start_time, end_time, location, notes, created_at, updated_at
                FROM events
                WHERE event_date >= ?
                ORDER BY event_date ASC, CASE WHEN start_time IS NULL THEN 0 ELSE 1 END ASC, start_time ASC, id ASC
                LIMIT ?;
                """,
                (start_iso, max(1, int(limit))),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]

    def list_all_events(self) -> list[CalendarEventRecord]:
        """Return every event ordered chronologically."""
        with self.db.read_only() as conn:
            rows = conn.execute(
                """
                SELECT id, title, event_date, start_time, end_time, location, notes, created_at, updated_at
                FROM events
                ORDER BY event_date ASC, CASE WHEN start_time IS NULL THEN 0 ELSE 1 END ASC, start_time ASC, id ASC;
                """
            ).fetchall()
            return [self._row_to_event(r) for r in rows]

    def search_events(self, query: str) -> list[CalendarEventRecord]:
        """Search events by title, location, or notes."""
        cleaned = query.strip()
        if not cleaned:
            return []
        pat = f"%{cleaned}%"
        with self.db.read_only() as conn:
            rows = conn.execute(
                """
                SELECT id, title, event_date, start_time, end_time, location, notes, created_at, updated_at
                FROM events
                WHERE title LIKE ? OR location LIKE ? OR notes LIKE ?
                ORDER BY event_date ASC, CASE WHEN start_time IS NULL THEN 0 ELSE 1 END ASC, start_time ASC;
                """,
                (pat, pat, pat),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]

    def get_month_event_counts(self, year: int, month: int) -> dict[str, int]:
        """Return a mapping of ISO date ('YYYY-MM-DD') -> event count for the given month."""
        _, last_day = calendar.monthrange(int(year), int(month))
        start_iso = f"{int(year):04d}-{int(month):02d}-01"
        end_iso = f"{int(year):04d}-{int(month):02d}-{last_day:02d}"

        counts: Counter[str] = Counter()
        with self.db.read_only() as conn:
            rows = conn.execute(
                """
                SELECT event_date, COUNT(*) AS cnt
                FROM events
                WHERE event_date BETWEEN ? AND ?
                GROUP BY event_date;
                """,
                (start_iso, end_iso),
            ).fetchall()
            for r in rows:
                counts[str(r["event_date"])] = int(r["cnt"])
        return dict(counts)

    # ------------------------------------------------------------------
    # Standard RFC 5545 (.ics) Export & Import
    # ------------------------------------------------------------------

    def export_ics_text(self, events: list[CalendarEventRecord] | None = None) -> str:
        """Serialize events into a standard RFC 5545 VCALENDAR string."""
        records = events if events is not None else self.list_all_events()
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")

        lines: list[str] = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Jaiho Digital//SimpleCalendar//EN",
            "CALSCALE:GREGORIAN",
        ]

        for ev in records:
            compact_date = ev.event_date.replace("-", "")
            lines.append("BEGIN:VEVENT")
            lines.append(f"UID:simple-calendar-{ev.event_id}-{uuid.uuid4().hex[:8]}@jaihodigital")
            lines.append(f"DTSTAMP:{stamp}")

            if ev.start_time:
                start_compact = ev.start_time.replace(":", "") + "00"
                lines.append(f"DTSTART:{compact_date}T{start_compact}")
                if ev.end_time:
                    end_compact = ev.end_time.replace(":", "") + "00"
                    lines.append(f"DTEND:{compact_date}T{end_compact}")
            else:
                lines.append(f"DTSTART;VALUE=DATE:{compact_date}")

            lines.append(f"SUMMARY:{_escape_ics_text(ev.title)}")
            if ev.location:
                lines.append(f"LOCATION:{_escape_ics_text(ev.location)}")
            if ev.notes:
                lines.append(f"DESCRIPTION:{_escape_ics_text(ev.notes)}")
            lines.append("END:VEVENT")

        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"

    def export_ics_file(
        self,
        destination: str | Path,
        events: list[CalendarEventRecord] | None = None,
    ) -> Path:
        """Atomically write calendar events to a .ics file."""
        ics_payload = self.export_ics_text(events=events)
        return atomic_write_text(destination, ics_payload, backup_app_id=APP_ID)

    def import_ics_text(self, raw_ics: str) -> int:
        """Parse VEVENT blocks from an .ics string and insert them into SQLite. Returns count added."""
        lines = _unfold_ics_lines(raw_ics)
        in_event = False
        current: dict[str, str] = {}
        imported_count = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            upper = stripped.upper()
            if upper == "BEGIN:VEVENT":
                in_event = True
                current = {}
                continue
            if upper == "END:VEVENT":
                if in_event and "DTSTART" in current:
                    try:
                        ev_date, start_hhmm = _parse_ics_datetime(current["DTSTART"])
                        end_hhmm: str | None = None
                        if "DTEND" in current:
                            end_date, parsed_end = _parse_ics_datetime(current["DTEND"])
                            if end_date == ev_date and parsed_end is not None:
                                end_hhmm = parsed_end
                        title = _unescape_ics_text(current.get("SUMMARY", "")).strip() or "Imported Event"
                        location = _unescape_ics_text(current.get("LOCATION", "")).strip()
                        notes = _unescape_ics_text(current.get("DESCRIPTION", "")).strip()
                        self.create_event(
                            title=title,
                            event_date=ev_date,
                            start_time=start_hhmm,
                            end_time=end_hhmm,
                            location=location,
                            notes=notes,
                        )
                        imported_count += 1
                    except (ValueError, DatabaseError):
                        pass
                in_event = False
                current = {}
                continue

            if in_event and ":" in stripped:
                prop_key, prop_val = stripped.split(":", 1)
                base_prop = prop_key.split(";", 1)[0].upper()
                current[base_prop] = prop_val

        return imported_count

    def import_ics_file(self, source_path: str | Path) -> int:
        """Validate and import events from an external .ics file."""
        validated = validate_input_file(
            source_path,
            allowed_extensions={".ics"},
            max_size_mb=10.0,
        )
        raw_text = validated.read_text(encoding="utf-8", errors="replace")
        return self.import_ics_text(raw_text)
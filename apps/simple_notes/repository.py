"""SQLite WAL + FTS5 persistence repository for SimpleNotes."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from core.database.db_manager import DatabaseError, DatabaseManager, Migration

APP_ID = "simple_notes"

_TOKEN_RE = re.compile(r"[0-9A-Za-z_]+")


def normalize_tags(raw_tags: Sequence[str] | str) -> tuple[str, ...]:
    """Normalize comma-separated or sequence tags into deduplicated lowercase tokens."""
    if isinstance(raw_tags, str):
        candidates = raw_tags.replace("#", " ").replace(",", " ").split()
    else:
        candidates = []
        for item in raw_tags:
            candidates.extend(str(item).replace("#", " ").replace(",", " ").split())

    seen: set[str] = set()
    cleaned: list[str] = []
    for token in candidates:
        norm = token.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    return tuple(cleaned)


def format_tags_storage(tags: Sequence[str] | str) -> str:
    """Serialize normalized tags into a canonical comma-separated string."""
    return ",".join(normalize_tags(tags))


@dataclass(frozen=True, slots=True)
class NoteRecord:
    """Immutable domain representation of a single stored note."""

    note_id: int
    title: str
    content: str
    tags: tuple[str, ...]
    is_pinned: bool
    is_archived: bool
    created_at: str
    updated_at: str

    @property
    def word_count(self) -> int:
        """Return total whitespace-delimited word count across title and body."""
        words = self.content.split()
        return len(words)

    @property
    def char_count(self) -> int:
        """Return total character count of the note body."""
        return len(self.content)

    @property
    def reading_time_minutes(self) -> int:
        """Estimate reading time at 220 words per minute (minimum 1 min if non-empty)."""
        if not self.content.strip():
            return 0
        return max(1, round(self.word_count / 220))

    @property
    def preview_excerpt(self) -> str:
        """Return a clean single-line excerpt for sidebar list cards."""
        for line in self.content.splitlines():
            stripped = line.lstrip("#>-*• ").strip()
            if stripped:
                return stripped[:110]
        return "Empty note"


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Create notes table, indexes, and synchronized FTS5 search index."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            is_pinned INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notes_active_order
            ON notes (is_archived, is_pinned DESC, updated_at DESC);
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            title,
            content,
            tags,
            content='notes',
            content_rowid='id'
        );
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
            INSERT INTO notes_fts(rowid, title, content, tags)
            VALUES (new.id, new.title, new.content, new.tags);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, title, content, tags)
            VALUES ('delete', old.id, old.title, old.content, old.tags);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, title, content, tags)
            VALUES ('delete', old.id, old.title, old.content, old.tags);
            INSERT INTO notes_fts(rowid, title, content, tags)
            VALUES (new.id, new.title, new.content, new.tags);
        END;
        """
    )


NOTES_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="Create notes table with FTS5 full-text search index and sync triggers",
        up=_migrate_v1,
    ),
)


class NotesRepository:
    """Data-access layer for creating, searching, tagging, and archiving notes."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or DatabaseManager(app_id=APP_ID)
        self.db.apply_migrations(NOTES_MIGRATIONS)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> NoteRecord:
        raw_tags = str(row["tags"] or "")
        parsed_tags = tuple(t for t in raw_tags.split(",") if t)
        return NoteRecord(
            note_id=int(row["id"]),
            title=str(row["title"]),
            content=str(row["content"]),
            tags=parsed_tags,
            is_pinned=bool(row["is_pinned"]),
            is_archived=bool(row["is_archived"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def create_note(
        self,
        title: str = "Untitled Note",
        content: str = "",
        tags: Sequence[str] | str = (),
        is_pinned: bool = False,
    ) -> NoteRecord:
        """Insert a new note and return the hydrated NoteRecord."""
        clean_title = title.strip() or "Untitled Note"
        tags_str = format_tags_storage(tags)
        now = self._now_iso()

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO notes (title, content, tags, is_pinned, is_archived, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?);
                """,
                (clean_title, content, tags_str, int(is_pinned), now, now),
            )
            new_id = cursor.lastrowid
            if new_id is None:
                raise DatabaseError("Failed to retrieve inserted note ID.")

        created = self.get_note(int(new_id))
        if created is None:
            raise DatabaseError(f"Inserted note #{new_id} could not be loaded.")
        return created

    def get_note(self, note_id: int) -> NoteRecord | None:
        """Fetch a single note by primary key, or None if it does not exist."""
        with self.db.read_only() as conn:
            row = conn.execute(
                """
                SELECT id, title, content, tags, is_pinned, is_archived, created_at, updated_at
                FROM notes
                WHERE id = ?;
                """,
                (int(note_id),),
            ).fetchone()
            return self._row_to_record(row) if row is not None else None

    def update_note(
        self,
        note_id: int,
        *,
        title: str | None = None,
        content: str | None = None,
        tags: Sequence[str] | str | None = None,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
    ) -> NoteRecord:
        """Update selected fields on an existing note and bump its updated_at timestamp."""
        existing = self.get_note(note_id)
        if existing is None:
            raise DatabaseError(f"Cannot update missing note #{note_id}.")

        next_title = (title.strip() or "Untitled Note") if title is not None else existing.title
        next_content = content if content is not None else existing.content
        next_tags = (
            format_tags_storage(tags)
            if tags is not None
            else format_tags_storage(existing.tags)
        )
        next_pinned = int(is_pinned) if is_pinned is not None else int(existing.is_pinned)
        next_archived = int(is_archived) if is_archived is not None else int(existing.is_archived)
        now = self._now_iso()

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE notes
                SET title = ?,
                    content = ?,
                    tags = ?,
                    is_pinned = ?,
                    is_archived = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    next_title,
                    next_content,
                    next_tags,
                    next_pinned,
                    next_archived,
                    now,
                    int(note_id),
                ),
            )

        updated = self.get_note(note_id)
        if updated is None:
            raise DatabaseError(f"Updated note #{note_id} could not be loaded.")
        return updated

    def toggle_pin(self, note_id: int) -> NoteRecord:
        """Flip the pinned state of a note."""
        existing = self.get_note(note_id)
        if existing is None:
            raise DatabaseError(f"Cannot pin missing note #{note_id}.")
        return self.update_note(note_id, is_pinned=not existing.is_pinned)

    def toggle_archive(self, note_id: int) -> NoteRecord:
        """Flip the archived state of a note (unpinning automatically when archiving)."""
        existing = self.get_note(note_id)
        if existing is None:
            raise DatabaseError(f"Cannot archive missing note #{note_id}.")
        next_archived = not existing.is_archived
        next_pinned = False if next_archived else existing.is_pinned
        return self.update_note(note_id, is_archived=next_archived, is_pinned=next_pinned)

    def delete_note(self, note_id: int) -> bool:
        """Permanently delete a note by ID. Returns True if a row was removed."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM notes WHERE id = ?;", (int(note_id),))
            return cursor.rowcount > 0

    @staticmethod
    def _build_fts_match_query(raw_query: str) -> str:
        """Convert user search text into safe FTS5 prefix tokens (e.g. '"proj"* AND "plan"*')."""
        tokens = _TOKEN_RE.findall(raw_query)
        if not tokens:
            return ""
        return " AND ".join(f'"{tok}"*' for tok in tokens)

    def list_notes(
        self,
        *,
        search_query: str = "",
        tag_filter: str = "",
        only_archived: bool = False,
        only_pinned: bool = False,
    ) -> list[NoteRecord]:
        """Query notes filtered by archive status, pin flag, tag, and FTS5/LIKE search."""
        cleaned_query = search_query.strip()
        cleaned_tag = tag_filter.strip().lstrip("#").lower()

        where_clauses: list[str] = ["n.is_archived = ?"]
        params: list[object] = [1 if only_archived else 0]

        if only_pinned and not only_archived:
            where_clauses.append("n.is_pinned = 1")

        if cleaned_tag:
            # Match exact tag within comma-delimited string
            where_clauses.append("(',' || n.tags || ',') LIKE ?")
            params.append(f"%,{cleaned_tag},%")

        join_sql = ""
        if cleaned_query:
            fts_expr = self._build_fts_match_query(cleaned_query)
            if fts_expr:
                join_sql = "JOIN notes_fts fts ON fts.rowid = n.id"
                where_clauses.append("notes_fts MATCH ?")
                params.append(fts_expr)
            else:
                # Fallback for punctuation-only queries such as "C++" or "@@"
                where_clauses.append("(n.title LIKE ? OR n.content LIKE ?)")
                like_pat = f"%{cleaned_query}%"
                params.extend([like_pat, like_pat])

        sql = f"""
            SELECT n.id, n.title, n.content, n.tags, n.is_pinned, n.is_archived, n.created_at, n.updated_at
            FROM notes n
            {join_sql}
            WHERE {' AND '.join(where_clauses)}
            ORDER BY n.is_pinned DESC, n.updated_at DESC, n.id DESC;
        """

        with self.db.read_only() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._row_to_record(r) for r in rows]

    def list_all_tags(self, include_archived: bool = False) -> list[tuple[str, int]]:
        """Return all tags with note counts sorted by frequency descending, then alphabetically."""
        sql = "SELECT tags FROM notes" if include_archived else "SELECT tags FROM notes WHERE is_archived = 0"
        counts: Counter[str] = Counter()
        with self.db.read_only() as conn:
            for row in conn.execute(sql).fetchall():
                raw = str(row["tags"] or "")
                for tag in raw.split(","):
                    if tag:
                        counts[tag] += 1

        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    def get_counts(self) -> dict[str, int]:
        """Return summary counts for sidebar badges."""
        with self.db.read_only() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_archived = 0 THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN is_archived = 0 AND is_pinned = 1 THEN 1 ELSE 0 END) AS pinned,
                    SUM(CASE WHEN is_archived = 1 THEN 1 ELSE 0 END) AS archived
                FROM notes;
                """
            ).fetchone()
            if row is None:
                return {"total": 0, "active": 0, "pinned": 0, "archived": 0}
            return {
                "total": int(row["total"] or 0),
                "active": int(row["active"] or 0),
                "pinned": int(row["pinned"] or 0),
                "archived": int(row["archived"] or 0),
            }

    def seed_welcome_note_if_empty(self) -> NoteRecord | None:
        """Create an initial quick-reference note when the database has zero notes."""
        if self.get_counts()["total"] > 0:
            return None

        welcome_body = (
            "# Welcome to SimpleNotes\n\n"
            "A fast, distraction-free archival notebook built for **Jaiho Super Simple Office**.\n\n"
            "## Key Capabilities\n"
            "- **Instant Full-Text Search:** Powered by SQLite FTS5 across titles, content, and tags.\n"
            "- **Split & Preview Modes:** Switch between *Write*, *Split*, and *Reading Preview* anytime.\n"
            "- **Automatic Persistence:** Every keystroke is debounced and saved safely to your local database.\n"
            "- **Portable Export:** Export any note cleanly as `.md` or `.txt`.\n\n"
            "## Keyboard Shortcuts\n"
            "- `Ctrl+N` — Create a new note\n"
            "- `Ctrl+F` — Jump to instant search\n"
            "- `Ctrl+E` — Cycle Write / Split / Preview view\n"
            "- `Ctrl+Shift+T` — Toggle Archival Paper & Obsidian Ink themes\n"
        )
        return self.create_note(
            title="Welcome to SimpleNotes",
            content=welcome_body,
            tags=("guide", "getting-started"),
            is_pinned=True,
        )
"""Isolated SQLite database manager with WAL mode, migrations, and safe backups."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.utilities.paths import get_backups_dir, get_database_dir

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Raised when a database initialization, migration, query, or backup fails."""


@dataclass(frozen=True, slots=True)
class Migration:
    """Represents a single forward schema migration step."""

    version: int
    description: str
    up: str | Callable[[sqlite3.Connection], None]


def _split_sql_statements(script: str) -> list[str]:
    """Split a multi-statement SQL string into complete executable statements without implicit COMMIT."""
    statements: list[str] = []
    buffer: list[str] = []
    for line in script.splitlines(keepends=True):
        buffer.append(line)
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            cleaned = candidate.strip()
            if cleaned and cleaned != ";":
                statements.append(cleaned)
            buffer.clear()

    remainder = "".join(buffer).strip()
    if remainder and remainder != ";":
        statements.append(remainder)
    return statements


class DatabaseManager:
    """Manages an isolated SQLite database file for an individual application."""

    def __init__(
        self,
        app_id: str,
        db_path: str | Path | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.app_id = app_id
        self.timeout = timeout
        if db_path is not None:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.db_path = get_database_dir() / f"{app_id}.db"

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection with WAL mode and foreign keys enabled."""
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=self.timeout,
                isolation_level=None,  # Explicit transaction management
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            return conn
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Failed to connect to database '{self.db_path}': {exc}"
            ) from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager executing SQL operations inside an atomic BEGIN/COMMIT block."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            yield conn
            if conn.in_transaction:
                conn.execute("COMMIT;")
        except Exception as exc:
            if conn.in_transaction:
                with suppress(sqlite3.Error):
                    conn.execute("ROLLBACK;")
            if isinstance(exc, DatabaseError):
                raise
            raise DatabaseError(f"Database transaction failed for '{self.app_id}': {exc}") from exc
        finally:
            conn.close()

    @contextmanager
    def read_only(self) -> Iterator[sqlite3.Connection]:
        """Context manager yielding a connection for read queries."""
        conn = self.connect()
        try:
            yield conn
        except sqlite3.Error as exc:
            raise DatabaseError(f"Database read query failed for '{self.app_id}': {exc}") from exc
        finally:
            conn.close()

    def get_schema_version(self) -> int:
        """Return the current SQLite PRAGMA user_version integer."""
        with self.read_only() as conn:
            row = conn.execute("PRAGMA user_version;").fetchone()
            return int(row[0]) if row else 0

    def apply_migrations(self, migrations: Sequence[Migration]) -> int:
        """Apply any pending schema migrations in ascending version order."""
        ordered = sorted(migrations, key=lambda m: m.version)
        for index in range(1, len(ordered)):
            if ordered[index].version <= ordered[index - 1].version:
                raise DatabaseError(
                    f"Duplicate or non-increasing migration version: {ordered[index].version}"
                )

        current_version = self.get_schema_version()
        pending = [m for m in ordered if m.version > current_version]
        if not pending:
            return current_version

        # Create a safety backup before altering an existing populated schema
        if current_version > 0 and self.db_path.exists():
            self.create_backup(tag=f"pre_v{pending[-1].version}")

        for migration in pending:
            logger.info(
                "Applying %s database migration v%d: %s",
                self.app_id,
                migration.version,
                migration.description,
            )
            with self.transaction() as conn:
                if callable(migration.up):
                    migration.up(conn)
                else:
                    for statement in _split_sql_statements(migration.up):
                        conn.execute(statement)
                # PRAGMA user_version does not support '?' parameter binding
                conn.execute(f"PRAGMA user_version = {int(migration.version)};")

        final_version = self.get_schema_version()
        logger.info("%s database schema is now at v%d", self.app_id, final_version)
        return final_version

    def create_backup(
        self,
        destination_dir: Path | None = None,
        tag: str = "manual",
        max_backups: int = 10,
    ) -> Path:
        """Create a consistent hot backup of the SQLite database using SQLite's backup API."""
        target_dir = destination_dir or get_backups_dir(self.app_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_tag = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in tag)
        backup_path = target_dir / f"{self.app_id}_{safe_tag}_{timestamp}.db"

        source_conn = self.connect()
        try:
            dest_conn = sqlite3.connect(str(backup_path))
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        except sqlite3.Error as exc:
            if backup_path.exists():
                with suppress(OSError):
                    backup_path.unlink()
            raise DatabaseError(f"Failed to backup database '{self.app_id}': {exc}") from exc
        finally:
            source_conn.close()

        self._prune_backups(target_dir, max_backups=max_backups)
        logger.info("Created SQLite backup: %s", backup_path)
        return backup_path

    def _prune_backups(self, backup_dir: Path, max_backups: int) -> None:
        """Retain only the newest `max_backups` database backup files."""
        if max_backups < 1:
            return
        backups = sorted(
            (p for p in backup_dir.glob(f"{self.app_id}_*.db") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in backups[max_backups:]:
            with suppress(OSError):
                stale.unlink()
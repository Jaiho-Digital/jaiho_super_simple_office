"""SQLite WAL task, list, and subtask persistence repository for SimpleTasks."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum

from core.database.db_manager import DatabaseError, DatabaseManager, Migration

APP_ID = "simple_tasks"


class TaskPriority(IntEnum):
    """Priority tiers for tasks."""

    LOW = 1
    NORMAL = 2
    HIGH = 3

    @property
    def label(self) -> str:
        """Human-readable priority badge label."""
        if self == TaskPriority.HIGH:
            return "High"
        if self == TaskPriority.LOW:
            return "Low"
        return "Normal"


@dataclass(frozen=True, slots=True)
class SubtaskRecord:
    """Immutable representation of a single checklist item attached to a parent task."""

    subtask_id: int
    task_id: int
    title: str
    is_done: bool
    position: int


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """Immutable domain representation of a single task."""

    task_id: int
    title: str
    notes: str
    list_name: str
    priority: TaskPriority
    due_date: str | None  # ISO YYYY-MM-DD or None
    is_completed: bool
    completed_at: str | None
    created_at: str
    updated_at: str
    subtasks: tuple[SubtaskRecord, ...] = ()

    @property
    def completed_subtask_count(self) -> int:
        return sum(1 for s in self.subtasks if s.is_done)

    def is_overdue(self, reference_date: date | None = None) -> bool:
        """Return True if task is incomplete and its due_date is strictly before `reference_date`."""
        if self.is_completed or not self.due_date:
            return False
        ref = reference_date or date.today()
        try:
            return date.fromisoformat(self.due_date) < ref
        except ValueError:
            return False

    def is_due_today(self, reference_date: date | None = None) -> bool:
        """Return True if due_date equals `reference_date`."""
        if not self.due_date:
            return False
        ref = reference_date or date.today()
        return self.due_date == ref.isoformat()


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Create tasks and subtasks tables with proper indexes and cascade deletion."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            list_name TEXT NOT NULL DEFAULT 'Inbox',
            priority INTEGER NOT NULL DEFAULT 2,
            due_date TEXT,
            is_completed INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tasks_status_due
            ON tasks (is_completed, priority DESC, due_date ASC, updated_at DESC);
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subtasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            is_done INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_subtasks_task_id
            ON subtasks (task_id, position ASC, id ASC);
        """
    )


TASKS_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="Create tasks and subtasks tables with foreign key cascade",
        up=_migrate_v1,
    ),
)


def normalize_due_date(raw_due: str | date | None) -> str | None:
    """Validate and normalize a due date into ISO YYYY-MM-DD format, or None."""
    if raw_due is None:
        return None
    if isinstance(raw_due, date):
        return raw_due.isoformat()
    cleaned = str(raw_due).strip()
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError as exc:
        raise DatabaseError(f"Invalid due date '{cleaned}'. Expected YYYY-MM-DD.") from exc


class TasksRepository:
    """Data-access layer for managing tasks, smart views, lists, and subtasks."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or DatabaseManager(app_id=APP_ID)
        self.db.apply_migrations(TASKS_MIGRATIONS)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _normalize_list_name(raw_list: str | None) -> str:
        cleaned = (raw_list or "").strip()
        return cleaned if cleaned else "Inbox"

    @staticmethod
    def _row_to_subtask(row: sqlite3.Row) -> SubtaskRecord:
        return SubtaskRecord(
            subtask_id=int(row["id"]),
            task_id=int(row["task_id"]),
            title=str(row["title"]),
            is_done=bool(row["is_done"]),
            position=int(row["position"]),
        )

    @staticmethod
    def _row_to_task(
        row: sqlite3.Row,
        subtasks: tuple[SubtaskRecord, ...] = (),
    ) -> TaskRecord:
        try:
            prio = TaskPriority(int(row["priority"]))
        except ValueError:
            prio = TaskPriority.NORMAL

        return TaskRecord(
            task_id=int(row["id"]),
            title=str(row["title"]),
            notes=str(row["notes"] or ""),
            list_name=str(row["list_name"] or "Inbox"),
            priority=prio,
            due_date=str(row["due_date"]) if row["due_date"] else None,
            is_completed=bool(row["is_completed"]),
            completed_at=str(row["completed_at"]) if row["completed_at"] else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            subtasks=subtasks,
        )

    def _fetch_subtasks_map(
        self,
        conn: sqlite3.Connection,
        task_ids: list[int],
    ) -> dict[int, tuple[SubtaskRecord, ...]]:
        if not task_ids:
            return {}
        placeholders = ",".join("?" for _ in task_ids)
        rows = conn.execute(
            f"""
            SELECT id, task_id, title, is_done, position
            FROM subtasks
            WHERE task_id IN ({placeholders})
            ORDER BY position ASC, id ASC;
            """,
            tuple(task_ids),
        ).fetchall()

        grouped: dict[int, list[SubtaskRecord]] = {tid: [] for tid in task_ids}
        for r in rows:
            sub = self._row_to_subtask(r)
            grouped.setdefault(sub.task_id, []).append(sub)
        return {tid: tuple(items) for tid, items in grouped.items()}

    def create_task(
        self,
        title: str,
        *,
        notes: str = "",
        list_name: str = "Inbox",
        priority: TaskPriority = TaskPriority.NORMAL,
        due_date: str | date | None = None,
    ) -> TaskRecord:
        """Create and persist a new task."""
        clean_title = title.strip()
        if not clean_title:
            raise DatabaseError("Task title cannot be empty.")

        norm_list = self._normalize_list_name(list_name)
        norm_due = normalize_due_date(due_date)
        now = self._now_iso()

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    title, notes, list_name, priority, due_date,
                    is_completed, completed_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?);
                """,
                (clean_title, notes.strip(), norm_list, int(priority), norm_due, now, now),
            )
            new_id = cursor.lastrowid
            if new_id is None:
                raise DatabaseError("Failed to retrieve inserted task ID.")

        created = self.get_task(int(new_id))
        if created is None:
            raise DatabaseError(f"Inserted task #{new_id} could not be loaded.")
        return created

    def get_task(self, task_id: int) -> TaskRecord | None:
        """Return a single hydrated TaskRecord with all its subtasks, or None."""
        with self.db.read_only() as conn:
            row = conn.execute(
                """
                SELECT id, title, notes, list_name, priority, due_date,
                       is_completed, completed_at, created_at, updated_at
                FROM tasks
                WHERE id = ?;
                """,
                (int(task_id),),
            ).fetchone()
            if row is None:
                return None
            subs_map = self._fetch_subtasks_map(conn, [int(task_id)])
            return self._row_to_task(row, subs_map.get(int(task_id), ()))

    def update_task(
        self,
        task_id: int,
        *,
        title: str | None = None,
        notes: str | None = None,
        list_name: str | None = None,
        priority: TaskPriority | None = None,
        due_date: str | date | None = ...,  # type: ignore[assignment]
        is_completed: bool | None = None,
    ) -> TaskRecord:
        """Update selected fields on an existing task."""
        existing = self.get_task(task_id)
        if existing is None:
            raise DatabaseError(f"Cannot update missing task #{task_id}.")

        next_title = (title.strip() or existing.title) if title is not None else existing.title
        next_notes = notes if notes is not None else existing.notes
        next_list = (
            self._normalize_list_name(list_name)
            if list_name is not None
            else existing.list_name
        )
        next_priority = int(priority) if priority is not None else int(existing.priority)
        next_due = (
            existing.due_date
            if due_date is ...
            else normalize_due_date(due_date)
        )

        now = self._now_iso()
        if is_completed is not None:
            next_completed = int(is_completed)
            next_completed_at = now if is_completed else None
        else:
            next_completed = int(existing.is_completed)
            next_completed_at = existing.completed_at

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET title = ?,
                    notes = ?,
                    list_name = ?,
                    priority = ?,
                    due_date = ?,
                    is_completed = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    next_title,
                    next_notes,
                    next_list,
                    next_priority,
                    next_due,
                    next_completed,
                    next_completed_at,
                    now,
                    int(task_id),
                ),
            )

        updated = self.get_task(task_id)
        if updated is None:
            raise DatabaseError(f"Updated task #{task_id} could not be loaded.")
        return updated

    def toggle_task_completed(self, task_id: int) -> TaskRecord:
        """Toggle completion status on a task."""
        existing = self.get_task(task_id)
        if existing is None:
            raise DatabaseError(f"Cannot toggle missing task #{task_id}.")
        return self.update_task(task_id, is_completed=not existing.is_completed)

    def delete_task(self, task_id: int) -> bool:
        """Permanently delete a task and its cascaded subtasks."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?;", (int(task_id),))
            return cursor.rowcount > 0

    def clear_completed_tasks(self) -> int:
        """Delete all completed tasks and return the number of removed rows."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE is_completed = 1;")
            return int(cursor.rowcount or 0)

    # ------------------------------------------------------------------
    # Subtask Operations
    # ------------------------------------------------------------------

    def add_subtask(self, task_id: int, title: str) -> SubtaskRecord:
        """Append a new checklist subtask to `task_id`."""
        clean_title = title.strip()
        if not clean_title:
            raise DatabaseError("Subtask title cannot be empty.")
        if self.get_task(task_id) is None:
            raise DatabaseError(f"Cannot add subtask to missing task #{task_id}.")

        with self.db.transaction() as conn:
            pos_row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM subtasks WHERE task_id = ?;",
                (int(task_id),),
            ).fetchone()
            next_pos = int(pos_row[0]) if pos_row else 0

            cursor = conn.execute(
                """
                INSERT INTO subtasks (task_id, title, is_done, position)
                VALUES (?, ?, 0, ?);
                """,
                (int(task_id), clean_title, next_pos),
            )
            sub_id = cursor.lastrowid
            if sub_id is None:
                raise DatabaseError("Failed to retrieve inserted subtask ID.")
            conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?;",
                (self._now_iso(), int(task_id)),
            )
            row = conn.execute(
                "SELECT id, task_id, title, is_done, position FROM subtasks WHERE id = ?;",
                (int(sub_id),),
            ).fetchone()
            if row is None:
                raise DatabaseError("Inserted subtask could not be loaded.")
            return self._row_to_subtask(row)

    def toggle_subtask(self, subtask_id: int) -> SubtaskRecord:
        """Flip completion state on an existing subtask."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id, task_id, title, is_done, position FROM subtasks WHERE id = ?;",
                (int(subtask_id),),
            ).fetchone()
            if row is None:
                raise DatabaseError(f"Missing subtask #{subtask_id}.")
            next_done = 0 if bool(row["is_done"]) else 1
            conn.execute(
                "UPDATE subtasks SET is_done = ? WHERE id = ?;",
                (next_done, int(subtask_id)),
            )
            updated = conn.execute(
                "SELECT id, task_id, title, is_done, position FROM subtasks WHERE id = ?;",
                (int(subtask_id),),
            ).fetchone()
            assert updated is not None
            return self._row_to_subtask(updated)

    def delete_subtask(self, subtask_id: int) -> bool:
        """Delete a single subtask by ID."""
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM subtasks WHERE id = ?;", (int(subtask_id),))
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Smart Views, Lists & Search Queries
    # ------------------------------------------------------------------

    def list_tasks(
        self,
        *,
        smart_view: str = "all",  # 'all' | 'today' | 'upcoming' | 'high' | 'completed'
        list_filter: str = "",
        search_query: str = "",
        reference_date: date | None = None,
    ) -> list[TaskRecord]:
        """Query tasks filtered by smart view, custom list, and text search."""
        today_iso = (reference_date or date.today()).isoformat()
        where_clauses: list[str] = []
        params: list[object] = []

        if smart_view == "completed":
            where_clauses.append("is_completed = 1")
        else:
            where_clauses.append("is_completed = 0")
            if smart_view == "today":
                where_clauses.append("due_date IS NOT NULL AND due_date <= ?")
                params.append(today_iso)
            elif smart_view == "upcoming":
                where_clauses.append("due_date IS NOT NULL AND due_date > ?")
                params.append(today_iso)
            elif smart_view == "high":
                where_clauses.append("priority = ?")
                params.append(int(TaskPriority.HIGH))

        clean_list = list_filter.strip()
        if clean_list:
            where_clauses.append("LOWER(list_name) = LOWER(?)")
            params.append(clean_list)

        clean_query = search_query.strip()
        if clean_query:
            pat = f"%{clean_query}%"
            where_clauses.append("(title LIKE ? OR notes LIKE ? OR list_name LIKE ?)")
            params.extend([pat, pat, pat])

        order_sql = (
            "completed_at DESC, id DESC"
            if smart_view == "completed"
            else "priority DESC, CASE WHEN due_date IS NULL THEN 1 ELSE 0 END ASC, due_date ASC, id DESC"
        )

        sql = f"""
            SELECT id, title, notes, list_name, priority, due_date,
                   is_completed, completed_at, created_at, updated_at
            FROM tasks
            WHERE {' AND '.join(where_clauses)}
            ORDER BY {order_sql};
        """

        with self.db.read_only() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            task_ids = [int(r["id"]) for r in rows]
            subs_map = self._fetch_subtasks_map(conn, task_ids)
            return [self._row_to_task(r, subs_map.get(int(r["id"]), ())) for r in rows]

    def get_summary_counts(self, reference_date: date | None = None) -> dict[str, int]:
        """Return counts for all sidebar smart views."""
        today_iso = (reference_date or date.today()).isoformat()
        with self.db.read_only() as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN is_completed = 0 THEN 1 ELSE 0 END) AS all_active,
                    SUM(CASE WHEN is_completed = 0 AND due_date IS NOT NULL AND due_date <= ? THEN 1 ELSE 0 END) AS today,
                    SUM(CASE WHEN is_completed = 0 AND due_date IS NOT NULL AND due_date > ? THEN 1 ELSE 0 END) AS upcoming,
                    SUM(CASE WHEN is_completed = 0 AND priority = 3 THEN 1 ELSE 0 END) AS high,
                    SUM(CASE WHEN is_completed = 1 THEN 1 ELSE 0 END) AS completed
                FROM tasks;
                """,
                (today_iso, today_iso),
            ).fetchone()
            if row is None:
                return {"all": 0, "today": 0, "upcoming": 0, "high": 0, "completed": 0}
            return {
                "all": int(row["all_active"] or 0),
                "today": int(row["today"] or 0),
                "upcoming": int(row["upcoming"] or 0),
                "high": int(row["high"] or 0),
                "completed": int(row["completed"] or 0),
            }

    def list_custom_lists(self) -> list[tuple[str, int]]:
        """Return active task counts grouped by list_name."""
        counts: Counter[str] = Counter()
        with self.db.read_only() as conn:
            for row in conn.execute(
                "SELECT list_name FROM tasks WHERE is_completed = 0;"
            ).fetchall():
                counts[str(row["list_name"] or "Inbox")] += 1
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].lower()))

    def seed_starter_tasks_if_empty(self) -> None:
        """Populate two lightweight starter tasks on first launch."""
        with self.db.read_only() as conn:
            row = conn.execute("SELECT COUNT(*) FROM tasks;").fetchone()
            if row is not None and int(row[0]) > 0:
                return

        t1 = self.create_task(
            title="Review daily priorities in SimpleTasks",
            notes="Use the quick-add bar at the top to capture new tasks with Enter.",
            list_name="Inbox",
            priority=TaskPriority.HIGH,
            due_date=date.today(),
        )
        self.add_subtask(t1.task_id, "Check Today and Upcoming smart views")
        self.add_subtask(t1.task_id, "Try switching between Light and Dark theme (Ctrl+Shift+T)")
"""SQLite persistence for race schedules, race details, and entries."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from scripts.jvlink_loader import (
    RaceEntry,
    RaceHistoryEntry,
    RaceList,
    RaceSchedule,
)

DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parents[1] / "database" / "horse_racing.db"
)


class StorageError(RuntimeError):
    """Raised when race data cannot be saved to SQLite."""


class RaceRepository:
    """Create and update the Phase 3 SQLite tables."""

    def __init__(self, database_path: Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        """Create the database directory and all required tables."""
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS race_schedule (
                        race_date TEXT NOT NULL,
                        racecourse TEXT NOT NULL,
                        meeting_no INTEGER NOT NULL,
                        race_count INTEGER NOT NULL,
                        PRIMARY KEY (race_date, racecourse, meeting_no)
                    );

                    CREATE TABLE IF NOT EXISTS race_list (
                        race_key TEXT PRIMARY KEY,
                        race_date TEXT NOT NULL,
                        racecourse_code TEXT NOT NULL,
                        racecourse TEXT NOT NULL,
                        meeting_no INTEGER NOT NULL,
                        day_no INTEGER NOT NULL,
                        race_no INTEGER NOT NULL,
                        race_name TEXT NOT NULL,
                        distance INTEGER NOT NULL,
                        surface TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        race_condition TEXT NOT NULL,
                        start_time TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS race_entries (
                        race_key TEXT NOT NULL,
                        horse_no INTEGER NOT NULL,
                        race_date TEXT NOT NULL,
                        racecourse_code TEXT NOT NULL,
                        racecourse TEXT NOT NULL,
                        meeting_no INTEGER NOT NULL,
                        day_no INTEGER NOT NULL,
                        race_no INTEGER NOT NULL,
                        gate_no INTEGER NOT NULL,
                        horse_name TEXT NOT NULL,
                        jockey_name TEXT NOT NULL,
                        trainer_name TEXT NOT NULL,
                        sex_age TEXT NOT NULL,
                        assigned_weight REAL NOT NULL,
                        popularity INTEGER,
                        odds REAL,
                        PRIMARY KEY (race_key, horse_no)
                    );

                    CREATE TABLE IF NOT EXISTS race_history (
                        race_key TEXT NOT NULL,
                        horse_no INTEGER NOT NULL,
                        race_date TEXT NOT NULL,
                        racecourse_code TEXT NOT NULL,
                        racecourse TEXT NOT NULL,
                        meeting_no INTEGER NOT NULL,
                        day_no INTEGER NOT NULL,
                        race_no INTEGER NOT NULL,
                        race_name TEXT NOT NULL,
                        distance INTEGER NOT NULL,
                        surface TEXT NOT NULL,
                        direction TEXT NOT NULL DEFAULT '不明',
                        track_layout TEXT NOT NULL DEFAULT 'なし',
                        race_class TEXT NOT NULL DEFAULT '未設定',
                        track_condition TEXT NOT NULL,
                        weather TEXT NOT NULL,
                        horse_name TEXT NOT NULL,
                        jockey_name TEXT NOT NULL,
                        popularity INTEGER,
                        odds REAL,
                        finish_position INTEGER NOT NULL,
                        race_time TEXT NOT NULL,
                        last_3f REAL,
                        passing_order TEXT NOT NULL,
                        body_weight INTEGER,
                        assigned_weight REAL NOT NULL,
                        PRIMARY KEY (race_key, horse_no)
                    );

                    CREATE TABLE IF NOT EXISTS history_collection_progress (
                        range_start TEXT PRIMARY KEY,
                        completed_through TEXT NOT NULL
                    );
                    """
                )
                self._ensure_history_columns(connection)
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(f"SQLite初期化失敗: {exc}") from exc

    def save_schedule(self, schedule: Sequence[RaceSchedule]) -> int:
        """Upsert race meeting summaries."""
        rows = [
            (item.date.isoformat(), item.racecourse, item.meeting_no, item.race_count)
            for item in schedule
        ]
        return self._save_many(
            """
            INSERT INTO race_schedule
                (race_date, racecourse, meeting_no, race_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(race_date, racecourse, meeting_no) DO UPDATE SET
                race_count=excluded.race_count
            """,
            rows,
        )

    def save_races(self, races: Sequence[RaceList]) -> int:
        """Upsert detailed races."""
        rows = [
            (
                race.race_key,
                race.date.isoformat(),
                race.racecourse_code,
                race.racecourse,
                race.meeting_no,
                race.day_no,
                race.race_no,
                race.race_name,
                race.distance,
                race.surface,
                race.direction,
                race.condition,
                race.start_time,
            )
            for race in races
        ]
        return self._save_many(
            """
            INSERT INTO race_list VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_key) DO UPDATE SET
                race_name=excluded.race_name,
                distance=excluded.distance,
                surface=excluded.surface,
                direction=excluded.direction,
                race_condition=excluded.race_condition,
                start_time=excluded.start_time
            """,
            rows,
        )

    def save_entries(self, entries: Sequence[RaceEntry]) -> int:
        """Upsert runners for one race."""
        rows = [
            (
                entry.race_key,
                entry.horse_no,
                entry.date.isoformat(),
                entry.racecourse_code,
                entry.racecourse,
                entry.meeting_no,
                entry.day_no,
                entry.race_no,
                entry.gate_no,
                entry.horse_name,
                entry.jockey_name,
                entry.trainer_name,
                entry.sex_age,
                entry.assigned_weight,
                entry.popularity,
                entry.odds,
            )
            for entry in entries
        ]
        return self._save_many(
            """
            INSERT INTO race_entries VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_key, horse_no) DO UPDATE SET
                gate_no=excluded.gate_no,
                horse_name=excluded.horse_name,
                jockey_name=excluded.jockey_name,
                trainer_name=excluded.trainer_name,
                sex_age=excluded.sex_age,
                assigned_weight=excluded.assigned_weight,
                popularity=excluded.popularity,
                odds=excluded.odds
            """,
            rows,
        )

    def history_resume_date(self, requested_from: date) -> date:
        """Return the first uncompleted date for a collection range."""
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT completed_through
                    FROM history_collection_progress
                    WHERE range_start = ?
                    """,
                    (requested_from.isoformat(),),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"SQLite再開位置取得失敗: {exc}") from exc
        if row is None:
            return requested_from
        return date.fromisoformat(str(row[0])) + timedelta(days=1)

    def save_history(
        self,
        entries: Sequence[RaceHistoryEntry],
        range_start: date,
        completed_through: date,
    ) -> int:
        """Insert new history rows and checkpoint each completed race day."""
        self.initialize()
        entries_by_date: dict[date, list[RaceHistoryEntry]] = defaultdict(list)
        for entry in entries:
            entries_by_date[entry.date].append(entry)

        saved_count = 0
        try:
            for race_date in sorted(entries_by_date):
                with self._connect() as connection:
                    before_changes = connection.total_changes
                    connection.executemany(
                        """
                        INSERT OR IGNORE INTO race_history
                            (race_key, horse_no, race_date, racecourse_code,
                             racecourse, meeting_no, day_no, race_no,
                             race_name, distance, surface, direction,
                             track_layout, race_class, track_condition,
                             weather, horse_name, jockey_name, popularity,
                             odds, finish_position, race_time, last_3f,
                             passing_order, body_weight, assigned_weight)
                        VALUES
                            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            self._history_row(entry)
                            for entry in entries_by_date[race_date]
                        ],
                    )
                    saved_count += connection.total_changes - before_changes
                    self._save_history_progress(connection, range_start, race_date)

            with self._connect() as connection:
                self._save_history_progress(
                    connection, range_start, completed_through
                )
        except sqlite3.Error as exc:
            raise StorageError(f"SQLite履歴保存失敗: {exc}") from exc
        return saved_count

    @staticmethod
    def _history_row(entry: RaceHistoryEntry) -> tuple[object, ...]:
        return (
            entry.race_key,
            entry.horse_no,
            entry.date.isoformat(),
            entry.racecourse_code,
            entry.racecourse,
            entry.meeting_no,
            entry.day_no,
            entry.race_no,
            entry.race_name,
            entry.distance,
            entry.surface,
            entry.direction,
            entry.track_layout,
            entry.race_class,
            entry.track_condition,
            entry.weather,
            entry.horse_name,
            entry.jockey_name,
            entry.popularity,
            entry.odds,
            entry.finish_position,
            entry.race_time,
            entry.last_3f,
            entry.passing_order,
            entry.body_weight,
            entry.assigned_weight,
        )

    @staticmethod
    def _ensure_history_columns(connection: sqlite3.Connection) -> None:
        """Add Phase 4 source columns to an existing Phase 3 database."""
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(race_history)")
        }
        additions = {
            "direction": "TEXT NOT NULL DEFAULT '不明'",
            "track_layout": "TEXT NOT NULL DEFAULT 'なし'",
            "race_class": "TEXT NOT NULL DEFAULT '未設定'",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE race_history ADD COLUMN {name} {definition}"
                )

    @staticmethod
    def _save_history_progress(
        connection: sqlite3.Connection, range_start: date, completed: date
    ) -> None:
        connection.execute(
            """
            INSERT INTO history_collection_progress
                (range_start, completed_through)
            VALUES (?, ?)
            ON CONFLICT(range_start) DO UPDATE SET
                completed_through=MAX(
                    history_collection_progress.completed_through,
                    excluded.completed_through
                )
            """,
            (range_start.isoformat(), completed.isoformat()),
        )

    def _save_many(self, statement: str, rows: Sequence[tuple[object, ...]]) -> int:
        self.initialize()
        try:
            with self._connect() as connection:
                connection.executemany(statement, rows)
        except sqlite3.Error as exc:
            raise StorageError(f"SQLite保存失敗: {exc}") from exc
        return len(rows)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

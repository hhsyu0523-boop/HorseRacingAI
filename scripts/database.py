"""SQLite persistence for race schedules, race details, and entries."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from scripts.jvlink_loader import RaceEntry, RaceList, RaceSchedule

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
                    """
                )
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

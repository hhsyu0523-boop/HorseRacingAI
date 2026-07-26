"""Leakage-safe feature generation from the historical race database."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from statistics import fmean

from scripts.database import DEFAULT_DATABASE_PATH, RaceRepository, StorageError

RACE_CLASSES = ("新馬", "未勝利", "1勝", "2勝", "3勝", "OP", "G3", "G2", "G1")


@dataclass(frozen=True)
class FeatureBuildResult:
    """Summary returned after rebuilding feature_history."""

    source_count: int
    saved_count: int


@dataclass(frozen=True)
class _Run:
    race_key: str
    race_date: date
    racecourse_code: str
    distance: int
    surface: str
    direction: str
    track_layout: str
    track_condition: str
    race_class: str
    horse_no: int
    horse_name: str
    jockey_name: str
    popularity: int | None
    finish_position: int
    last_3f: float | None
    race_seconds: float | None
    popularity_diff: float | None = None
    time_diff: float | None = None


@dataclass
class _Performance:
    starts: int = 0
    wins: int = 0
    top2: int = 0
    top3: int = 0

    def add(self, finish_position: int) -> None:
        self.starts += 1
        self.wins += finish_position == 1
        self.top2 += finish_position <= 2
        self.top3 += finish_position <= 3

    def rates(self) -> tuple[float, float, float]:
        if self.starts == 0:
            return 0.0, 0.0, 0.0
        return (
            self.wins / self.starts,
            self.top2 / self.starts,
            self.top3 / self.starts,
        )


class FeatureEngineeringEngine:
    """Build training rows using only races earlier than each target race."""

    def __init__(self, database_path: Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = database_path

    def build(self) -> FeatureBuildResult:
        """Rebuild feature_history atomically from race_history."""
        RaceRepository(self.database_path).initialize()
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                self._initialize_table(connection)
                runs = self._load_runs(connection)
                features = self._generate_features(runs)
                with connection:
                    connection.execute("DELETE FROM feature_history")
                    connection.executemany(self._insert_statement(), features)
        except sqlite3.Error as exc:
            raise StorageError(f"特徴量SQLite処理失敗: {exc}") from exc
        return FeatureBuildResult(len(runs), len(features))

    @staticmethod
    def _initialize_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_history (
                race_key TEXT NOT NULL,
                horse_no INTEGER NOT NULL,
                race_date TEXT NOT NULL,
                horse_name TEXT NOT NULL,
                jockey_name TEXT NOT NULL,
                past_race_count INTEGER NOT NULL,
                past_1_finish INTEGER,
                past_2_finish INTEGER,
                past_3_finish INTEGER,
                past_4_finish INTEGER,
                past_5_finish INTEGER,
                average_finish REAL NOT NULL,
                horse_win_rate REAL NOT NULL,
                horse_top2_rate REAL NOT NULL,
                horse_top3_rate REAL NOT NULL,
                average_popularity REAL NOT NULL,
                average_popularity_diff REAL NOT NULL,
                average_last_3f REAL NOT NULL,
                average_time_diff REAL NOT NULL,
                jockey_race_count INTEGER NOT NULL,
                jockey_win_rate REAL NOT NULL,
                jockey_top2_rate REAL NOT NULL,
                jockey_top3_rate REAL NOT NULL,
                racecourse_code TEXT NOT NULL,
                distance INTEGER NOT NULL,
                surface TEXT NOT NULL,
                direction TEXT NOT NULL,
                track_layout TEXT NOT NULL,
                going_good INTEGER NOT NULL,
                going_slightly_heavy INTEGER NOT NULL,
                going_heavy INTEGER NOT NULL,
                going_bad INTEGER NOT NULL,
                days_since_last_race INTEGER NOT NULL,
                class_newcomer INTEGER NOT NULL,
                class_maiden INTEGER NOT NULL,
                class_1win INTEGER NOT NULL,
                class_2win INTEGER NOT NULL,
                class_3win INTEGER NOT NULL,
                class_open INTEGER NOT NULL,
                class_g3 INTEGER NOT NULL,
                class_g2 INTEGER NOT NULL,
                class_g1 INTEGER NOT NULL,
                target_finish_position INTEGER NOT NULL,
                PRIMARY KEY (race_key, horse_no)
            )
            """
        )

    @staticmethod
    def _load_runs(connection: sqlite3.Connection) -> list[_Run]:
        rows = connection.execute(
            """
            SELECT race_key, race_date, racecourse_code, distance, surface,
                   direction, track_layout, track_condition, race_class,
                   horse_no, horse_name, jockey_name, popularity,
                   finish_position, last_3f, race_time
            FROM race_history
            ORDER BY race_date, race_key, horse_no
            """
        ).fetchall()
        runs = [
            _Run(
                race_key=str(row["race_key"]),
                race_date=date.fromisoformat(str(row["race_date"])),
                racecourse_code=str(row["racecourse_code"]),
                distance=int(row["distance"]),
                surface=str(row["surface"]),
                direction=str(row["direction"]),
                track_layout=str(row["track_layout"]),
                track_condition=str(row["track_condition"]),
                race_class=_normalize_class(str(row["race_class"])),
                horse_no=int(row["horse_no"]),
                horse_name=str(row["horse_name"]),
                jockey_name=str(row["jockey_name"]),
                popularity=_optional_int(row["popularity"]),
                finish_position=int(row["finish_position"]),
                last_3f=_optional_float(row["last_3f"]),
                race_seconds=_race_time_seconds(str(row["race_time"])),
            )
            for row in rows
        ]
        return FeatureEngineeringEngine._add_race_relative_values(runs)

    @staticmethod
    def _add_race_relative_values(runs: list[_Run]) -> list[_Run]:
        grouped: dict[str, list[_Run]] = defaultdict(list)
        for run in runs:
            grouped[run.race_key].append(run)

        enriched: list[_Run] = []
        for race_runs in grouped.values():
            popularities = [
                run.popularity
                for run in race_runs
                if run.popularity is not None
            ]
            mean_popularity = fmean(popularities) if popularities else None
            winner_times = [
                r.race_seconds
                for r in race_runs
                if r.finish_position == 1 and r.race_seconds is not None
            ]
            winner_time = min(winner_times) if winner_times else None
            for run in race_runs:
                popularity_diff = (
                    run.popularity - mean_popularity
                    if run.popularity is not None and mean_popularity is not None
                    else None
                )
                time_diff = (
                    run.race_seconds - winner_time
                    if run.race_seconds is not None and winner_time is not None
                    else None
                )
                enriched.append(
                    replace(
                        run,
                        popularity_diff=popularity_diff,
                        time_diff=time_diff,
                    )
                )
        return sorted(
            enriched,
            key=lambda run: (run.race_date, run.race_key, run.horse_no),
        )

    @staticmethod
    def _generate_features(runs: list[_Run]) -> list[tuple[object, ...]]:
        horse_history: dict[str, list[_Run]] = defaultdict(list)
        jockey_history: dict[str, _Performance] = defaultdict(_Performance)
        rows: list[tuple[object, ...]] = []

        by_date: dict[date, list[_Run]] = defaultdict(list)
        for run in runs:
            by_date[run.race_date].append(run)

        for race_date in sorted(by_date):
            day_runs = by_date[race_date]
            for run in day_runs:
                previous = horse_history[run.horse_name][-5:]
                jockey = jockey_history[run.jockey_name]
                rows.append(_feature_row(run, previous, jockey))
            for run in day_runs:
                horse_history[run.horse_name].append(run)
                jockey_history[run.jockey_name].add(run.finish_position)
        return rows

    @staticmethod
    def _insert_statement() -> str:
        return "INSERT INTO feature_history VALUES (" + ",".join("?" * 43) + ")"


def _feature_row(
    run: _Run, previous: list[_Run], jockey: _Performance
) -> tuple[object, ...]:
    recent = list(reversed(previous))
    finishes: list[int | None] = [item.finish_position for item in recent]
    finishes.extend([None] * (5 - len(finishes)))
    win_rate, top2_rate, top3_rate = _rates(previous)
    jockey_win, jockey_top2, jockey_top3 = jockey.rates()
    class_flags = tuple(int(run.race_class == value) for value in RACE_CLASSES)
    days_since = (run.race_date - recent[0].race_date).days if recent else 0
    return (
        run.race_key,
        run.horse_no,
        run.race_date.isoformat(),
        run.horse_name,
        run.jockey_name,
        len(previous),
        *finishes,
        _mean([item.finish_position for item in previous]),
        win_rate,
        top2_rate,
        top3_rate,
        _mean_optional([item.popularity for item in previous]),
        _mean_optional([item.popularity_diff for item in previous]),
        _mean_optional([item.last_3f for item in previous]),
        _mean_optional([item.time_diff for item in previous]),
        jockey.starts,
        jockey_win,
        jockey_top2,
        jockey_top3,
        run.racecourse_code,
        run.distance,
        run.surface,
        run.direction,
        run.track_layout,
        int(run.track_condition == "良"),
        int(run.track_condition == "稍重"),
        int(run.track_condition == "重"),
        int(run.track_condition == "不良"),
        days_since,
        *class_flags,
        run.finish_position,
    )


def _rates(runs: list[_Run]) -> tuple[float, float, float]:
    if not runs:
        return 0.0, 0.0, 0.0
    count = len(runs)
    return (
        sum(run.finish_position == 1 for run in runs) / count,
        sum(run.finish_position <= 2 for run in runs) / count,
        sum(run.finish_position <= 3 for run in runs) / count,
    )


def _mean(values: list[int]) -> float:
    return fmean(values) if values else 0.0


def _mean_optional(values: list[float | int | None]) -> float:
    available = [float(value) for value in values if value is not None]
    return fmean(available) if available else 0.0


def _race_time_seconds(value: str) -> float | None:
    try:
        minutes, seconds = value.split(":", maxsplit=1)
        return int(minutes) * 60 + float(seconds)
    except (ValueError, AttributeError):
        return None


def _normalize_class(value: str) -> str:
    aliases = {
        "新馬": "新馬",
        "未出走": "新馬",
        "未勝利": "未勝利",
        "400万円以下": "1勝",
        "1勝クラス": "1勝",
        "600万円以下": "2勝",
        "700万円以下": "2勝",
        "800万円以下": "2勝",
        "900万円以下": "2勝",
        "2勝クラス": "2勝",
        "3勝クラス": "3勝",
        "オープン": "OP",
    }
    return aliases.get(value, value if value in RACE_CLASSES else "未設定")


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None

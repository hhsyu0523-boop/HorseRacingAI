"""Expanding-window rolling backtest with automatic reports."""

from __future__ import annotations

import csv
import html
import sqlite3
import uuid
from collections import defaultdict
from contextlib import closing
from dataclasses import astuple, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from scripts.database import DEFAULT_DATABASE_PATH, RaceRepository
from scripts.ensemble_predict import EnsembleConfig, load_ensemble_config
from scripts.train_model import (
    CATEGORICAL_COLUMNS,
    EXCLUDED_COLUMNS,
    ModelError,
    _labels,
    _load_lightgbm,
    _transform_rows,
)
from scripts.train_xgboost import _load_xgboost

DEFAULT_REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


@dataclass(frozen=True)
class BacktestRecord:
    race_key: str
    race_date: str
    racecourse: str
    distance: int
    track_condition: str
    horse_no: int
    horse_name: str
    popularity: int | None
    actual_finish: int
    win_probability: float
    place_probability: float
    ai_score: float
    win_hit: int
    place_hit: int
    win_return: int
    place_return: int
    payout_missing: int


@dataclass(frozen=True)
class BacktestResult:
    run_id: str
    race_count: int
    skipped_count: int
    records: tuple[BacktestRecord, ...]
    summary: dict[str, float]
    csv_path: Path
    report_path: Path
    importance_path: Path


class RollingBacktestEngine:
    """Retrain on all prior races and predict the next race repeatedly."""

    def __init__(
        self,
        database_path: Path = DEFAULT_DATABASE_PATH,
        reports_dir: Path = DEFAULT_REPORTS_DIR,
        min_training_races: int = 20,
        config_path: Path | None = None,
        num_boost_round: int = 100,
    ) -> None:
        if min_training_races < 1:
            raise ValueError("min_training_racesは1以上にしてください")
        if num_boost_round < 1:
            raise ValueError("num_boost_roundは1以上にしてください")
        self.database_path = database_path
        self.reports_dir = reports_dir
        self.min_training_races = min_training_races
        self.config_path = config_path
        self.num_boost_round = num_boost_round

    def run(
        self, from_date: date | None = None, to_date: date | None = None
    ) -> BacktestResult:
        """Execute an expanding-window backtest for the requested period."""
        if from_date and to_date and from_date > to_date:
            raise ValueError("開始日は終了日以前を指定してください")
        lightgbm = _load_lightgbm()
        xgboost = _load_xgboost()
        config = (
            load_ensemble_config(self.config_path)
            if self.config_path
            else load_ensemble_config()
        )
        rows, feature_names = self._load_rows()
        races = _group_races(rows)
        if len(races) <= self.min_training_races:
            raise ModelError("バックテストに必要なレース数が不足しています")

        records: list[BacktestRecord] = []
        prior_rows: list[sqlite3.Row] = []
        importance: dict[str, float] = defaultdict(float)
        skipped = 0
        for race_index, race_rows in enumerate(races):
            race_day = date.fromisoformat(str(race_rows[0]["race_date"]))
            in_period = (from_date is None or race_day >= from_date) and (
                to_date is None or race_day <= to_date
            )
            if race_index < self.min_training_races or not in_period:
                prior_rows.extend(race_rows)
                continue
            try:
                record = self._predict_race(
                    lightgbm,
                    xgboost,
                    prior_rows,
                    race_rows,
                    feature_names,
                    config,
                    importance,
                )
            except ModelError:
                skipped += 1
            else:
                records.append(record)
            prior_rows.extend(race_rows)

        if not records:
            raise ModelError("評価可能なバックテスト結果がありません")
        run_id = uuid.uuid4().hex
        self._save_history(run_id, records)
        paths = self._write_reports(run_id, records, importance, skipped)
        return BacktestResult(
            run_id,
            len(records),
            skipped,
            tuple(records),
            _summary(records),
            paths[0],
            paths[1],
            paths[2],
        )

    def _load_rows(self) -> tuple[list[sqlite3.Row], list[str]]:
        if not self.database_path.exists():
            raise ModelError(f"SQLiteが存在しません: {self.database_path}")
        RaceRepository(self.database_path).initialize()
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                columns = [
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(feature_history)"
                    )
                ]
                if not columns:
                    raise ModelError("先にbuild-featuresを実行してください")
                rows = connection.execute(
                    """
                    SELECT f.*, h.racecourse, h.distance AS history_distance,
                           h.track_condition, h.popularity, h.odds,
                           h.win_payout, h.place_payout
                    FROM feature_history AS f
                    JOIN race_history AS h
                      ON h.race_key=f.race_key AND h.horse_no=f.horse_no
                    ORDER BY f.race_date,
                             CAST(SUBSTR(f.race_key, 11, 2) AS INTEGER),
                             SUBSTR(f.race_key, 9, 2), f.horse_no
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise ModelError(f"バックテストデータ読込失敗: {exc}") from exc
        feature_names = [name for name in columns if name not in EXCLUDED_COLUMNS]
        return rows, feature_names

    def _predict_race(
        self,
        lightgbm: Any,
        xgboost: Any,
        training_rows: list[sqlite3.Row],
        race_rows: list[sqlite3.Row],
        feature_names: list[str],
        config: EnsembleConfig,
        importance: dict[str, float],
    ) -> BacktestRecord:
        category_maps = _category_maps(training_rows, feature_names)
        x_train = _transform_rows(training_rows, feature_names, category_maps)
        x_test = _transform_rows(race_rows, feature_names, category_maps)
        probabilities: dict[str, list[float]] = {}
        for label, threshold in (("winner", 1), ("place", 3)):
            y_train = _labels(training_rows, threshold)
            if len(set(y_train)) < 2:
                raise ModelError(f"{label}の学習クラスが不足しています")
            lgb_values, lgb_importance = _fit_lightgbm(
                lightgbm,
                x_train,
                y_train,
                x_test,
                feature_names,
                self.num_boost_round,
            )
            xgb_values, xgb_importance = _fit_xgboost(
                xgboost,
                x_train,
                y_train,
                x_test,
                feature_names,
                self.num_boost_round,
            )
            weights = config.winner if label == "winner" else config.place
            probabilities[label] = [
                lgb * weights.lightgbm + xgb * weights.xgboost
                for lgb, xgb in zip(lgb_values, xgb_values)
            ]
            for name, value in lgb_importance.items():
                importance[f"lightgbm_{label}:{name}"] += value
            for name, value in xgb_importance.items():
                importance[f"xgboost_{label}:{name}"] += value

        scores = [
            100
            * (
                winner * config.ai_score.winner
                + place * config.ai_score.place
            )
            for winner, place in zip(
                probabilities["winner"], probabilities["place"]
            )
        ]
        selected_index = max(range(len(scores)), key=scores.__getitem__)
        selected = race_rows[selected_index]
        finish = int(selected["target_finish_position"])
        win_hit = int(finish == 1)
        place_hit = int(finish <= 3)
        win_payout = _optional_int(selected["win_payout"])
        place_payout = _optional_int(selected["place_payout"])
        win_return = win_payout if win_hit and win_payout is not None else 0
        if win_hit and win_payout is None:
            odds = _optional_float(selected["odds"])
            win_return = round(100 * odds) if odds is not None else 0
        place_return = place_payout if place_hit and place_payout is not None else 0
        payout_missing = int(place_hit and place_payout is None)
        return BacktestRecord(
            race_key=str(selected["race_key"]),
            race_date=str(selected["race_date"]),
            racecourse=str(selected["racecourse"]),
            distance=int(selected["history_distance"]),
            track_condition=str(selected["track_condition"]),
            horse_no=int(selected["horse_no"]),
            horse_name=str(selected["horse_name"]),
            popularity=_optional_int(selected["popularity"]),
            actual_finish=finish,
            win_probability=probabilities["winner"][selected_index],
            place_probability=probabilities["place"][selected_index],
            ai_score=scores[selected_index],
            win_hit=win_hit,
            place_hit=place_hit,
            win_return=win_return,
            place_return=place_return,
            payout_missing=payout_missing,
        )

    def _save_history(self, run_id: str, records: list[BacktestRecord]) -> None:
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                with connection:
                    connection.execute(_BACKTEST_TABLE_SQL)
                    created_at = datetime.now(timezone.utc).isoformat()
                    connection.executemany(
                        """
                        INSERT INTO backtest_history
                            (backtest_run_id, created_at, race_key, race_date,
                             racecourse, distance, track_condition, horse_no,
                             horse_name, popularity, actual_finish,
                             win_probability, place_probability, ai_score,
                             win_hit, place_hit, win_return, place_return,
                             payout_missing)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?)
                        """,
                        [
                            (run_id, created_at, *astuple(record))
                            for record in records
                        ],
                    )
        except sqlite3.Error as exc:
            raise ModelError(f"バックテスト履歴保存失敗: {exc}") from exc

    def _write_reports(
        self,
        run_id: str,
        records: list[BacktestRecord],
        importance: dict[str, float],
        skipped: int,
    ) -> tuple[Path, Path, Path]:
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            csv_path = self.reports_dir / "backtest.csv"
            report_path = self.reports_dir / "backtest_report.html"
            importance_path = self.reports_dir / "feature_importance.html"
            _write_csv(csv_path, run_id, records)
            _write_report(report_path, run_id, records, skipped)
            _write_importance(importance_path, importance)
        except OSError as exc:
            raise ModelError(f"バックテストレポート作成失敗: {exc}") from exc
        return csv_path, report_path, importance_path


def _group_races(rows: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["race_key"]), []).append(row)
    return list(grouped.values())


def _category_maps(
    rows: list[sqlite3.Row], feature_names: list[str]
) -> dict[str, dict[str, int]]:
    return {
        name: {
            value: index
            for index, value in enumerate(sorted({str(row[name]) for row in rows}))
        }
        for name in feature_names
        if name in CATEGORICAL_COLUMNS
    }


def _fit_lightgbm(
    module: Any,
    x_train: list[list[float]],
    y_train: list[int],
    x_test: list[list[float]],
    feature_names: list[str],
    rounds: int,
) -> tuple[list[float], dict[str, float]]:
    dataset = module.Dataset(x_train, label=y_train, feature_name=feature_names)
    booster = module.train(
        {
            "objective": "binary",
            "metric": "binary_logloss",
            "verbosity": -1,
            "learning_rate": 0.05,
            "seed": 42,
        },
        dataset,
        num_boost_round=rounds,
    )
    predictions = [float(value) for value in booster.predict(x_test)]
    gains = booster.feature_importance(importance_type="gain")
    return predictions, dict(zip(feature_names, map(float, gains)))


def _fit_xgboost(
    module: Any,
    x_train: list[list[float]],
    y_train: list[int],
    x_test: list[list[float]],
    feature_names: list[str],
    rounds: int,
) -> tuple[list[float], dict[str, float]]:
    training = module.DMatrix(x_train, label=y_train, feature_names=feature_names)
    testing = module.DMatrix(x_test, feature_names=feature_names)
    booster = module.train(
        {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "eta": 0.05,
            "max_depth": 6,
            "seed": 42,
        },
        training,
        num_boost_round=rounds,
        verbose_eval=False,
    )
    predictions = [float(value) for value in booster.predict(testing)]
    gains = booster.get_score(importance_type="gain")
    return predictions, {name: float(gains.get(name, 0.0)) for name in feature_names}


def _summary(records: list[BacktestRecord]) -> dict[str, float]:
    count = len(records)
    win_returns = sum(record.win_return for record in records)
    place_returns = sum(record.place_return for record in records)
    return {
        "的中率": sum(record.place_hit for record in records) / count * 100,
        "単勝回収率": win_returns / (count * 100) * 100,
        "複勝回収率": place_returns / (count * 100) * 100,
        "ROI": (win_returns + place_returns - count * 200) / (count * 200) * 100,
        "勝率": sum(record.win_hit for record in records) / count * 100,
        "複勝率": sum(record.place_hit for record in records) / count * 100,
        "平均順位": sum(record.actual_finish for record in records) / count,
        "平均AI Score": sum(record.ai_score for record in records) / count,
    }


def _groups(records: list[BacktestRecord], key: str) -> dict[str, list[BacktestRecord]]:
    result: dict[str, list[BacktestRecord]] = defaultdict(list)
    for record in records:
        if key == "popularity":
            label = "1番人気" if record.popularity == 1 else (
                "2〜5番人気" if record.popularity and record.popularity <= 5 else "穴馬"
            )
        elif key == "distance":
            if record.distance <= 1400:
                label = "短距離"
            elif record.distance <= 1800:
                label = "マイル"
            elif record.distance <= 2400:
                label = "中距離"
            else:
                label = "長距離"
        else:
            label = str(getattr(record, key))
        result[label].append(record)
    return result


def _write_csv(path: Path, run_id: str, records: list[BacktestRecord]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("backtest_run_id", *BacktestRecord.__dataclass_fields__))
        for record in records:
            writer.writerow((run_id, *astuple(record)))


def _write_report(
    path: Path,
    run_id: str,
    records: list[BacktestRecord],
    skipped: int,
) -> None:
    sections = [
        ("全体", {"全体": records}),
        ("人気別", _groups(records, "popularity")),
        ("コース別", _groups(records, "racecourse")),
        ("距離別", _groups(records, "distance")),
        ("馬場別", _groups(records, "track_condition")),
    ]
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Backtest Report</title>",
        "<style>body{font-family:sans-serif}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:6px;text-align:right}"
        "th:first-child{text-align:left}</style>",
        f"<h1>Rolling Backtest</h1><p>Run: {html.escape(run_id)} / "
        f"races={len(records)} / skipped={skipped}</p>",
    ]
    for title, groups in sections:
        headings = "".join(
            f"<th>{html.escape(key)}</th>" for key in _summary(records)
        )
        parts.append(
            f"<h2>{html.escape(title)}</h2><table><tr><th>区分</th>"
            f"{headings}</tr>"
        )
        for label, values in groups.items():
            summary = _summary(values)
            cells = "".join(f"<td>{value:.2f}</td>" for value in summary.values())
            parts.append(
                f"<tr><th>{html.escape(label)} ({len(values)})</th>"
                f"{cells}</tr>"
            )
        parts.append("</table>")
    missing = sum(record.payout_missing for record in records)
    parts.append(f"<p>複勝払戻欠損的中数: {missing}</p>")
    path.write_text("".join(parts), encoding="utf-8")


def _write_importance(path: Path, importance: dict[str, float]) -> None:
    rankings = sorted(importance.items(), key=lambda item: item[1], reverse=True)[:50]
    rows = "".join(
        f"<tr><td>{rank}</td><td>{html.escape(name)}</td>"
        f"<td>{value:.4f}</td></tr>"
        for rank, (name, value) in enumerate(rankings, 1)
    )
    document = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>Feature Importance</title>"
        "<h1>Feature Importance Top 50</h1>"
        "<table><tr><th>Rank</th><th>Feature</th><th>Gain</th></tr>"
        f"{rows}</table>"
    )
    path.write_text(document, encoding="utf-8")


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


_BACKTEST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS backtest_history (
    backtest_id INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_run_id TEXT NOT NULL, created_at TEXT NOT NULL,
    race_key TEXT NOT NULL, race_date TEXT NOT NULL, racecourse TEXT NOT NULL,
    distance INTEGER NOT NULL, track_condition TEXT NOT NULL,
    horse_no INTEGER NOT NULL, horse_name TEXT NOT NULL, popularity INTEGER,
    actual_finish INTEGER NOT NULL, win_probability REAL NOT NULL,
    place_probability REAL NOT NULL, ai_score REAL NOT NULL,
    win_hit INTEGER NOT NULL, place_hit INTEGER NOT NULL,
    win_return INTEGER NOT NULL, place_return INTEGER NOT NULL,
    payout_missing INTEGER NOT NULL,
    UNIQUE(backtest_run_id, race_key)
)
"""

"""Practical race prediction, value selection, tickets, and reports."""

from __future__ import annotations

import csv
import html
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.database import DEFAULT_DATABASE_PATH
from scripts.ensemble_predict import EnsemblePredictionEngine, EnsembleResult
from scripts.train_model import ModelError

DEFAULT_REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
MARKS = ("◎", "○", "▲", "△", "△")


@dataclass(frozen=True)
class PredictionRow:
    """One ranked runner enriched with live odds and value assessment."""

    rank: int
    horse_no: int
    horse_name: str
    mark: str
    win_probability: float
    place_probability: float
    odds: float | None
    expected_value: float | None
    is_value: bool
    ai_score: float
    confidence: float


@dataclass(frozen=True)
class BetRecommendation:
    """One generated ticket proposal."""

    bet_type: str
    selection: str


@dataclass(frozen=True)
class PredictionResult:
    """Complete Phase 6 prediction output."""

    race_key: str
    prediction_run_id: str
    predictions: tuple[PredictionRow, ...]
    bets: tuple[BetRecommendation, ...]
    saved_count: int
    csv_path: Path
    html_path: Path


class PredictionEngine:
    """Build an actionable report from the existing ensemble prediction."""

    def __init__(
        self,
        database_path: Path = DEFAULT_DATABASE_PATH,
        reports_dir: Path = DEFAULT_REPORTS_DIR,
        ensemble_engine: EnsemblePredictionEngine | None = None,
    ) -> None:
        self.database_path = database_path
        self.reports_dir = reports_dir
        self.ensemble_engine = ensemble_engine or EnsemblePredictionEngine(
            database_path=database_path
        )

    def predict(self, race_key: str) -> PredictionResult:
        """Run the ensemble, enrich it, generate tickets, and persist output."""
        ensemble = self.ensemble_engine.predict(race_key)
        odds = self._load_odds(race_key)
        rows = self._build_rows(ensemble, odds)
        bets = _generate_bets(rows)
        self._write_reports(race_key, rows, bets)
        saved_count = self._save(ensemble.prediction_run_id, race_key, rows, bets)
        return PredictionResult(
            race_key=race_key,
            prediction_run_id=ensemble.prediction_run_id,
            predictions=rows,
            bets=bets,
            saved_count=saved_count,
            csv_path=self.reports_dir / "prediction.csv",
            html_path=self.reports_dir / "prediction.html",
        )

    def _load_odds(self, race_key: str) -> dict[int, float | None]:
        if not self.database_path.exists():
            raise ModelError(f"SQLiteが存在しません: {self.database_path}")
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                rows = connection.execute(
                    "SELECT horse_no, odds FROM race_entries WHERE race_key = ?",
                    (race_key,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ModelError(f"オッズ読込失敗: {exc}") from exc
        return {
            int(horse_no): float(value) if value is not None else None
            for horse_no, value in rows
        }

    @staticmethod
    def _build_rows(
        ensemble: EnsembleResult,
        odds: dict[int, float | None],
    ) -> tuple[PredictionRow, ...]:
        value_horses = {
            prediction.horse_no
            for prediction in ensemble.predictions
            if odds.get(prediction.horse_no) is not None
            and prediction.win_probability * float(odds[prediction.horse_no]) > 1.0
        }
        star_horse = next(
            (
                prediction.horse_no
                for prediction in ensemble.predictions
                if prediction.rank > len(MARKS)
                and prediction.horse_no in value_horses
            ),
            ensemble.predictions[len(MARKS)].horse_no
            if len(ensemble.predictions) > len(MARKS)
            else None,
        )
        result: list[PredictionRow] = []
        for prediction in ensemble.predictions:
            current_odds = odds.get(prediction.horse_no)
            expected_value = (
                prediction.win_probability * current_odds
                if current_odds is not None
                else None
            )
            mark = MARKS[prediction.rank - 1] if prediction.rank <= len(MARKS) else ""
            if prediction.horse_no == star_horse:
                mark = "☆"
            result.append(
                PredictionRow(
                    rank=prediction.rank,
                    horse_no=prediction.horse_no,
                    horse_name=prediction.horse_name,
                    mark=mark,
                    win_probability=prediction.win_probability,
                    place_probability=prediction.place_probability,
                    odds=current_odds,
                    expected_value=expected_value,
                    is_value=prediction.horse_no in value_horses,
                    ai_score=prediction.ai_score,
                    confidence=prediction.confidence,
                )
            )
        return tuple(result)

    def _write_reports(
        self,
        race_key: str,
        rows: tuple[PredictionRow, ...],
        bets: tuple[BetRecommendation, ...],
    ) -> None:
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            _write_csv(self.reports_dir / "prediction.csv", race_key, rows)
            _write_html(self.reports_dir / "prediction.html", race_key, rows, bets)
        except OSError as exc:
            raise ModelError(f"予測レポート作成失敗: {exc}") from exc

    def _save(
        self,
        run_id: str,
        race_key: str,
        rows: tuple[PredictionRow, ...],
        bets: tuple[BetRecommendation, ...],
    ) -> int:
        bets_json = json.dumps(
            [bet.__dict__ for bet in bets], ensure_ascii=False
        )
        predicted_at = datetime.now(timezone.utc).isoformat()
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS prediction_history (
                            prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            prediction_run_id TEXT NOT NULL,
                            predicted_at TEXT NOT NULL,
                            race_key TEXT NOT NULL,
                            horse_no INTEGER NOT NULL,
                            horse_name TEXT NOT NULL,
                            ai_rank INTEGER NOT NULL,
                            recommendation_mark TEXT NOT NULL,
                            win_probability REAL NOT NULL,
                            place_probability REAL NOT NULL,
                            odds REAL,
                            expected_value REAL,
                            is_value INTEGER NOT NULL,
                            ai_score REAL NOT NULL,
                            confidence REAL NOT NULL,
                            bets_json TEXT NOT NULL,
                            UNIQUE (prediction_run_id, horse_no)
                        )
                        """
                    )
                    connection.executemany(
                        """
                        INSERT INTO prediction_history
                            (prediction_run_id, predicted_at, race_key,
                             horse_no, horse_name, ai_rank,
                             recommendation_mark, win_probability,
                             place_probability, odds, expected_value,
                             is_value, ai_score, confidence, bets_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                run_id,
                                predicted_at,
                                race_key,
                                row.horse_no,
                                row.horse_name,
                                row.rank,
                                row.mark,
                                row.win_probability,
                                row.place_probability,
                                row.odds,
                                row.expected_value,
                                int(row.is_value),
                                row.ai_score,
                                row.confidence,
                                bets_json,
                            )
                            for row in rows
                        ],
                    )
        except sqlite3.Error as exc:
            raise ModelError(f"予測履歴保存失敗: {exc}") from exc
        return len(rows)


def _generate_bets(
    rows: tuple[PredictionRow, ...],
) -> tuple[BetRecommendation, ...]:
    """Generate deterministic tickets from the top five AI selections."""
    if not rows:
        return ()
    numbers = [str(row.horse_no) for row in rows[:5]]
    key = numbers[0]
    value_numbers = [str(row.horse_no) for row in rows if row.is_value]
    win_targets = value_numbers or [key]
    bets = [
        BetRecommendation("単勝", number) for number in win_targets
    ]
    bets.extend(BetRecommendation("複勝", number) for number in numbers[:2])
    if len(numbers) >= 2:
        bets.extend(
            BetRecommendation(kind, f"{key}-{number}")
            for kind in ("馬連", "ワイド")
            for number in numbers[1:4]
        )
        bets.extend(
            BetRecommendation("馬単", f"{key}→{number}")
            for number in numbers[1:3]
        )
    if len(numbers) >= 3:
        bets.extend(
            BetRecommendation("三連複", f"{key}-{numbers[1]}-{number}")
            for number in numbers[2:5]
        )
        bets.extend(
            BetRecommendation("三連単", f"{key}→{numbers[1]}→{number}")
            for number in numbers[2:5]
        )
    return tuple(bets)


def _write_csv(path: Path, race_key: str, rows: tuple[PredictionRow, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "race_key", "rank", "mark", "horse_no", "horse_name",
                "probability", "place_probability", "odds",
                "expected_value", "value_over_1", "ai_score", "confidence",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    race_key, row.rank, row.mark, row.horse_no, row.horse_name,
                    row.win_probability, row.place_probability, row.odds,
                    row.expected_value, row.is_value, row.ai_score,
                    row.confidence,
                ]
            )


def _write_html(
    path: Path,
    race_key: str,
    rows: tuple[PredictionRow, ...],
    bets: tuple[BetRecommendation, ...],
) -> None:
    table_rows = "".join(_html_prediction_row(row) for row in rows)
    bet_items = "".join(
        f"<li><strong>{html.escape(bet.bet_type)}</strong> "
        f"{html.escape(bet.selection)}</li>" for bet in bets
    )
    document = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>Prediction {race_key}</title>
<style>body{{font-family:sans-serif;margin:2rem}}table{{border-collapse:collapse}}
th,td{{border:1px solid #bbb;padding:.45rem;text-align:right}}
th:nth-child(4),td:nth-child(4){{text-align:left}}.value{{background:#fff3cd}}</style>
</head><body><h1>HorseRacingAI Prediction</h1><h2>{race_key}</h2>
<p>黄色は単勝期待値（勝率 × オッズ）が1.0を超える馬です。</p>
<table><thead><tr><th>順位</th><th>印</th><th>馬番</th><th>馬名</th>
<th>勝率</th><th>複勝率</th><th>オッズ</th><th>EV</th>
<th>AI Score</th><th>Confidence</th></tr></thead><tbody>{table_rows}</tbody></table>
<h2>買い目</h2><ul>{bet_items}</ul></body></html>"""
    path.write_text(document, encoding="utf-8")


def _html_prediction_row(row: PredictionRow) -> str:
    row_class = " class='value'" if row.is_value else ""
    odds = f"{row.odds:.1f}" if row.odds is not None else "-"
    expected_value = (
        f"{row.expected_value:.3f}" if row.expected_value is not None else "-"
    )
    return (
        f"<tr{row_class}><td>{row.rank}</td><td>{html.escape(row.mark)}</td>"
        f"<td>{row.horse_no}</td><td>{html.escape(row.horse_name)}</td>"
        f"<td>{row.win_probability:.2%}</td>"
        f"<td>{row.place_probability:.2%}</td><td>{odds}</td>"
        f"<td>{expected_value}</td><td>{row.ai_score:.2f}</td>"
        f"<td>{row.confidence:.2f}</td></tr>"
    )

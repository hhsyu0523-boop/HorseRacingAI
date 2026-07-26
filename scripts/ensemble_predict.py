"""Weighted LightGBM/XGBoost prediction and persistence engine."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from scripts.database import DEFAULT_DATABASE_PATH
from scripts.predict_model import RacePredictor
from scripts.predict_xgboost import XGBoostRacePredictor
from scripts.train_model import DEFAULT_MODELS_DIR, ModelError

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "ensemble.json"
)


class _PredictionLike(Protocol):
    horse_no: int
    horse_name: str
    probability: float


@dataclass(frozen=True)
class ModelWeights:
    lightgbm: float
    xgboost: float


@dataclass(frozen=True)
class ScoreWeights:
    winner: float
    place: float


@dataclass(frozen=True)
class EnsembleConfig:
    winner: ModelWeights
    place: ModelWeights
    ai_score: ScoreWeights


@dataclass(frozen=True)
class EnsemblePrediction:
    horse_no: int
    horse_name: str
    win_probability: float
    place_probability: float
    rank: int
    ai_score: float
    confidence: float


@dataclass(frozen=True)
class EnsembleResult:
    race_key: str
    prediction_run_id: str
    predictions: tuple[EnsemblePrediction, ...]
    saved_count: int


class EnsemblePredictionEngine:
    """Combine four model outputs and store one prediction snapshot."""

    def __init__(
        self,
        database_path: Path = DEFAULT_DATABASE_PATH,
        models_dir: Path = DEFAULT_MODELS_DIR,
        config_path: Path = DEFAULT_CONFIG_PATH,
        lightgbm_predictor: RacePredictor | None = None,
        xgboost_predictor: XGBoostRacePredictor | None = None,
    ) -> None:
        self.database_path = database_path
        self.config_path = config_path
        self.lightgbm_predictor = lightgbm_predictor or RacePredictor(
            database_path, models_dir
        )
        self.xgboost_predictor = xgboost_predictor or XGBoostRacePredictor(
            database_path, models_dir
        )

    def predict(self, race_key: str) -> EnsembleResult:
        """Predict, rank, and persist one race."""
        config = load_ensemble_config(self.config_path)
        lgb_winner = self.lightgbm_predictor.predict(race_key, "winner")
        lgb_place = self.lightgbm_predictor.predict(race_key, "place")
        xgb_winner = self.xgboost_predictor.predict(race_key, "winner")
        xgb_place = self.xgboost_predictor.predict(race_key, "place")
        sources = {
            "lgb_winner": _prediction_map(lgb_winner),
            "lgb_place": _prediction_map(lgb_place),
            "xgb_winner": _prediction_map(xgb_winner),
            "xgb_place": _prediction_map(xgb_place),
        }
        horse_numbers = _validate_sources(sources)
        combined: list[tuple[int, str, float, float, float, float]] = []
        for horse_no in horse_numbers:
            name, lgb_win = sources["lgb_winner"][horse_no]
            _, lgb_top3 = sources["lgb_place"][horse_no]
            _, xgb_win = sources["xgb_winner"][horse_no]
            _, xgb_top3 = sources["xgb_place"][horse_no]
            win_probability = _weighted(
                lgb_win, xgb_win, config.winner
            )
            place_probability = _weighted(
                lgb_top3, xgb_top3, config.place
            )
            ai_score = 100 * (
                win_probability * config.ai_score.winner
                + place_probability * config.ai_score.place
            )
            agreement = 1 - (
                abs(lgb_win - xgb_win) + abs(lgb_top3 - xgb_top3)
            ) / 2
            confidence = 100 * min(max(agreement, 0.0), 1.0)
            combined.append(
                (
                    horse_no,
                    name,
                    win_probability,
                    place_probability,
                    ai_score,
                    confidence,
                )
            )
        combined.sort(key=lambda item: (-item[4], item[0]))
        predictions = tuple(
            EnsemblePrediction(
                horse_no=item[0],
                horse_name=item[1],
                win_probability=item[2],
                place_probability=item[3],
                rank=rank,
                ai_score=item[4],
                confidence=item[5],
            )
            for rank, item in enumerate(combined, start=1)
        )
        run_id = uuid.uuid4().hex
        saved_count = self._save(race_key, run_id, predictions, config)
        return EnsembleResult(race_key, run_id, predictions, saved_count)

    def _save(
        self,
        race_key: str,
        run_id: str,
        predictions: tuple[EnsemblePrediction, ...],
        config: EnsembleConfig,
    ) -> int:
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.database_path)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS race_prediction (
                            prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            prediction_run_id TEXT NOT NULL,
                            predicted_at TEXT NOT NULL,
                            race_key TEXT NOT NULL,
                            horse_no INTEGER NOT NULL,
                            horse_name TEXT NOT NULL,
                            win_probability REAL NOT NULL,
                            place_probability REAL NOT NULL,
                            predicted_rank INTEGER NOT NULL,
                            ai_score REAL NOT NULL,
                            confidence REAL NOT NULL,
                            winner_lgb_weight REAL NOT NULL,
                            winner_xgb_weight REAL NOT NULL,
                            place_lgb_weight REAL NOT NULL,
                            place_xgb_weight REAL NOT NULL,
                            UNIQUE (prediction_run_id, horse_no)
                        )
                        """
                    )
                    predicted_at = datetime.now(timezone.utc).isoformat()
                    connection.executemany(
                        """
                        INSERT INTO race_prediction
                            (prediction_run_id, predicted_at, race_key,
                             horse_no, horse_name, win_probability,
                             place_probability, predicted_rank, ai_score,
                             confidence, winner_lgb_weight,
                             winner_xgb_weight, place_lgb_weight,
                             place_xgb_weight)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                run_id,
                                predicted_at,
                                race_key,
                                prediction.horse_no,
                                prediction.horse_name,
                                prediction.win_probability,
                                prediction.place_probability,
                                prediction.rank,
                                prediction.ai_score,
                                prediction.confidence,
                                config.winner.lightgbm,
                                config.winner.xgboost,
                                config.place.lightgbm,
                                config.place.xgboost,
                            )
                            for prediction in predictions
                        ],
                    )
        except (OSError, sqlite3.Error) as exc:
            raise ModelError(f"予測履歴保存失敗: {exc}") from exc
        return len(predictions)


def load_ensemble_config(path: Path = DEFAULT_CONFIG_PATH) -> EnsembleConfig:
    """Load and normalize all ensemble weights from JSON."""
    try:
        with path.open(encoding="utf-8") as stream:
            raw = json.load(stream)
        return EnsembleConfig(
            winner=_model_weights(raw["winner"], "winner"),
            place=_model_weights(raw["place"], "place"),
            ai_score=_score_weights(raw["ai_score"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ModelError(f"アンサンブル設定読込失敗: {exc}") from exc


def _model_weights(raw: object, label: str) -> ModelWeights:
    if not isinstance(raw, dict):
        raise ModelError(f"{label}の重み設定が不正です")
    lightgbm = _nonnegative_float(raw.get("lightgbm"), f"{label}.lightgbm")
    xgboost = _nonnegative_float(raw.get("xgboost"), f"{label}.xgboost")
    total = lightgbm + xgboost
    if total <= 0:
        raise ModelError(f"{label}の重み合計は0より大きくしてください")
    return ModelWeights(lightgbm / total, xgboost / total)


def _score_weights(raw: object) -> ScoreWeights:
    if not isinstance(raw, dict):
        raise ModelError("ai_scoreの重み設定が不正です")
    winner = _nonnegative_float(raw.get("winner"), "ai_score.winner")
    place = _nonnegative_float(raw.get("place"), "ai_score.place")
    total = winner + place
    if total <= 0:
        raise ModelError("ai_scoreの重み合計は0より大きくしてください")
    return ScoreWeights(winner / total, place / total)


def _nonnegative_float(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ModelError(f"{label}は数値で指定してください") from exc
    if not math.isfinite(number) or number < 0:
        raise ModelError(f"{label}は0以上で指定してください")
    return number


def _prediction_map(
    predictions: Iterable[_PredictionLike],
) -> dict[int, tuple[str, float]]:
    mapped: dict[int, tuple[str, float]] = {}
    for prediction in predictions:
        probability = float(prediction.probability)
        horse_no = int(prediction.horse_no)
        if horse_no in mapped:
            raise ModelError(f"馬番{horse_no}の予測が重複しています")
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ModelError("モデル予測確率が0～1の範囲外です")
        mapped[horse_no] = (
            str(prediction.horse_name),
            probability,
        )
    return mapped


def _validate_sources(
    sources: dict[str, dict[int, tuple[str, float]]]
) -> list[int]:
    if not sources or any(not predictions for predictions in sources.values()):
        raise ModelError("アンサンブル対象の予測結果がありません")
    horse_sets = [set(predictions) for predictions in sources.values()]
    if any(horses != horse_sets[0] for horses in horse_sets[1:]):
        raise ModelError("モデル間で出走馬が一致しません")
    for horse_no in horse_sets[0]:
        names = {predictions[horse_no][0] for predictions in sources.values()}
        if len(names) != 1:
            raise ModelError(f"馬番{horse_no}の馬名がモデル間で一致しません")
    return sorted(horse_sets[0])


def _weighted(
    lightgbm: float, xgboost: float, weights: ModelWeights
) -> float:
    return lightgbm * weights.lightgbm + xgboost * weights.xgboost

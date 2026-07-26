"""Train and persist winner/place XGBoost models."""

from __future__ import annotations

import csv
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.database import DEFAULT_DATABASE_PATH
from scripts.train_model import (
    DEFAULT_MODELS_DIR,
    TARGETS,
    EvaluationMetrics,
    LightGBMTrainingEngine,
    ModelError,
    _evaluate,
    _labels,
    _transform_rows,
)

XGB_MODELS = {
    "winner": "winner_xgb.pkl",
    "place": "place_xgb.pkl",
}


@dataclass(frozen=True)
class XGBoostTrainingReport:
    model_kind: str
    train_count: int
    validation_count: int
    validation_start: str
    metrics: EvaluationMetrics
    model_path: Path
    importance_path: Path


class XGBoostTrainingEngine(LightGBMTrainingEngine):
    """Train XGBoost classifiers with the shared chronological split."""

    def __init__(
        self,
        database_path: Path = DEFAULT_DATABASE_PATH,
        models_dir: Path = DEFAULT_MODELS_DIR,
        validation_ratio: float = 0.2,
    ) -> None:
        super().__init__(database_path, models_dir, validation_ratio)

    def train_all(
        self,
    ) -> tuple[XGBoostTrainingReport, XGBoostTrainingReport]:
        """Train both targets and save a combined importance ranking."""
        xgboost = _load_xgboost()
        data = self._load_data()
        train_rows, validation_rows, validation_start = self._time_split(data.rows)
        try:
            self.models_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ModelError(f"モデルディレクトリ作成失敗: {exc}") from exc

        reports: list[XGBoostTrainingReport] = []
        importance: dict[str, list[tuple[str, float]]] = {}
        for kind, (_filename, threshold) in TARGETS.items():
            report, rankings = self._train_target_xgb(
                xgboost,
                kind,
                threshold,
                data.feature_names,
                data.category_maps,
                train_rows,
                validation_rows,
                validation_start,
            )
            reports.append(report)
            importance[kind] = rankings
        _save_combined_importance(reports[0].importance_path, importance)
        return reports[0], reports[1]

    def _train_target_xgb(
        self,
        xgboost: Any,
        kind: str,
        finish_threshold: int,
        feature_names: list[str],
        category_maps: dict[str, dict[str, int]],
        train_rows: list[Any],
        validation_rows: list[Any],
        validation_start: str,
    ) -> tuple[XGBoostTrainingReport, list[tuple[str, float]]]:
        x_train = _transform_rows(train_rows, feature_names, category_maps)
        x_validation = _transform_rows(
            validation_rows, feature_names, category_maps
        )
        y_train = _labels(train_rows, finish_threshold)
        y_validation = _labels(validation_rows, finish_threshold)
        if len(set(y_train)) < 2:
            raise ModelError(f"{kind}のtrainデータに正例と負例が必要です")

        training_set = xgboost.DMatrix(
            x_train,
            label=y_train,
            feature_names=feature_names,
        )
        validation_set = xgboost.DMatrix(
            x_validation,
            label=y_validation,
            feature_names=feature_names,
        )
        booster = xgboost.train(
            {
                "objective": "binary:logistic",
                "eval_metric": "logloss",
                "eta": 0.03,
                "max_depth": 6,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "seed": 42,
            },
            training_set,
            num_boost_round=500,
            evals=[(validation_set, "validation")],
            early_stopping_rounds=50,
            verbose_eval=False,
        )
        probabilities = [
            float(value) for value in _predict_booster(booster, validation_set)
        ]
        metrics = _evaluate(y_validation, probabilities)
        model_path = self.models_dir / XGB_MODELS[kind]
        importance_path = self.models_dir / "importance_xgb.csv"
        bundle = {
            "version": 1,
            "engine": "xgboost",
            "model_kind": kind,
            "finish_threshold": finish_threshold,
            "feature_names": feature_names,
            "category_maps": category_maps,
            "booster": booster,
            "validation_start": validation_start,
            "metrics": metrics,
        }
        _save_xgb_pickle(model_path, bundle)
        scores = booster.get_score(importance_type="gain")
        rankings = sorted(
            ((name, float(scores.get(name, 0.0))) for name in feature_names),
            key=lambda item: item[1],
            reverse=True,
        )[:50]
        return (
            XGBoostTrainingReport(
                model_kind=kind,
                train_count=len(train_rows),
                validation_count=len(validation_rows),
                validation_start=validation_start,
                metrics=metrics,
                model_path=model_path,
                importance_path=importance_path,
            ),
            rankings,
        )


def load_xgb_bundle(path: Path) -> dict[str, Any]:
    """Load and validate a trusted local XGBoost model bundle."""
    if not path.exists():
        raise ModelError(f"モデルが存在しません: {path}")
    try:
        with path.open("rb") as stream:
            bundle = pickle.load(stream)  # noqa: S301 - trusted local model
    except (
        OSError,
        pickle.PickleError,
        EOFError,
        AttributeError,
        ImportError,
    ) as exc:
        raise ModelError(f"XGBoostモデル読込失敗: {exc}") from exc
    required = {"booster", "feature_names", "category_maps", "model_kind"}
    if (
        not isinstance(bundle, dict)
        or bundle.get("engine") != "xgboost"
        or not required.issubset(bundle)
    ):
        raise ModelError("XGBoostモデルファイルの形式が不正です")
    return bundle


def transform_xgb_rows(rows: list[Any], bundle: dict[str, Any]) -> list[list[float]]:
    """Apply the feature order and category maps stored with the model."""
    return _transform_rows(
        rows,
        list(bundle["feature_names"]),
        dict(bundle["category_maps"]),
    )


def _predict_booster(booster: Any, matrix: Any) -> Any:
    best_iteration = getattr(booster, "best_iteration", None)
    if best_iteration is None:
        return booster.predict(matrix)
    return booster.predict(matrix, iteration_range=(0, best_iteration + 1))


def _load_xgboost() -> Any:
    try:
        import xgboost
    except (ImportError, OSError) as exc:
        raise ModelError(
            "XGBoostを読み込めません。.venv32の導入制約をREADMEで確認してください"
        ) from exc
    return xgboost


def _save_xgb_pickle(path: Path, bundle: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("wb") as stream:
            pickle.dump(bundle, stream, protocol=pickle.HIGHEST_PROTOCOL)
        temporary_path.replace(path)
    except (OSError, pickle.PickleError, TypeError) as exc:
        raise ModelError(f"XGBoostモデル保存失敗: {exc}") from exc


def _save_combined_importance(
    path: Path, rankings_by_model: dict[str, list[tuple[str, float]]]
) -> None:
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("model", "rank", "feature", "importance_gain"))
            for model_kind, rankings in rankings_by_model.items():
                for rank, (feature, importance) in enumerate(rankings, start=1):
                    writer.writerow((model_kind, rank, feature, importance))
    except OSError as exc:
        raise ModelError(f"XGBoost Feature Importance保存失敗: {exc}") from exc

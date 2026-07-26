"""Train and persist winner/place LightGBM models."""

from __future__ import annotations

import csv
import math
import pickle
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.database import DEFAULT_DATABASE_PATH

DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
CATEGORICAL_COLUMNS = {
    "racecourse_code",
    "surface",
    "direction",
    "track_layout",
}
EXCLUDED_COLUMNS = {
    "race_key",
    "horse_no",
    "race_date",
    "horse_name",
    "jockey_name",
    "target_finish_position",
}
TARGETS = {
    "winner": ("winner_model.pkl", 1),
    "place": ("place_model.pkl", 3),
}


class ModelError(RuntimeError):
    """Raised when model training or prediction cannot be completed."""


@dataclass(frozen=True)
class EvaluationMetrics:
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float | None
    log_loss: float


@dataclass(frozen=True)
class TrainingReport:
    model_kind: str
    train_count: int
    validation_count: int
    validation_start: str
    metrics: EvaluationMetrics
    model_path: Path
    importance_path: Path


@dataclass(frozen=True)
class _TrainingData:
    rows: list[sqlite3.Row]
    feature_names: list[str]
    category_maps: dict[str, dict[str, int]]


class LightGBMTrainingEngine:
    """Train binary classifiers with a chronological holdout."""

    def __init__(
        self,
        database_path: Path = DEFAULT_DATABASE_PATH,
        models_dir: Path = DEFAULT_MODELS_DIR,
        validation_ratio: float = 0.2,
    ) -> None:
        if not 0.0 < validation_ratio < 1.0:
            raise ValueError("validation_ratioは0より大きく1未満にしてください")
        self.database_path = database_path
        self.models_dir = models_dir
        self.validation_ratio = validation_ratio

    def train_all(self) -> tuple[TrainingReport, TrainingReport]:
        """Train winner and top-three models from one consistent snapshot."""
        lightgbm = _load_lightgbm()
        data = self._load_data()
        train_rows, validation_rows, validation_start = self._time_split(data.rows)
        try:
            self.models_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ModelError(f"モデルディレクトリ作成失敗: {exc}") from exc
        reports = [
            self._train_target(
                lightgbm,
                kind,
                threshold,
                data,
                train_rows,
                validation_rows,
                validation_start,
            )
            for kind, (_filename, threshold) in TARGETS.items()
        ]
        return reports[0], reports[1]

    def _load_data(self) -> _TrainingData:
        if not self.database_path.exists():
            raise ModelError(f"SQLiteが存在しません: {self.database_path}")
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
                    raise ModelError(
                        "feature_historyがありません。先にbuild-featuresを実行してください"
                    )
                rows = connection.execute(
                    """
                    SELECT * FROM feature_history
                    ORDER BY race_date, race_key, horse_no
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise ModelError(f"学習データ読込失敗: {exc}") from exc
        if not rows:
            raise ModelError("feature_historyに学習データがありません")
        feature_names = [name for name in columns if name not in EXCLUDED_COLUMNS]
        category_maps = {
            name: {
                value: index
                for index, value in enumerate(
                    sorted({str(row[name]) for row in rows})
                )
            }
            for name in feature_names
            if name in CATEGORICAL_COLUMNS
        }
        return _TrainingData(rows, feature_names, category_maps)

    def _time_split(
        self, rows: list[sqlite3.Row]
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row], str]:
        dates = sorted({str(row["race_date"]) for row in rows})
        if len(dates) < 2:
            raise ModelError("時系列分割には2日以上の特徴量データが必要です")
        validation_days = max(1, math.ceil(len(dates) * self.validation_ratio))
        split_index = len(dates) - validation_days
        validation_start = dates[split_index]
        train_rows = [row for row in rows if str(row["race_date"]) < validation_start]
        validation_rows = [
            row for row in rows if str(row["race_date"]) >= validation_start
        ]
        if not train_rows or not validation_rows:
            raise ModelError("train / validation分割後のデータが空です")
        return train_rows, validation_rows, validation_start

    def _train_target(
        self,
        lightgbm: Any,
        kind: str,
        finish_threshold: int,
        data: _TrainingData,
        train_rows: list[sqlite3.Row],
        validation_rows: list[sqlite3.Row],
        validation_start: str,
    ) -> TrainingReport:
        x_train = _transform_rows(
            train_rows, data.feature_names, data.category_maps
        )
        x_validation = _transform_rows(
            validation_rows, data.feature_names, data.category_maps
        )
        y_train = _labels(train_rows, finish_threshold)
        y_validation = _labels(validation_rows, finish_threshold)
        if len(set(y_train)) < 2:
            raise ModelError(f"{kind}のtrainデータに正例と負例が必要です")

        categorical = [
            name for name in data.feature_names if name in data.category_maps
        ]
        training_set = lightgbm.Dataset(
            x_train,
            label=y_train,
            feature_name=data.feature_names,
            categorical_feature=categorical,
            free_raw_data=False,
        )
        validation_set = lightgbm.Dataset(
            x_validation,
            label=y_validation,
            reference=training_set,
            feature_name=data.feature_names,
            categorical_feature=categorical,
            free_raw_data=False,
        )
        booster = lightgbm.train(
            {
                "objective": "binary",
                "metric": "binary_logloss",
                "learning_rate": 0.03,
                "num_leaves": 31,
                "feature_fraction": 0.8,
                "bagging_fraction": 0.8,
                "bagging_freq": 1,
                "seed": 42,
                "verbosity": -1,
            },
            training_set,
            num_boost_round=500,
            valid_sets=[validation_set],
            valid_names=["validation"],
            callbacks=[lightgbm.early_stopping(50, verbose=False)],
        )
        probabilities = [float(value) for value in booster.predict(x_validation)]
        metrics = _evaluate(y_validation, probabilities)
        model_path = self.models_dir / TARGETS[kind][0]
        importance_path = self.models_dir / f"{kind}_feature_importance.csv"
        bundle = {
            "version": 1,
            "model_kind": kind,
            "finish_threshold": finish_threshold,
            "feature_names": data.feature_names,
            "category_maps": data.category_maps,
            "booster": booster,
            "validation_start": validation_start,
            "metrics": metrics,
        }
        _save_pickle(model_path, bundle)
        _save_importance(importance_path, booster, data.feature_names)
        return TrainingReport(
            model_kind=kind,
            train_count=len(train_rows),
            validation_count=len(validation_rows),
            validation_start=validation_start,
            metrics=metrics,
            model_path=model_path,
            importance_path=importance_path,
        )


def load_model_bundle(path: Path) -> dict[str, Any]:
    """Load and minimally validate a persisted model bundle."""
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
        raise ModelError(f"モデル読込失敗: {exc}") from exc
    required = {"booster", "feature_names", "category_maps", "model_kind"}
    if not isinstance(bundle, dict) or not required.issubset(bundle):
        raise ModelError("モデルファイルの形式が不正です")
    return bundle


def transform_for_prediction(
    rows: list[sqlite3.Row], bundle: dict[str, Any]
) -> list[list[float]]:
    """Apply the training-time feature order and category mappings."""
    return _transform_rows(
        rows,
        list(bundle["feature_names"]),
        dict(bundle["category_maps"]),
    )


def _load_lightgbm() -> Any:
    try:
        import lightgbm
    except (ImportError, OSError) as exc:
        raise ModelError(
            "LightGBMを読み込めません。.venv32の導入手順をREADMEで確認してください"
        ) from exc
    return lightgbm


def _transform_rows(
    rows: list[sqlite3.Row],
    feature_names: list[str],
    category_maps: dict[str, dict[str, int]],
) -> list[list[float]]:
    matrix: list[list[float]] = []
    for row in rows:
        values: list[float] = []
        for name in feature_names:
            value = row[name]
            if name in category_maps:
                values.append(float(category_maps[name].get(str(value), -1)))
            else:
                values.append(float(value) if value is not None else 0.0)
        matrix.append(values)
    return matrix


def _labels(rows: list[sqlite3.Row], finish_threshold: int) -> list[int]:
    return [
        int(int(row["target_finish_position"]) <= finish_threshold)
        for row in rows
    ]


def _evaluate(labels: list[int], probabilities: list[float]) -> EvaluationMetrics:
    predictions = [int(probability >= 0.5) for probability in probabilities]
    true_positive = sum(p == 1 and y == 1 for p, y in zip(predictions, labels))
    false_positive = sum(p == 1 and y == 0 for p, y in zip(predictions, labels))
    false_negative = sum(p == 0 and y == 1 for p, y in zip(predictions, labels))
    accuracy = sum(p == y for p, y in zip(predictions, labels)) / len(labels)
    precision = _safe_ratio(true_positive, true_positive + false_positive)
    recall = _safe_ratio(true_positive, true_positive + false_negative)
    f1 = _safe_ratio(2 * precision * recall, precision + recall)
    clipped = [min(max(value, 1e-15), 1 - 1e-15) for value in probabilities]
    log_loss = -sum(
        y * math.log(p) + (1 - y) * math.log(1 - p)
        for y, p in zip(labels, clipped)
    ) / len(labels)
    return EvaluationMetrics(
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
        roc_auc=_roc_auc(labels, probabilities),
        log_loss=log_loss,
    )


def _roc_auc(labels: list[int], probabilities: list[float]) -> float | None:
    positive_count = sum(labels)
    negative_count = len(labels) - positive_count
    if positive_count == 0 or negative_count == 0:
        return None
    ordered = sorted(zip(probabilities, labels), key=lambda item: item[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (
        rank_sum - positive_count * (positive_count + 1) / 2
    ) / (positive_count * negative_count)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _save_pickle(path: Path, bundle: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("wb") as stream:
            pickle.dump(bundle, stream, protocol=pickle.HIGHEST_PROTOCOL)
        temporary_path.replace(path)
    except (OSError, pickle.PickleError, TypeError) as exc:
        raise ModelError(f"モデル保存失敗: {exc}") from exc


def _save_importance(path: Path, booster: Any, feature_names: list[str]) -> None:
    rankings = sorted(
        zip(feature_names, booster.feature_importance(importance_type="gain")),
        key=lambda item: float(item[1]),
        reverse=True,
    )[:50]
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("rank", "feature", "importance_gain"))
            for rank, (feature, importance) in enumerate(rankings, start=1):
                writer.writerow((rank, feature, float(importance)))
    except OSError as exc:
        raise ModelError(f"Feature Importance保存失敗: {exc}") from exc

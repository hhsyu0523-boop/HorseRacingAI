"""Load a trained LightGBM bundle and output race probabilities."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from scripts.database import DEFAULT_DATABASE_PATH
from scripts.train_model import (
    DEFAULT_MODELS_DIR,
    ModelError,
    TARGETS,
    load_model_bundle,
    transform_for_prediction,
)


@dataclass(frozen=True)
class Prediction:
    horse_no: int
    horse_name: str
    probability: float


class RacePredictor:
    """Predict one race from rows already present in feature_history."""

    def __init__(
        self,
        database_path: Path = DEFAULT_DATABASE_PATH,
        models_dir: Path = DEFAULT_MODELS_DIR,
    ) -> None:
        self.database_path = database_path
        self.models_dir = models_dir

    def predict(self, race_key: str, model_kind: str) -> list[Prediction]:
        """Return descending probabilities for winner or top-three target."""
        if model_kind not in TARGETS:
            raise ValueError("model_kindはwinnerまたはplaceを指定してください")
        rows = self._load_rows(race_key)
        model_path = self.models_dir / TARGETS[model_kind][0]
        bundle = load_model_bundle(model_path)
        if bundle["model_kind"] != model_kind:
            raise ModelError("指定した種類とモデルファイルの種類が一致しません")
        matrix = transform_for_prediction(rows, bundle)
        try:
            probabilities = bundle["booster"].predict(matrix)
        except Exception as exc:
            raise ModelError(f"予測処理失敗: {exc}") from exc
        predictions = [
            Prediction(
                horse_no=int(row["horse_no"]),
                horse_name=str(row["horse_name"]),
                probability=float(probability),
            )
            for row, probability in zip(rows, probabilities)
        ]
        return sorted(predictions, key=lambda item: item.probability, reverse=True)

    def _load_rows(self, race_key: str) -> list[sqlite3.Row]:
        if len(race_key) != 12 or not race_key.isdigit():
            raise ValueError("RACE_KEYは12桁の数字で指定してください")
        if not self.database_path.exists():
            raise ModelError(f"SQLiteが存在しません: {self.database_path}")
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT * FROM feature_history
                    WHERE race_key = ?
                    ORDER BY horse_no
                    """,
                    (race_key,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ModelError(f"予測特徴量読込失敗: {exc}") from exc
        if not rows:
            raise ModelError(
                f"{race_key}の特徴量がありません。先にbuild-featuresを実行してください"
            )
        return rows


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LightGBM race predictor")
    parser.add_argument("--race", required=True, metavar="RACE_KEY")
    parser.add_argument(
        "--model",
        choices=tuple(TARGETS),
        default="winner",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        predictions = RacePredictor().predict(args.race, args.model)
    except (ModelError, ValueError) as exc:
        print(f"エラー: {exc}")
        return 1
    label = "1着確率" if args.model == "winner" else "3着以内確率"
    print(f"{args.race} {label}")
    for prediction in predictions:
        print(
            f"{prediction.horse_no:>2} {prediction.horse_name:<18} "
            f"{prediction.probability:.2%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

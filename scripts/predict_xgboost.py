"""Load XGBoost models and output race probabilities."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from scripts.predict_model import RacePredictor
from scripts.train_model import ModelError
from scripts.train_xgboost import (
    XGB_MODELS,
    _load_xgboost,
    _predict_booster,
    load_xgb_bundle,
    transform_xgb_rows,
)


@dataclass(frozen=True)
class XGBoostPrediction:
    horse_no: int
    horse_name: str
    probability: float


class XGBoostRacePredictor(RacePredictor):
    """Predict one feature_history race with a saved XGBoost model."""

    def predict(
        self, race_key: str, model_kind: str
    ) -> list[XGBoostPrediction]:
        if model_kind not in XGB_MODELS:
            raise ValueError("model_kindはwinnerまたはplaceを指定してください")
        xgboost = _load_xgboost()
        rows = self._load_rows(race_key)
        bundle = load_xgb_bundle(self.models_dir / XGB_MODELS[model_kind])
        if bundle["model_kind"] != model_kind:
            raise ModelError("指定した種類とXGBoostモデルの種類が一致しません")
        matrix = xgboost.DMatrix(
            transform_xgb_rows(rows, bundle),
            feature_names=list(bundle["feature_names"]),
        )
        try:
            probabilities = _predict_booster(bundle["booster"], matrix)
        except Exception as exc:
            raise ModelError(f"XGBoost予測処理失敗: {exc}") from exc
        predictions = [
            XGBoostPrediction(
                horse_no=int(row["horse_no"]),
                horse_name=str(row["horse_name"]),
                probability=float(probability),
            )
            for row, probability in zip(rows, probabilities)
        ]
        return sorted(predictions, key=lambda item: item.probability, reverse=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="XGBoost race predictor")
    parser.add_argument("--race", required=True, metavar="RACE_KEY")
    parser.add_argument(
        "--model",
        choices=tuple(XGB_MODELS),
        default="winner",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        predictions = XGBoostRacePredictor().predict(args.race, args.model)
    except (ModelError, ValueError) as exc:
        print(f"エラー: {exc}")
        return 1
    label = "1着確率" if args.model == "winner" else "3着以内確率"
    print(f"{args.race} XGBoost {label}")
    for prediction in predictions:
        print(
            f"{prediction.horse_no:>2} {prediction.horse_name:<18} "
            f"{prediction.probability:.2%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

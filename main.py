"""Horse Racing AI Ver.1 command-line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Callable
from datetime import date, datetime

from scripts.database import RaceRepository, StorageError
from scripts.jvlink_loader import JVLinkError
from scripts.train_model import ModelError

DEFAULT_SID = "UNKNOWN"
LOGGER = logging.getLogger(__name__)


def parse_date(value: str) -> date:
    """Parse a YYYYMMDD command-line date."""
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日付はYYYYMMDD形式で指定してください") from exc


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="JRA-VAN JV-Link data loader")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    connection_parser = subparsers.add_parser(
        "test-connection",
        help="JV-Linkの接続と初期化をテストします",
    )
    connection_parser.add_argument(
        "--sid",
        default=os.getenv("JVLINK_SID", DEFAULT_SID),
        help="JRA-VAN発行SID（未登録ソフトはUNKNOWN）",
    )
    schedule_parser = subparsers.add_parser(
        "race-schedule",
        help="当日のJRA開催情報を取得します",
    )
    schedule_parser.add_argument(
        "--sid",
        default=os.getenv("JVLINK_SID", DEFAULT_SID),
        help="JRA-VAN発行SID（未登録ソフトはUNKNOWN）",
    )
    race_list_parser = subparsers.add_parser(
        "race-list",
        help="開催場ごとの全レース一覧を取得します",
    )
    race_list_parser.add_argument(
        "--sid",
        default=os.getenv("JVLINK_SID", DEFAULT_SID),
        help="JRA-VAN発行SID（未登録ソフトはUNKNOWN）",
    )
    race_list_parser.add_argument(
        "--date",
        type=parse_date,
        default=date.today(),
        metavar="YYYYMMDD",
        help="取得対象日（省略時は当日）",
    )
    race_entries_parser = subparsers.add_parser(
        "race-entries",
        help="当日の出馬表を取得します",
    )
    race_entries_parser.add_argument(
        "--sid",
        default=os.getenv("JVLINK_SID", DEFAULT_SID),
        help="JRA-VAN発行SID（未登録ソフトはUNKNOWN）",
    )
    race_entries_parser.add_argument(
        "--race",
        required=True,
        metavar="RACE_KEY",
        help="日付8桁＋競馬場コード2桁＋R番号2桁",
    )
    history_parser = subparsers.add_parser(
        "fetch-history",
        help="指定期間の過去レース結果を取得してSQLiteへ保存します",
    )
    history_parser.add_argument(
        "--sid",
        default=os.getenv("JVLINK_SID", DEFAULT_SID),
        help="JRA-VAN発行SID（未登録ソフトはUNKNOWN）",
    )
    history_parser.add_argument(
        "--from",
        dest="from_date",
        type=parse_date,
        required=True,
        metavar="YYYYMMDD",
        help="取得開始日",
    )
    history_parser.add_argument(
        "--to",
        dest="to_date",
        type=parse_date,
        required=True,
        metavar="YYYYMMDD",
        help="取得終了日",
    )
    subparsers.add_parser(
        "build-features",
        help="race_historyからAI学習用特徴量を生成します",
    )
    subparsers.add_parser(
        "train-model",
        help="LightGBMの1着・3着以内モデルを学習します",
    )
    prediction_parser = subparsers.add_parser(
        "predict-model",
        help="保存済みLightGBMモデルで確率を予測します",
    )
    prediction_parser.add_argument(
        "--race",
        required=True,
        metavar="RACE_KEY",
    )
    prediction_parser.add_argument(
        "--model",
        choices=("winner", "place"),
        default="winner",
    )
    return parser


def run_connection_test(sid: str) -> int:
    """Load JV-Link, run JVInit, and display its return code."""
    try:
        from scripts.jvlink_loader import JVLinkClient
    except ImportError as exc:
        print(f"JV-Link接続エラー: scripts/jvlink_loader.pyを読み込めません: {exc}")
        return 1

    result = JVLinkClient(sid=sid).test_connection()
    print(f"JVInit戻り値: {result.code}")
    if result.success:
        print("JV-Link接続成功")
        return 0

    print(f"JV-Link接続エラー: エラーコード={result.code}")
    return 1


def run_race_schedule(sid: str) -> int:
    """Fetch and display today's JRA race meetings."""
    try:
        from scripts.jvlink_loader import JVLinkClient
    except ImportError as exc:
        print(f"JV-Link接続エラー: scripts/jvlink_loader.pyを読み込めません: {exc}")
        return 1

    schedule = JVLinkClient(sid=sid).get_race_schedule()
    LOGGER.info("取得件数: %d", len(schedule))
    saved_count = RaceRepository().save_schedule(schedule)
    LOGGER.info("保存件数: %d", saved_count)
    print("========================")
    print("JRA開催情報")
    print("========================")
    display_date = schedule[0].date if schedule else date.today()
    print(display_date.isoformat())
    for meeting in schedule:
        print(meeting.racecourse)
    return 0


def run_race_list(sid: str, target_date: date) -> int:
    """Fetch, save, and display detailed races for one date."""
    try:
        from scripts.jvlink_loader import JVLinkClient
    except ImportError as exc:
        print(f"JV-Link接続エラー: scripts/jvlink_loader.pyを読み込めません: {exc}")
        return 1

    races = JVLinkClient(sid=sid).get_race_list(target_date)
    LOGGER.info("取得件数: %d", len(races))
    saved_count = RaceRepository().save_races(races)
    LOGGER.info("保存件数: %d", saved_count)
    if not races:
        print(f"{target_date:%Y%m%d}のレースデータがありません。")
        return 0

    current_course: str | None = None
    for race in races:
        if race.racecourse != current_course:
            print("========================")
            print(f"{race.date:%Y-%m-%d} {race.racecourse}")
            print("========================")
            current_course = race.racecourse
        print(
            f"{race.race_no:>2}R {race.start_time} "
            f"{race.race_name:<20} {race.surface}{race.distance}m "
            f"{race.direction} {race.condition} [{race.race_key}]"
        )
    return 0


def run_race_entries(sid: str, race_key: str) -> int:
    """Fetch, save, and display runners for one race key."""
    try:
        from scripts.jvlink_loader import JVLinkClient
    except ImportError as exc:
        print(f"JV-Link接続エラー: scripts/jvlink_loader.pyを読み込めません: {exc}")
        return 1

    entries = JVLinkClient(sid=sid).get_race_entries(race_key)
    LOGGER.info("取得件数: %d", len(entries))
    saved_count = RaceRepository().save_entries(entries)
    LOGGER.info("保存件数: %d", saved_count)
    if not entries:
        print("出馬表データがありません。")
        return 0

    first_entry = entries[0]
    print("========================")
    print(f"{first_entry.racecourse} {first_entry.race_no}R [{race_key}]")
    print("========================")
    print("枠 馬番 馬名             性齢 騎手       調教師     斤量 人気 オッズ")
    for entry in entries:
        popularity = str(entry.popularity) if entry.popularity is not None else "-"
        odds = f"{entry.odds:.1f}" if entry.odds is not None else "-"
        print(
            f"{entry.gate_no:>2} {entry.horse_no:>4} "
            f"{entry.horse_name:<16} {entry.sex_age:<4} "
            f"{entry.jockey_name:<8} {entry.trainer_name:<8} "
            f"{entry.assigned_weight:>4.1f} {popularity:>4} {odds:>6}"
        )
    return 0


def run_fetch_history(sid: str, from_date: date, to_date: date) -> int:
    """Fetch an inclusive historical range and persist new results."""
    if from_date > to_date:
        raise ValueError("開始日は終了日以前を指定してください")

    from scripts.jvlink_loader import JVLinkClient

    repository = RaceRepository()
    resume_date = repository.history_resume_date(from_date)
    if resume_date > to_date:
        LOGGER.info("取得件数: 0")
        LOGGER.info("保存件数: 0")
        LOGGER.info("エラー件数: 0")
        print(f"履歴収集済み: {from_date:%Y%m%d} - {to_date:%Y%m%d}")
        return 0

    if resume_date > from_date:
        LOGGER.info("再開位置: %s", resume_date.strftime("%Y%m%d"))

    result = JVLinkClient(sid=sid).get_race_history(resume_date, to_date)
    LOGGER.info("取得件数: %d", len(result.entries))
    saved_count = repository.save_history(result.entries, from_date, to_date)
    LOGGER.info("保存件数: %d", saved_count)
    LOGGER.info("エラー件数: %d", result.error_count)
    print(
        f"履歴収集完了: {resume_date:%Y%m%d} - {to_date:%Y%m%d} "
        f"取得={len(result.entries)} 保存={saved_count} "
        f"エラー={result.error_count}"
    )
    return 0


def run_build_features() -> int:
    """Generate and save leakage-safe training features."""
    from scripts.feature_engine import FeatureEngineeringEngine

    result = FeatureEngineeringEngine().build()
    LOGGER.info("履歴件数: %d", result.source_count)
    LOGGER.info("特徴量保存件数: %d", result.saved_count)
    print(
        f"特徴量生成完了: 履歴={result.source_count} "
        f"保存={result.saved_count}"
    )
    return 0


def run_train_model() -> int:
    """Train both LightGBM targets and display holdout metrics."""
    from scripts.train_model import LightGBMTrainingEngine

    reports = LightGBMTrainingEngine().train_all()
    for report in reports:
        metrics = report.metrics
        roc_auc = f"{metrics.roc_auc:.6f}" if metrics.roc_auc is not None else "N/A"
        print(f"[{report.model_kind}]")
        print(
            f"train={report.train_count} validation={report.validation_count} "
            f"validation_start={report.validation_start}"
        )
        print(
            f"Accuracy={metrics.accuracy:.6f} "
            f"Precision={metrics.precision:.6f} Recall={metrics.recall:.6f}"
        )
        print(
            f"F1={metrics.f1:.6f} ROC-AUC={roc_auc} "
            f"LogLoss={metrics.log_loss:.6f}"
        )
        print(f"model={report.model_path}")
        print(f"importance={report.importance_path}")
    return 0


def run_predict_model(race_key: str, model_kind: str) -> int:
    """Display sorted probabilities for one feature_history race."""
    from scripts.predict_model import RacePredictor

    predictions = RacePredictor().predict(race_key, model_kind)
    label = "1着確率" if model_kind == "winner" else "3着以内確率"
    print(f"{race_key} {label}")
    for prediction in predictions:
        print(
            f"{prediction.horse_no:>2} {prediction.horse_name:<18} "
            f"{prediction.probability:.2%}"
        )
    return 0


def run_timed(action: Callable[[], int]) -> int:
    """Run one CLI action and log its elapsed time."""
    started_at = time.perf_counter()
    try:
        return action()
    finally:
        LOGGER.info("処理時間: %.3f秒", time.perf_counter() - started_at)


def main() -> int:
    """Run the selected command."""
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "test-connection":
            return run_timed(lambda: run_connection_test(args.sid))
        if args.command == "race-schedule":
            return run_timed(lambda: run_race_schedule(args.sid))
        if args.command == "race-list":
            return run_timed(lambda: run_race_list(args.sid, args.date))
        if args.command == "race-entries":
            return run_timed(lambda: run_race_entries(args.sid, args.race))
        if args.command == "fetch-history":
            return run_timed(
                lambda: run_fetch_history(args.sid, args.from_date, args.to_date)
            )
        if args.command == "build-features":
            return run_timed(run_build_features)
        if args.command == "train-model":
            return run_timed(run_train_model)
        if args.command == "predict-model":
            return run_timed(
                lambda: run_predict_model(args.race, args.model)
            )
    except (JVLinkError, ModelError, StorageError, ValueError) as exc:
        LOGGER.error("エラー件数: 1")
        LOGGER.error("処理失敗: %s", exc)
        print(f"エラー: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

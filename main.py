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
    except (JVLinkError, StorageError, ValueError) as exc:
        LOGGER.error("処理失敗: %s", exc)
        print(f"エラー: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

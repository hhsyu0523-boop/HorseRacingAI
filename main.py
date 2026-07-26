"""Horse Racing AI Ver.1 command-line entry point."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date

DEFAULT_SID = "UNKNOWN"


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
    race_entries_parser = subparsers.add_parser(
        "race-entries",
        help="当日の出馬表を取得します",
    )
    race_entries_parser.add_argument(
        "--sid",
        default=os.getenv("JVLINK_SID", DEFAULT_SID),
        help="JRA-VAN発行SID（未登録ソフトはUNKNOWN）",
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
    print("========================")
    print("JRA開催情報")
    print("========================")
    display_date = schedule[0].date if schedule else date.today()
    print(display_date.isoformat())
    for meeting in schedule:
        print(meeting.racecourse)
    return 0


def run_race_list(sid: str) -> int:
    """Fetch and display every race number grouped by racecourse."""
    try:
        from scripts.jvlink_loader import JVLinkClient
    except ImportError as exc:
        print(f"JV-Link接続エラー: scripts/jvlink_loader.pyを読み込めません: {exc}")
        return 1

    meetings = JVLinkClient(sid=sid).get_race_list()
    for meeting in meetings:
        print("========================")
        print(meeting.racecourse)
        print("========================")
        print()
        for race_no in meeting.race_numbers:
            print(f"{race_no}R")
        print()
    return 0


def run_race_entries(sid: str) -> int:
    """Fetch and display runners grouped by racecourse and race number."""
    try:
        from scripts.jvlink_loader import JVLinkClient
    except ImportError as exc:
        print(f"JV-Link接続エラー: scripts/jvlink_loader.pyを読み込めません: {exc}")
        return 1

    entries = JVLinkClient(sid=sid).get_race_entries()
    if not entries:
        print("出馬表データがありません。")
        return 0

    current_race: tuple[str, int] | None = None
    for entry in entries:
        race_key = (entry.racecourse, entry.race_no)
        if race_key != current_race:
            if current_race is not None:
                print()
            print("========================")
            print(f"{entry.racecourse} {entry.race_no}R")
            print("========================")
            print("枠 馬番 馬名                 騎手       斤量")
            current_race = race_key
        print(
            f"{entry.gate_no:>2} {entry.horse_no:>4} "
            f"{entry.horse_name:<18} {entry.jockey_name:<8} "
            f"{entry.assigned_weight:>4.1f}"
        )
    return 0


def main() -> int:
    """Run the selected command."""
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "test-connection":
        return run_connection_test(args.sid)
    if args.command == "race-schedule":
        return run_race_schedule(args.sid)
    if args.command == "race-list":
        return run_race_list(args.sid)
    if args.command == "race-entries":
        return run_race_entries(args.sid)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

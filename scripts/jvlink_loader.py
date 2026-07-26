"""JRA-VAN DataLab (JV-Link) access layer.

The COM-specific code is kept in :class:`Win32ComJVLinkAdapter`; the public
client can therefore be unit-tested without JV-Link being installed.
"""

from __future__ import annotations

import logging
import platform
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)

DEFAULT_PROG_ID = "JVDTLab.JVLink"
DEFAULT_SID = "UNKNOWN"
DEFAULT_BUFFER_SIZE = 1_048_576
DATA_SPEC_RACE = "RACE"
DATA_SPEC_SE = "RACE"
RECORD_TYPE_SE = "SE"
RECORD_TYPE_RA = "RA"
RECORD_TYPE_HR = "HR"
CURRENT_WEEK_FROM_TIME = "00000000000000"
CURRENT_WEEK_OPTION = 2

JRA_RACECOURSES = {
    "01": "札幌",
    "02": "函館",
    "03": "福島",
    "04": "新潟",
    "05": "東京",
    "06": "中山",
    "07": "中京",
    "08": "京都",
    "09": "阪神",
    "10": "小倉",
}

ERROR_MESSAGES = {
    -1: "該当データがありません",
    -2: "ダウンロード失敗",
    -3: "データの準備中です",
    -101: "SIDが不正です",
    -102: "利用キーが不正です",
    -103: "利用キーが設定されていません",
    -111: "JV-Linkが初期化されていません",
    -201: "JVOpenのデータ種別が不正です",
    -301: "認証に失敗しました",
}


class JVLinkError(RuntimeError):
    """Base exception for JV-Link failures."""

    def __init__(self, operation: str, code: int | None, detail: str) -> None:
        self.operation = operation
        self.code = code
        super().__init__(
            f"{operation} failed"
            + (f" (code={code})" if code is not None else "")
            + f": {detail}"
        )


class JVLinkUnavailableError(JVLinkError):
    """Raised when the Windows COM component cannot be loaded."""


class JVLinkProtocol(Protocol):
    """Small protocol representing only the COM methods used by this project."""

    def init(self, sid: str) -> int: ...

    def open(
        self, dataspec: str, from_time: str, option: int
    ) -> tuple[int, int, int, str]: ...

    def read(self, buffer_size: int) -> tuple[int, str, str]: ...

    def close(self) -> None: ...


class Win32ComJVLinkAdapter:
    """Late-bound adapter for the JV-Link ActiveX COM component."""

    def __init__(self, prog_id: str = DEFAULT_PROG_ID) -> None:
        if platform.system() != "Windows":
            raise JVLinkUnavailableError(
                "COM load", None, "JV-LinkはWindows専用です"
            )
        try:
            import win32com.client  # type: ignore[import-untyped]
        except ImportError as exc:
            raise JVLinkUnavailableError(
                "COM load", None, "pywin32が未インストールです"
            ) from exc
        try:
            self._com = win32com.client.Dispatch(prog_id)
        except Exception as exc:
            raise JVLinkUnavailableError(
                "COM load",
                None,
                f"{prog_id}を生成できません。JV-LinkのインストールとPythonのビット数を確認してください",
            ) from exc

    def init(self, sid: str) -> int:
        return int(self._com.JVInit(sid))

    def open(
        self, dataspec: str, from_time: str, option: int
    ) -> tuple[int, int, int, str]:
        result = self._com.JVOpen(dataspec, from_time, option, 0, 0, "")
        return int(result[0]), int(result[1]), int(result[2]), str(result[3])

    def read(self, buffer_size: int) -> tuple[int, str, str]:
        result = self._com.JVRead(" " * buffer_size, buffer_size, "")
        return int(result[0]), str(result[1]), str(result[3])

    def close(self) -> None:
        self._com.JVClose()


@dataclass(frozen=True)
class ConnectionTestResult:
    success: bool
    code: int
    message: str


@dataclass(frozen=True)
class OpenResult:
    read_count: int
    download_count: int
    last_file_timestamp: str


@dataclass(frozen=True)
class RaceSchedule:
    """Summary of one JRA meeting held on a given date."""

    date: date
    racecourse: str
    meeting_no: int
    race_count: int


@dataclass(frozen=True)
class RaceList:
    """Race numbers held at one JRA racecourse on a given date."""

    date: date
    racecourse: str
    meeting_no: int
    race_numbers: tuple[int, ...]


@dataclass(frozen=True)
class RaceEntry:
    """One runner from a JV-Data SE (horse-per-race) record."""

    date: date
    racecourse: str
    meeting_no: int
    day_no: int
    race_no: int
    gate_no: int
    horse_no: int
    horse_name: str
    jockey_name: str
    assigned_weight: float


class JVLinkClient:
    """Lifecycle-safe facade for connection testing and accumulated data reads."""

    def __init__(
        self,
        sid: str = DEFAULT_SID,
        adapter_factory: Callable[[], JVLinkProtocol] = Win32ComJVLinkAdapter,
        *,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        retry_interval: float = 0.2,
        max_prepare_retries: int = 300,
    ) -> None:
        if buffer_size <= 0:
            raise ValueError("buffer_size must be positive")
        self.sid = sid
        self._adapter_factory = adapter_factory
        self.buffer_size = buffer_size
        self.retry_interval = retry_interval
        self.max_prepare_retries = max_prepare_retries
        self._adapter: JVLinkProtocol | None = None
        self._initialized = False
        self._opened = False

    def test_connection(self) -> ConnectionTestResult:
        """Run JVInit and always release the COM resource afterward."""
        try:
            self.connect()
            return ConnectionTestResult(True, 0, "JV-Link connection succeeded")
        except JVLinkError as exc:
            LOGGER.error("JV-Link接続テスト失敗: %s", exc)
            return ConnectionTestResult(False, exc.code or -9999, str(exc))
        finally:
            self.close()

    def open(
        self, dataspec: str, from_time: str, option: int = 2
    ) -> OpenResult:
        """Open an accumulated-data stream using JVOpen."""
        self.connect()
        if not dataspec or len(from_time) != 14 or not from_time.isdigit():
            raise ValueError("dataspec and 14-digit from_time are required")
        assert self._adapter is not None
        try:
            code, read_count, download_count, timestamp = self._adapter.open(
                dataspec, from_time, option
            )
        except Exception as exc:
            raise JVLinkError("JVOpen", None, str(exc)) from exc
        if code != 0:
            raise self._error("JVOpen", code)
        self._opened = True
        LOGGER.info(
            "JVOpen成功 (dataspec=%s, read=%d, download=%d)",
            dataspec,
            read_count,
            download_count,
        )
        return OpenResult(read_count, download_count, timestamp)

    def fetch(
        self, dataspec: str, from_time: str, option: int = 2
    ) -> Iterator[str]:
        """Open and stream raw records, closing JV-Link when iteration ends."""
        try:
            self.open(dataspec, from_time, option)
            yield from self.iter_records()
        finally:
            self.close()

    def get_race_schedule(
        self, target_date: date | None = None
    ) -> list[RaceSchedule]:
        """Fetch this week's RACE data and return the nearest JRA meeting."""
        scheduled_date = target_date or date.today()
        races: dict[tuple[date, str, int], set[int]] = {}

        for data in self.fetch(
            DATA_SPEC_RACE,
            CURRENT_WEEK_FROM_TIME,
            option=CURRENT_WEEK_OPTION,
        ):
            for record in data.splitlines():
                if len(record) < 27 or record[:2] != RECORD_TYPE_RA:
                    continue

                racecourse_code = record[19:21]
                if racecourse_code not in JRA_RACECOURSES:
                    continue
                try:
                    race_date_key = record[11:19]
                    race_date = date(
                        int(race_date_key[:4]),
                        int(race_date_key[4:6]),
                        int(race_date_key[6:8]),
                    )
                    meeting_no = int(record[21:23])
                    race_no = int(record[25:27])
                except ValueError:
                    LOGGER.warning("Invalid RA race key: %r", record[:27])
                    continue

                races.setdefault(
                    (race_date, racecourse_code, meeting_no), set()
                ).add(race_no)

        if not races:
            return []

        available_dates = sorted({race_date for race_date, _, _ in races})
        if scheduled_date in available_dates:
            selected_date = scheduled_date
        else:
            future_dates = [day for day in available_dates if day > scheduled_date]
            selected_date = future_dates[0] if future_dates else available_dates[-1]

        return [
            RaceSchedule(
                date=race_date,
                racecourse=JRA_RACECOURSES[racecourse_code],
                meeting_no=meeting_no,
                race_count=len(race_numbers),
            )
            for (
                race_date,
                racecourse_code,
                meeting_no,
            ), race_numbers in sorted(races.items())
            if race_date == selected_date
        ]

    def get_race_list(self, target_date: date | None = None) -> list[RaceList]:
        """Fetch race numbers from RA records."""
        race_date = target_date or date.today()
        races_by_course: dict[str, set[int]] = {}

        for data in self.fetch(
            DATA_SPEC_RACE,
            CURRENT_WEEK_FROM_TIME,
            option=CURRENT_WEEK_OPTION,
        ):
            for record in data.splitlines():
                record_type = record[:2]
                if record_type != RECORD_TYPE_RA:
                    continue

                racecourse_code = record[19:21]
                race_no_text = record[25:27]

                try:
                    race_no = int(race_no_text)
                except ValueError:
                    LOGGER.warning("Invalid RA race key: %r", record[:27])
                    continue

                races_by_course.setdefault(racecourse_code, set()).add(race_no)

        return [
            RaceList(
                date=race_date,
                racecourse=JRA_RACECOURSES.get(
                    racecourse_code, f"競馬場コード{racecourse_code}"
                ),
                meeting_no=0,
                race_numbers=tuple(sorted(race_numbers)),
            )
            for racecourse_code, race_numbers in sorted(races_by_course.items())
        ]

    def get_race_entries(self, target_date: date | None = None) -> list[RaceEntry]:
        """Fetch and parse the SE records for one race day."""
        requested_date = target_date or date.today()
        entries_by_key: dict[tuple[date, str, int, int, int, int], RaceEntry] = {}

        for data in self.fetch(
            DATA_SPEC_SE,
            CURRENT_WEEK_FROM_TIME,
            option=CURRENT_WEEK_OPTION,
        ):
            # Do not use splitlines(): mojibake from the COM bridge can contain
            # U+0085, which Python also treats as a line boundary.
            for record in data.split("\r\n"):
                entry = self._parse_se_record(record)
                if entry is None or not 1 <= entry.horse_no <= 18:
                    continue
                key = (
                    entry.date,
                    entry.racecourse,
                    entry.meeting_no,
                    entry.day_no,
                    entry.race_no,
                    entry.horse_no,
                )
                entries_by_key[key] = entry

        entries = list(entries_by_key.values())

        if not entries:
            return []

        available_dates = sorted({entry.date for entry in entries})
        if requested_date in available_dates:
            selected_date = requested_date
        else:
            future_dates = [day for day in available_dates if day > requested_date]
            selected_date = future_dates[0] if future_dates else available_dates[-1]

        racecourse_order = {
            racecourse: index
            for index, racecourse in enumerate(JRA_RACECOURSES.values())
        }
        return sorted(
            (entry for entry in entries if entry.date == selected_date),
            key=lambda entry: (
                racecourse_order.get(entry.racecourse, len(racecourse_order)),
                entry.meeting_no,
                entry.day_no,
                entry.race_no,
                entry.horse_no,
            ),
        )

    @staticmethod
    def _parse_se_record(record: str) -> RaceEntry | None:
        """Parse fields from a 555-byte, CP932-encoded SE fixed-width record."""
        try:
            raw = record.encode("cp932")
        except UnicodeEncodeError:
            # The late-bound COM bridge can expose the original JV-Data bytes
            # as Windows-1252 characters rather than decoded CP932 text.
            try:
                raw = bytes(
                    ord(char)
                    if ord(char) <= 0xFF
                    else char.encode("cp1252")[0]
                    for char in record
                )
            except (UnicodeEncodeError, ValueError):
                LOGGER.warning("Invalid characters in SE record")
                return None

        if len(raw) < 314 or raw[:2] != RECORD_TYPE_SE.encode("ascii"):
            return None

        def text(start: int, length: int) -> str:
            return raw[start - 1 : start - 1 + length].decode(
                "cp932", errors="replace"
            ).strip()

        try:
            race_date = date(int(text(12, 4)), int(text(16, 2)), int(text(18, 2)))
            racecourse_code = text(20, 2)
            meeting_no = int(text(22, 2))
            day_no = int(text(24, 2))
            race_no = int(text(26, 2))
            gate_no = int(text(28, 1))
            horse_no = int(text(29, 2))
            assigned_weight = int(text(289, 3)) / 10
        except ValueError:
            LOGGER.warning("Invalid SE race key: %r", raw[:30])
            return None

        return RaceEntry(
            date=race_date,
            racecourse=JRA_RACECOURSES.get(
                racecourse_code, f"競馬場コード{racecourse_code}"
            ),
            meeting_no=meeting_no,
            day_no=day_no,
            race_no=race_no,
            gate_no=gate_no,
            horse_no=horse_no,
            horse_name=text(41, 36),
            jockey_name=text(307, 8),
            assigned_weight=assigned_weight,
        )

    def connect(self) -> None:
        """Create the COM object and initialize it with the configured SID."""
        if self._initialized:
            return
        LOGGER.info("JV-Linkへ接続します (SID=%s)", self.sid)
        adapter = self._adapter_factory()
        try:
            code = adapter.init(self.sid)
        except Exception as exc:
            raise JVLinkError("JVInit", None, str(exc)) from exc
        if code != 0:
            self._safe_close(adapter)
            raise self._error("JVInit", code)
        self._adapter = adapter
        self._initialized = True
        LOGGER.info("JV-Linkへの接続に成功しました")

    def iter_records(self) -> Iterator[str]:
        """Yield raw JV-Data records from the currently opened stream."""
        if not self._opened or self._adapter is None:
            raise JVLinkError("JVRead", -111, "先にopen()を実行してください")
        prepare_retries = 0
        while True:
            try:
                code, data, _filename = self._adapter.read(self.buffer_size)
            except Exception as exc:
                raise JVLinkError("JVRead", None, str(exc)) from exc
            if code > 0:
                prepare_retries = 0
                yield data[:code]
            elif code in (0, -1):
                return
            elif code == -3:
                prepare_retries += 1
                if prepare_retries > self.max_prepare_retries:
                    raise JVLinkError(
                        "JVRead",
                        code,
                        "データ準備の待機がタイムアウトしました",
                    )
                time.sleep(self.retry_interval)
            else:
                raise self._error("JVRead", code)

    def close(self) -> None:
        adapter, self._adapter = self._adapter, None
        self._opened = False
        self._initialized = False
        if adapter is not None:
            self._safe_close(adapter)

    def __enter__(self) -> "JVLinkClient":
        self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @staticmethod
    def _safe_close(adapter: JVLinkProtocol) -> None:
        try:
            adapter.close()
        except Exception:
            LOGGER.warning("JVCloseに失敗しました", exc_info=True)

    @staticmethod
    def _error(operation: str, code: int) -> JVLinkError:
        return JVLinkError(operation, code, ERROR_MESSAGES.get(code, "不明なエラー"))

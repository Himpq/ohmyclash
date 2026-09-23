from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any

from .logging_config import get_logger


logger = get_logger("traffic_stats")


class HourlyTrafficStats:
    """Sample per-core cumulative counters and persist hourly byte deltas."""

    def __init__(self, data_dir: Path, server: Any):
        self._db_path = data_dir / "traffic-stats.sqlite3"
        self._server = server
        self._sample_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ohmyclash-traffic-stats", daemon=True)
        self._initialize_database()
        self._thread.start()

    def close(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._sample_all(final=True)
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def sample_instance(self, instance_id: str, *, final: bool = False) -> None:
        with self._server.lifecycle_lock:
            with self._sample_lock:
                manager = self._server.manager
                try:
                    status = manager.status(instance_id)
                    if status["running"]:
                        self._sample_status(manager, status, final=final)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("traffic sample failed core=%s error=%s", instance_id, exc)

    def sample_all(self, *, final: bool = False) -> None:
        self._sample_all(final=final)

    def daily(self, requested_date: str | None = None) -> dict[str, Any]:
        local_now = datetime.now().astimezone()
        selected_date = date.fromisoformat(requested_date) if requested_date else local_now.date()
        local_zone = local_now.tzinfo or timezone.utc
        day_start = datetime.combine(selected_date, day_time.min, tzinfo=local_zone)
        next_day = datetime.combine(selected_date + timedelta(days=1), day_time.min, tzinfo=local_zone)

        with self._connect() as database:
            rows = database.execute(
                """SELECT core_id, core_name, local_hour, upload_bytes, download_bytes, partial
                   FROM hourly_traffic WHERE local_date = ? ORDER BY core_id, hour_start""",
                (selected_date.isoformat(),),
            ).fetchall()
            gap_rows = database.execute(
                """SELECT core_id, core_name, started_at, ended_at, reason, upload_bytes, download_bytes
                   FROM traffic_gaps WHERE started_at < ? AND ended_at > ? ORDER BY started_at""",
                (next_day.timestamp(), day_start.timestamp()),
            ).fetchall()

        cores: dict[str, dict[str, Any]] = {}
        all_hours = self._empty_hours()
        for row in rows:
            core = cores.setdefault(row["core_id"], self._empty_core(row["core_id"], row["core_name"]))
            hour = int(row["local_hour"])
            self._add_hour(core["hours"][hour], int(row["upload_bytes"]), int(row["download_bytes"]), bool(row["partial"]))
            self._add_hour(all_hours[hour], int(row["upload_bytes"]), int(row["download_bytes"]), bool(row["partial"]))

        for core in cores.values():
            core["uploadBytes"] = sum(item["uploadBytes"] for item in core["hours"])
            core["downloadBytes"] = sum(item["downloadBytes"] for item in core["hours"])

        gaps: list[dict[str, Any]] = []
        unallocated = {"uploadBytes": 0, "downloadBytes": 0}
        totals = {"uploadBytes": 0, "downloadBytes": 0}
        for core in cores.values():
            totals["uploadBytes"] += core["uploadBytes"]
            totals["downloadBytes"] += core["downloadBytes"]

        for row in gap_rows:
            start = datetime.fromtimestamp(float(row["started_at"])).astimezone()
            end = datetime.fromtimestamp(float(row["ended_at"])).astimezone()
            upload_bytes = None if row["upload_bytes"] is None else int(row["upload_bytes"])
            download_bytes = None if row["download_bytes"] is None else int(row["download_bytes"])
            gap = {
                "coreId": row["core_id"],
                "coreName": row["core_name"],
                "from": start.isoformat(),
                "to": end.isoformat(),
                "reason": row["reason"],
                "uploadBytes": upload_bytes,
                "downloadBytes": download_bytes,
            }
            gaps.append(gap)
            core = cores.setdefault(row["core_id"], self._empty_core(row["core_id"], row["core_name"]))
            if upload_bytes is not None and download_bytes is not None and start.date() == selected_date and end.date() == selected_date:
                core["uploadBytes"] += upload_bytes
                core["downloadBytes"] += download_bytes
                totals["uploadBytes"] += upload_bytes
                totals["downloadBytes"] += download_bytes
                unallocated["uploadBytes"] += upload_bytes
                unallocated["downloadBytes"] += download_bytes

        for core in cores.values():
            core["hours"].sort(key=lambda item: item["hour"])

        all_hours.sort(key=lambda item: item["hour"])
        return {
            "date": selected_date.isoformat(),
            "timezone": str(local_zone),
            "sampledAt": local_now.isoformat(),
            "complete": bool(rows) and not gaps and not any(item["partial"] for item in all_hours),
            "totals": totals,
            "unallocated": unallocated,
            "hours": all_hours,
            "cores": sorted(cores.values(), key=lambda item: item["name"].casefold()),
            "gaps": gaps,
        }

    def _run(self) -> None:
        self._sample_all()
        while not self._stop_event.is_set():
            now = datetime.now().astimezone()
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            delay = max(0.1, next_hour.timestamp() - time.time())
            if self._stop_event.wait(delay):
                return
            self._sample_all()

    def _sample_all(self, *, final: bool = False) -> None:
        if self._stop_event.is_set() and not final:
            return
        with self._server.lifecycle_lock:
            with self._sample_lock:
                manager = self._server.manager
                try:
                    statuses = manager.statuses()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("traffic status scan failed error=%s", exc)
                    return
                for status in statuses:
                    if status.get("running"):
                        self._sample_status(manager, status, final=final)

    def _sample_status(self, manager: Any, status: dict[str, Any], *, final: bool) -> None:
        instance_id = str(status["id"])
        started_at = status.get("startedAt")
        if not started_at:
            logger.warning("traffic sample skipped without process start time core=%s", instance_id)
            return

        try:
            _, _, body = manager.request(instance_id, "GET", "/connections", timeout=3)
            payload = json.loads(body.decode("utf-8"))
            upload_total = self._counter(payload, "uploadTotal")
            download_total = self._counter(payload, "downloadTotal")
            self._record_snapshot(
                instance_id,
                str(status.get("name") or instance_id),
                str(started_at),
                upload_total,
                download_total,
                final=final,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("traffic sample failed core=%s error=%s", instance_id, exc)

    @staticmethod
    def _counter(payload: Any, key: str) -> int:
        if not isinstance(payload, dict):
            raise ValueError("Mihomo returned a non-object connections response")
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Mihomo response is missing a valid {key} counter")
        return int(value)

    def _record_snapshot(
        self,
        core_id: str,
        core_name: str,
        process_started_at: str,
        upload_total: int,
        download_total: int,
        *,
        final: bool,
    ) -> None:
        now = time.time()
        process_start_epoch = datetime.fromisoformat(process_started_at).timestamp()
        with self._connect() as database:
            state = database.execute(
                """SELECT process_started_at, upload_total, download_total, sampled_at, final
                   FROM traffic_state WHERE core_id = ?""",
                (core_id,),
            ).fetchone()

            previous_upload = 0
            previous_download = 0
            previous_sampled_at = process_start_epoch
            same_process = bool(state and state["process_started_at"] == process_started_at)

            if state and not same_process and not bool(state["final"]):
                self._insert_gap(database, core_id, core_name, float(state["sampled_at"]), process_start_epoch, "core_restarted_without_final_sample", None, None)

            if same_process:
                previous_upload = int(state["upload_total"])
                previous_download = int(state["download_total"])
                previous_sampled_at = float(state["sampled_at"])

            if upload_total < previous_upload or download_total < previous_download:
                self._insert_gap(database, core_id, core_name, previous_sampled_at, now, "counter_reset", None, None)
            else:
                upload_delta = upload_total - previous_upload
                download_delta = download_total - previous_download
                bucket = self._bucket_for_interval(previous_sampled_at, now)
                if bucket is None:
                    self._insert_gap(database, core_id, core_name, previous_sampled_at, now, "sampling_interval_crossed_multiple_hours", upload_delta, download_delta)
                else:
                    hour_start, local_date, local_hour, partial = bucket
                    database.execute(
                        """INSERT INTO hourly_traffic (
                               hour_start, local_date, local_hour, core_id, core_name,
                               upload_bytes, download_bytes, partial
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(hour_start, core_id) DO UPDATE SET
                               core_name = excluded.core_name,
                               upload_bytes = hourly_traffic.upload_bytes + excluded.upload_bytes,
                               download_bytes = hourly_traffic.download_bytes + excluded.download_bytes,
                               partial = MAX(hourly_traffic.partial, excluded.partial)""",
                        (hour_start, local_date, local_hour, core_id, core_name, upload_delta, download_delta, int(partial)),
                    )

            database.execute(
                """INSERT INTO traffic_state (
                       core_id, core_name, process_started_at, upload_total, download_total, sampled_at, final
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(core_id) DO UPDATE SET
                       core_name = excluded.core_name,
                       process_started_at = excluded.process_started_at,
                       upload_total = excluded.upload_total,
                       download_total = excluded.download_total,
                       sampled_at = excluded.sampled_at,
                       final = excluded.final""",
                (core_id, core_name, process_started_at, upload_total, download_total, now, int(final)),
            )

    @staticmethod
    def _bucket_for_interval(started_at: float, ended_at: float) -> tuple[float, str, int, bool] | None:
        if ended_at < started_at:
            return None
        start_local = datetime.fromtimestamp(started_at).astimezone()
        end_local = datetime.fromtimestamp(ended_at).astimezone()
        hour_start_local = start_local.replace(minute=0, second=0, microsecond=0)
        end_hour_local = end_local.replace(minute=0, second=0, microsecond=0)
        hour_start = hour_start_local.timestamp()
        end_hour_start = end_hour_local.timestamp()
        duration = ended_at - started_at

        if hour_start == end_hour_start:
            return hour_start, hour_start_local.date().isoformat(), hour_start_local.hour, True

        seconds_after_end_boundary = ended_at - end_hour_start
        spans_one_hour = 0 < end_hour_start - hour_start <= 3600
        if spans_one_hour and duration <= 3900 and seconds_after_end_boundary <= 300:
            starts_near_boundary = started_at - hour_start <= 300
            is_full_hour = starts_near_boundary and abs(duration - 3600) <= 300
            return hour_start, hour_start_local.date().isoformat(), hour_start_local.hour, not is_full_hour
        return None

    @staticmethod
    def _insert_gap(
        database: sqlite3.Connection,
        core_id: str,
        core_name: str,
        started_at: float,
        ended_at: float,
        reason: str,
        upload_bytes: int | None,
        download_bytes: int | None,
    ) -> None:
        database.execute(
            """INSERT INTO traffic_gaps (
                   core_id, core_name, started_at, ended_at, reason, upload_bytes, download_bytes
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (core_id, core_name, started_at, ended_at, reason, upload_bytes, download_bytes),
        )

    @staticmethod
    def _empty_hours() -> list[dict[str, Any]]:
        return [
            {"hour": hour, "uploadBytes": 0, "downloadBytes": 0, "partial": False}
            for hour in range(24)
        ]

    @classmethod
    def _empty_core(cls, core_id: str, name: str) -> dict[str, Any]:
        return {
            "id": core_id,
            "name": name,
            "uploadBytes": 0,
            "downloadBytes": 0,
            "hours": cls._empty_hours(),
        }

    @staticmethod
    def _add_hour(hour: dict[str, Any], upload_bytes: int, download_bytes: int, partial: bool) -> None:
        hour["uploadBytes"] += upload_bytes
        hour["downloadBytes"] += download_bytes
        hour["partial"] = hour["partial"] or partial

    def _initialize_database(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(
                """CREATE TABLE IF NOT EXISTS hourly_traffic (
                       hour_start REAL NOT NULL,
                       local_date TEXT NOT NULL,
                       local_hour INTEGER NOT NULL,
                       core_id TEXT NOT NULL,
                       core_name TEXT NOT NULL,
                       upload_bytes INTEGER NOT NULL,
                       download_bytes INTEGER NOT NULL,
                       partial INTEGER NOT NULL,
                       PRIMARY KEY (hour_start, core_id)
                   )"""
            )
            database.execute("CREATE INDEX IF NOT EXISTS idx_hourly_traffic_date ON hourly_traffic(local_date)")
            database.execute(
                """CREATE TABLE IF NOT EXISTS traffic_state (
                       core_id TEXT PRIMARY KEY,
                       core_name TEXT NOT NULL,
                       process_started_at TEXT NOT NULL,
                       upload_total INTEGER NOT NULL,
                       download_total INTEGER NOT NULL,
                       sampled_at REAL NOT NULL,
                       final INTEGER NOT NULL
                   )"""
            )
            database.execute(
                """CREATE TABLE IF NOT EXISTS traffic_gaps (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       core_id TEXT NOT NULL,
                       core_name TEXT NOT NULL,
                       started_at REAL NOT NULL,
                       ended_at REAL NOT NULL,
                       reason TEXT NOT NULL,
                       upload_bytes INTEGER,
                       download_bytes INTEGER
                   )"""
            )
            database.execute("CREATE INDEX IF NOT EXISTS idx_traffic_gaps_range ON traffic_gaps(started_at, ended_at)")

    @contextmanager
    def _connect(self):
        database = sqlite3.connect(self._db_path, timeout=3)
        database.row_factory = sqlite3.Row
        try:
            yield database
            database.commit()
        except Exception:
            database.rollback()
            raise
        finally:
            database.close()

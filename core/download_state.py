from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Any

STATUS_PENDING = "pending"
STATUS_DOWNLOADING = "downloading"
STATUS_DOWNLOADED = "downloaded"
STATUS_REUSED = "reused"
STATUS_EXISTING = "existing"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_SUCCESS = {STATUS_DOWNLOADED, STATUS_REUSED, STATUS_EXISTING}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class DownloadState:
    """SQLite checkpoint untuk satu folder sesi download."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quads (
                    quad_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    file_path TEXT NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    started_at TEXT,
                    completed_at TEXT,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_quads_status
                ON quads(status);
                """
            )

            # Migrasi aman untuk database sesi yang dibuat oleh versi lama.
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(quads)"
                ).fetchall()
            }
            if "duration_seconds" not in columns:
                connection.execute(
                    """
                    ALTER TABLE quads
                    ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 0
                    """
                )

    def set_meta(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO run_meta(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, encoded),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM run_meta WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def initialize_quads(self, quad_ids: Iterable[str]) -> None:
        now = utc_now()
        rows = [(str(quad_id), now) for quad_id in quad_ids]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                """
                INSERT INTO quads(quad_id, updated_at)
                VALUES(?, ?)
                ON CONFLICT(quad_id) DO NOTHING
                """,
                rows,
            )
            connection.commit()

    def reset_interrupted(self) -> int:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE quads
                SET status='pending', error_type='Interrupted',
                    error_message='Proses sebelumnya terhenti sebelum selesai',
                    updated_at=?
                WHERE status='downloading'
                """,
                (now,),
            )
            return int(cursor.rowcount)

    def reset_failed(self) -> int:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE quads
                SET status='pending', error_type='', error_message='',
                    http_status=NULL, updated_at=?
                WHERE status='failed'
                """,
                (now,),
            )
            return int(cursor.rowcount)

    def reconcile_existing_files(self, output_folder: Path) -> int:
        changed = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT quad_id, status FROM quads"
            ).fetchall()
            connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                if row["status"] in TERMINAL_SUCCESS:
                    continue
                path = output_folder / f"{row['quad_id']}.tif"
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size <= 0:
                    continue
                connection.execute(
                    """
                    UPDATE quads
                    SET status='existing', file_path=?, file_size=?,
                        error_type='', error_message='', completed_at=?, updated_at=?
                    WHERE quad_id=?
                    """,
                    (str(path.resolve()), size, utc_now(), utc_now(), row["quad_id"]),
                )
                changed += 1
            connection.commit()
        return changed

    def work_ids(self, limit: int) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT quad_id FROM quads
                WHERE status='pending'
                ORDER BY quad_id
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [str(row["quad_id"]) for row in rows]

    def mark_downloading(self, quad_id: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE quads
                SET status='downloading', started_at=?,
                    completed_at=NULL, duration_seconds=0,
                    updated_at=?
                WHERE quad_id=?
                """,
                (now, now, quad_id),
            )

    def mark_result(
        self,
        quad_id: str,
        *,
        status: str,
        attempts_delta: int = 0,
        file_path: str = "",
        file_size: int = 0,
        error_type: str = "",
        error_message: str = "",
        http_status: int | None = None,
        duration_seconds: float = 0.0,
    ) -> None:
        now = utc_now()
        completed_at = now if status in TERMINAL_SUCCESS | {STATUS_FAILED, STATUS_CANCELLED} else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE quads
                SET status=?, attempts=attempts+?, file_path=?, file_size=?,
                    error_type=?, error_message=?, http_status=?,
                    completed_at=?, duration_seconds=?, updated_at=?
                WHERE quad_id=?
                """,
                (
                    status, max(0, int(attempts_delta)), file_path,
                    max(0, int(file_size)), error_type, error_message,
                    http_status, completed_at,
                    max(0.0, float(duration_seconds)), now, quad_id,
                ),
            )

    def active_items(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Mengambil Quad yang saat ini berstatus downloading."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT quad_id, status, attempts, started_at, updated_at
                FROM quads
                WHERE status='downloading'
                ORDER BY started_at, quad_id
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_items(
        self,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Mengambil hasil terminal terbaru untuk monitoring UI."""
        terminal_statuses = sorted(
            TERMINAL_SUCCESS
            | {
                STATUS_FAILED,
                STATUS_CANCELLED,
            }
        )
        placeholders = ",".join(
            "?" for _ in terminal_statuses
        )
        query = f"""
            SELECT quad_id, status, attempts, file_path, file_size,
                   error_type, error_message, http_status,
                   started_at, completed_at, duration_seconds, updated_at
            FROM quads
            WHERE status IN ({placeholders})
            ORDER BY COALESCE(completed_at, updated_at) DESC, quad_id
            LIMIT ?
        """
        parameters = [
            *terminal_statuses,
            max(1, int(limit)),
        ]
        with self._connect() as connection:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        result = {
            STATUS_PENDING: 0,
            STATUS_DOWNLOADING: 0,
            STATUS_DOWNLOADED: 0,
            STATUS_REUSED: 0,
            STATUS_EXISTING: 0,
            STATUS_FAILED: 0,
            STATUS_CANCELLED: 0,
            "total": 0,
            "completed": 0,
            "processed": 0,
        }
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM quads GROUP BY status"
            ).fetchall()
        for row in rows:
            result[str(row["status"])] = int(row["count"])
        result["total"] = sum(
            value for key, value in result.items()
            if key not in {"total", "completed", "processed"}
        )
        result["completed"] = sum(result[key] for key in TERMINAL_SUCCESS)
        result["processed"] = (
            result["completed"]
            + result[STATUS_FAILED]
            + result[STATUS_CANCELLED]
        )
        return result

    def total_bytes(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(file_size), 0) AS total FROM quads"
            ).fetchone()
        return int(row["total"] if row else 0)

    def rows(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM quads ORDER BY quad_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def failures(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT quad_id, status, attempts, error_type, error_message,
                       http_status, updated_at
                FROM quads WHERE status='failed' ORDER BY quad_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def export_failures(self, json_path: Path, csv_path: Path) -> list[dict[str, Any]]:
        failures = self.failures()
        atomic_write_json(json_path, {"failed_count": len(failures), "items": failures})
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = csv_path.with_suffix(csv_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "quad_id", "status", "attempts", "error_type",
                    "error_message", "http_status", "updated_at",
                ],
            )
            writer.writeheader()
            writer.writerows(failures)
        temporary.replace(csv_path)
        return failures

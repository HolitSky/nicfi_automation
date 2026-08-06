from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter

from core import planet_report as report
from core.run_identity import (
    create_run_id,
    ensure_run_id,
    selection_signature_from_payload,
)
from core.download_state import (
    DownloadState,
    STATUS_CANCELLED,
    STATUS_DOWNLOADED,
    STATUS_EXISTING,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REUSED,
    atomic_write_json,
    read_json,
)

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH, override=True)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def resolve_path(name: str, default: str) -> Path:
    raw = (os.getenv(name, "") or default).strip().strip('"').strip("'")
    raw = os.path.expandvars(os.path.expanduser(raw))
    path = Path(raw)
    return (path if path.is_absolute() else BASE_DIR / path).resolve()


API_KEY = os.getenv("PL_API_KEY", "").strip()
MOSAIC_NAME = os.getenv("MOSAIC_NAME", "").strip()
BBOX = os.getenv("BBOX", "").strip()
MAX_WORKERS = max(1, int(os.getenv("MAX_WORKERS", "8")))
DOWNLOAD_BATCH_SIZE = max(MAX_WORKERS, int(os.getenv("DOWNLOAD_BATCH_SIZE", "250")))
DOWNLOAD_CHUNK_BYTES = max(1, int(os.getenv("DOWNLOAD_CHUNK_MB", "8"))) * 1024 * 1024
DOWNLOAD_MAX_RETRIES = max(1, int(os.getenv("DOWNLOAD_MAX_RETRIES", "5")))
RETRY_BACKOFF_SECONDS = max(0.25, float(os.getenv("RETRY_BACKOFF_SECONDS", "3")))
NETWORK_FAILURE_PAUSE_THRESHOLD = max(1, int(os.getenv("NETWORK_FAILURE_PAUSE_THRESHOLD", "10")))
RESUME_PARTIAL_DOWNLOADS = env_bool("RESUME_PARTIAL_DOWNLOADS", True)
USE_PERSISTENT_HTTP_SESSIONS = env_bool("USE_PERSISTENT_HTTP_SESSIONS", True)
ENABLE_DOWNLOAD_RESUME = env_bool("ENABLE_DOWNLOAD_RESUME", True)
DOWNLOAD_STATE_FILENAME = os.getenv("DOWNLOAD_STATE_FILENAME", "download_state.sqlite").strip()
ACTIVE_RUN_FILE = resolve_path("ACTIVE_RUN_FILE", "config/active_run.json")
PAUSE_FLAG_FILE = resolve_path("PAUSE_FLAG_FILE", "config/download.pause")
CANCEL_FLAG_FILE = resolve_path("CANCEL_FLAG_FILE", "config/download.cancel")
RUN_HEARTBEAT_SECONDS = max(1.0, float(os.getenv("RUN_HEARTBEAT_SECONDS", "5")))
EXCEL_UPDATE_EVERY_BATCH = env_bool("EXCEL_UPDATE_EVERY_BATCH", False)
EXCEL_UPDATE_ON_PAUSE = env_bool("EXCEL_UPDATE_ON_PAUSE", True)
EXPORT_EXCEL = env_bool("EXPORT_EXCEL", True)
DRY_RUN = env_bool("DRY_RUN", False)
FAILED_JSON_NAME = "failed_quads.json"
FAILED_CSV_NAME = "failed_quads.csv"
REQUESTED_RUN_ID = os.getenv("REQUESTED_RUN_ID", "").strip()
REQUESTED_SELECTION_SIGNATURE = os.getenv(
    "REQUESTED_SELECTION_SIGNATURE",
    "",
).strip()

TERMINAL_RUN_STATUSES = {
    "completed",
    "completed_with_failures",
    "failed",
    "cancelled",
}

_thread_local = threading.local()


class FatalAccessError(RuntimeError):
    pass


class NetworkHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consecutive_failures = 0

    def success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    def failure(self) -> int:
        with self._lock:
            self._consecutive_failures += 1
            return self._consecutive_failures


NETWORK_HEALTH = NetworkHealth()


class Tee:
    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, value: str) -> int:
        for stream in self.streams:
            stream.write(value)
            stream.flush()
        return len(value)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def create_http_session(use_auth: bool) -> requests.Session:
    session = requests.Session()
    if use_auth:
        session.auth = (API_KEY, "")
    adapter = HTTPAdapter(
        max_retries=0,
        pool_connections=MAX_WORKERS,
        pool_maxsize=MAX_WORKERS,
        pool_block=True,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_worker_session(use_auth: bool) -> requests.Session:
    if not USE_PERSISTENT_HTTP_SESSIONS:
        return create_http_session(use_auth)
    key = "auth_session" if use_auth else "anonymous_session"
    session = getattr(_thread_local, key, None)
    if session is None:
        session = create_http_session(use_auth)
        setattr(_thread_local, key, session)
    return session


def touch_flag(path: Path, reason: str) -> None:
    atomic_write_json(path, {"reason": reason, "created_at": datetime.now().isoformat(timespec="seconds")})


def clear_control_flags() -> None:
    PAUSE_FLAG_FILE.unlink(missing_ok=True)
    CANCEL_FLAG_FILE.unlink(missing_ok=True)


def format_bytes_short(value: int | float) -> str:
    """Format ukuran file untuk log satu baris."""
    size = max(0.0, float(value or 0))
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]

    for candidate in units:
        unit = candidate
        if size < 1024.0 or candidate == units[-1]:
            break
        size /= 1024.0

    precision = 0 if unit == "B" else 2
    return f"{size:.{precision}f} {unit}"


def load_active_context() -> dict[str, Any]:
    payload = read_json(ACTIVE_RUN_FILE)
    if not payload:
        raise RuntimeError("Tidak ada sesi aktif yang dapat dilanjutkan.")

    required = ["run_name", "output_folder", "excel_path", "started_at"]
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise RuntimeError(
            "active_run.json tidak lengkap: "
            + ", ".join(missing)
        )

    output_folder = Path(str(payload["output_folder"])).resolve()
    selection_snapshot = Path(
        str(
            payload.get("selection_snapshot")
            or output_folder / "selection_snapshot.json"
        )
    ).resolve()

    return {
        "run_id": ensure_run_id(payload),
        "run_name": str(payload["run_name"]),
        "started_at": datetime.fromisoformat(str(payload["started_at"])),
        "mosaic_name": str(payload.get("mosaic_name", "")).strip(),
        "area_mode": str(payload.get("area_mode", "")).strip(),
        "bbox": payload.get("bbox", ""),
        "selection_signature": str(
            payload.get("selection_signature", "")
        ).strip(),
        "selection_snapshot": selection_snapshot,
        "output_folder": output_folder,
        "excel_path": Path(str(payload["excel_path"])).resolve(),
        "manifest_path": Path(
            str(
                payload.get("manifest_path")
                or output_folder / "run_manifest.json"
            )
        ).resolve(),
        "log_path": Path(
            str(
                payload.get("run_log_path")
                or output_folder / "run.log"
            )
        ).resolve(),
    }

def create_or_resume_context(mode: str) -> dict[str, Any]:
    if mode in {"resume", "retry-failed"}:
        context = load_active_context()
        context["output_folder"].mkdir(parents=True, exist_ok=True)
        return context

    selection = report.load_selection_metadata()
    if not selection:
        raise RuntimeError(
            "Konfigurasi area belum tersimpan. "
            "Simpan konfigurasi melalui Area Selector terlebih dahulu."
        )

    signature = selection_signature_from_payload(selection)
    if (
        REQUESTED_SELECTION_SIGNATURE
        and signature != REQUESTED_SELECTION_SIGNATURE
    ):
        raise RuntimeError(
            "Pilihan area berubah sebelum downloader dimulai. "
            "Lakukan Preview dan Simpan Konfigurasi ulang."
        )

    started_at = datetime.now()
    context = report.create_run_context(started_at)
    context.update(
        {
            "run_id": REQUESTED_RUN_ID or create_run_id(started_at),
            "mosaic_name": str(
                selection.get("mosaic_name") or MOSAIC_NAME
            ).strip(),
            "area_mode": str(selection.get("area_mode", "")).strip(),
            "bbox": selection.get("bbox", BBOX),
            "selection_signature": signature,
            "selection_snapshot": (
                Path(context["output_folder"])
                / "selection_snapshot.json"
            ),
        }
    )
    return context

def make_run_payload(
    context: dict[str, Any],
    *,
    status: str,
    state: DownloadState | None = None,
    error: str = "",
    batch_index: int = 0,
    total_batches: int = 0,
    started_monotonic: float | None = None,
    mode: str = "new",
) -> dict[str, Any]:
    counts = state.counts() if state else {}
    total_bytes = state.total_bytes() if state else 0
    active_items = state.active_items(MAX_WORKERS) if state else []
    recent_items = state.recent_items(1) if state else []
    last_result = recent_items[0] if recent_items else {}

    elapsed = (
        max(0.0, time.monotonic() - started_monotonic)
        if started_monotonic
        else 0.0
    )
    speed_bps = total_bytes / elapsed if elapsed > 0 else 0.0
    processed = (
        counts.get("processed", counts.get("completed", 0))
        if counts
        else 0
    )
    remaining = (
        max(0, counts.get("total", 0) - processed)
        if counts
        else 0
    )
    average_seconds = elapsed / max(1, processed) if counts else 0.0
    eta_seconds = (
        int(remaining * average_seconds)
        if average_seconds > 0
        else None
    )

    output_folder = Path(context["output_folder"])
    payload = {
        "schema_version": 4,
        "run_id": str(context.get("run_id", "")),
        "selection_signature": str(
            context.get("selection_signature", "")
        ),
        "selection_snapshot": str(
            Path(context["selection_snapshot"]).resolve()
        ),
        "status": status,
        "mode": mode,
        "pid": os.getpid(),
        "run_name": str(context["run_name"]),
        "started_at": (
            context["started_at"].isoformat(timespec="seconds")
            if isinstance(context["started_at"], datetime)
            else str(context["started_at"])
        ),
        "heartbeat_at": datetime.now().isoformat(timespec="seconds"),
        "mosaic_name": str(context.get("mosaic_name", "")),
        "area_mode": str(context.get("area_mode", "")),
        "bbox": context.get("bbox", ""),
        "quad_count": (
            counts.get("total", 0)
            if counts
            else 0
        ),
        "counts": counts,
        "active_quad_ids": [
            str(item.get("quad_id", ""))
            for item in active_items
            if item.get("quad_id")
        ],
        "active_quad_items": active_items,
        "last_result": last_result,
        "batch_index": batch_index,
        "total_batches": total_batches,
        "total_bytes": total_bytes,
        "speed_bps": speed_bps,
        "eta_seconds": eta_seconds,
        "output_folder": str(output_folder.resolve()),
        "excel_path": str(Path(context["excel_path"]).resolve()),
        "manifest_path": str(Path(context["manifest_path"]).resolve()),
        "run_log_path": str(Path(context["log_path"]).resolve()),
        "state_database": str(
            (output_folder / DOWNLOAD_STATE_FILENAME).resolve()
        ),
        "failed_json": str((output_folder / FAILED_JSON_NAME).resolve()),
        "failed_csv": str((output_folder / FAILED_CSV_NAME).resolve()),
        "error": error,
    }
    if status in TERMINAL_RUN_STATUSES:
        payload["completed_at"] = datetime.now().isoformat(
            timespec="seconds"
        )
    return payload

def write_run_payload(
    context: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """
    active_run.json hanya menyimpan proses aktif/paused.
    Status terminal dipindahkan ke last_run.json agar tidak mengunci UI baru.
    """
    manifest_path = Path(context["manifest_path"])
    atomic_write_json(manifest_path, payload)

    status = str(payload.get("status", "")).strip()
    if status in TERMINAL_RUN_STATUSES:
        atomic_write_json(report.LAST_RUN_FILE, payload)
        current_active = read_json(ACTIVE_RUN_FILE)
        current_run_id = ensure_run_id(current_active) if current_active else ""
        if (
            not current_active
            or current_run_id == str(payload.get("run_id", ""))
        ):
            ACTIVE_RUN_FILE.unlink(missing_ok=True)
        return

    atomic_write_json(ACTIVE_RUN_FILE, payload)

def build_report_results(state: DownloadState) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    for row in state.rows():
        status = str(row["status"])
        if status == STATUS_DOWNLOADED:
            mapped = "downloaded"
        elif status == STATUS_REUSED:
            mapped = "reused_hardlink"
        elif status == STATUS_EXISTING:
            mapped = "existing"
        elif status == STATUS_FAILED:
            mapped = "failed"
        else:
            mapped = status
        results[str(row["quad_id"])] = {
            "status": mapped,
            "error": str(row.get("error_message") or "") if status == STATUS_FAILED else "",
        }
    return results


def update_excel(
    state: DownloadState,
    quads: list[dict[str, Any]],
    context: dict[str, Any],
) -> Path:
    records, invalid_files = report.build_active_records(
        quads,
        build_report_results(state),
        Path(context["output_folder"]),
    )
    return report.export_quads_excel(
        records,
        invalid_files,
        Path(context["excel_path"]),
        str(context["run_name"]),
    )


def download_one(
    quad: dict[str, Any],
    output_folder: Path,
    previous_tiff_index: dict[str, Path],
) -> dict[str, Any]:
    quad_id = str(quad["id"])
    target = output_folder / f"{quad_id}.tif"
    temporary = output_folder / f"{quad_id}.tif.part"

    if target.exists() and target.stat().st_size > 0:
        NETWORK_HEALTH.success()
        return {"status": STATUS_EXISTING, "attempts": 0, "path": target, "size": target.stat().st_size}

    previous = previous_tiff_index.get(quad_id)
    if previous and previous.exists() and previous.stat().st_size > 0:
        report.reuse_previous_tiff(previous, target)
        NETWORK_HEALTH.success()
        return {"status": STATUS_REUSED, "attempts": 0, "path": target, "size": target.stat().st_size}

    if DRY_RUN:
        return {"status": STATUS_PENDING, "attempts": 0, "path": temporary, "size": 0}

    url = report.get_download_url(quad)
    use_auth = urlparse(url).hostname == "api.planet.com"
    last_error: Exception | None = None
    last_http_status: int | None = None

    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        if CANCEL_FLAG_FILE.exists():
            return {"status": STATUS_CANCELLED, "attempts": attempt - 1, "path": temporary, "size": temporary.stat().st_size if temporary.exists() else 0, "error_type": "Cancelled", "error": "Sesi dibatalkan"}
        try:
            offset = temporary.stat().st_size if RESUME_PARTIAL_DOWNLOADS and temporary.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset > 0 else {}
            session = get_worker_session(use_auth)
            with session.get(
                url,
                stream=True,
                allow_redirects=True,
                timeout=(30, 900),
                headers=headers,
            ) as response:
                last_http_status = response.status_code
                if response.status_code in {401, 403}:
                    raise FatalAccessError(f"Planet mengembalikan HTTP {response.status_code}. Periksa API key dan akses mosaic.")
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
                response.raise_for_status()
                append_mode = offset > 0 and response.status_code == 206
                if offset > 0 and not append_mode:
                    offset = 0
                mode = "ab" if append_mode else "wb"
                with temporary.open(mode) as stream:
                    for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                        if chunk:
                            stream.write(chunk)
                temporary.replace(target)
            NETWORK_HEALTH.success()
            return {"status": STATUS_DOWNLOADED, "attempts": attempt, "path": target, "size": target.stat().st_size}
        except FatalAccessError:
            raise
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            failures = NETWORK_HEALTH.failure()
            if failures >= NETWORK_FAILURE_PAUSE_THRESHOLD:
                touch_flag(PAUSE_FLAG_FILE, "network")
                return {"status": STATUS_PENDING, "attempts": attempt, "path": temporary, "size": temporary.stat().st_size if temporary.exists() else 0, "error_type": type(exc).__name__, "error": str(exc), "network_pause": True}
        except requests.HTTPError as exc:
            last_error = exc
            status_code = exc.response.status_code if exc.response is not None else None
            last_http_status = status_code
            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                break
        except OSError as exc:
            last_error = exc
            break

        if attempt < DOWNLOAD_MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    return {
        "status": STATUS_FAILED,
        "attempts": DOWNLOAD_MAX_RETRIES,
        "path": temporary,
        "size": temporary.stat().st_size if temporary.exists() else 0,
        "error_type": type(last_error).__name__ if last_error else "DownloadError",
        "error": str(last_error or "Download gagal"),
        "http_status": last_http_status,
    }


def process_batch(
    batch_ids: list[str],
    quad_by_id: dict[str, dict[str, Any]],
    state: DownloadState,
    context: dict[str, Any],
    previous_tiff_index: dict[str, Path],
    *,
    started_monotonic: float,
    batch_index: int,
    total_batches: int,
    mode: str,
) -> str:
    queue = deque(batch_ids)
    futures: dict[
        Future[dict[str, Any]],
        tuple[str, float],
    ] = {}
    last_heartbeat = 0.0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS,
        thread_name_prefix="planet-quad",
    ) as executor:
        while queue or futures:
            pause_requested = PAUSE_FLAG_FILE.exists()
            cancel_requested = CANCEL_FLAG_FILE.exists()

            while (
                queue
                and len(futures) < MAX_WORKERS
                and not pause_requested
                and not cancel_requested
            ):
                quad_id = queue.popleft()
                state.mark_downloading(quad_id)
                item_started = time.monotonic()

                future = executor.submit(
                    download_one,
                    quad_by_id[quad_id],
                    Path(context["output_folder"]),
                    previous_tiff_index,
                )
                futures[future] = (
                    quad_id,
                    item_started,
                )

                print(
                    f"START {quad_id} | "
                    f"batch={batch_index}/{total_batches} | "
                    f"active={len(futures)}/{MAX_WORKERS}"
                )

            if not futures:
                if cancel_requested:
                    return "cancelled"
                if pause_requested:
                    reason = read_json(
                        PAUSE_FLAG_FILE
                    ).get("reason")
                    return (
                        "paused_network"
                        if reason == "network"
                        else "paused"
                    )
                break

            done, _ = wait(
                set(futures),
                timeout=0.5,
                return_when=FIRST_COMPLETED,
            )

            for future in done:
                quad_id, item_started = futures.pop(
                    future
                )
                duration_seconds = max(
                    0.0,
                    time.monotonic() - item_started,
                )

                try:
                    result = future.result()
                except FatalAccessError:
                    raise
                except Exception as exc:
                    result = {
                        "status": STATUS_FAILED,
                        "attempts": 1,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "path": "",
                        "size": 0,
                    }

                status = str(
                    result.get(
                        "status",
                        STATUS_FAILED,
                    )
                )
                attempts = int(
                    result.get(
                        "attempts",
                        0,
                    )
                )
                file_size = int(
                    result.get(
                        "size",
                        0,
                    )
                )
                error_type = str(
                    result.get(
                        "error_type",
                        "",
                    )
                )
                error_message = str(
                    result.get(
                        "error",
                        "",
                    )
                )

                state.mark_result(
                    quad_id,
                    status=status,
                    attempts_delta=attempts,
                    file_path=(
                        str(
                            Path(
                                result["path"]
                            ).resolve()
                        )
                        if result.get("path")
                        else ""
                    ),
                    file_size=file_size,
                    error_type=error_type,
                    error_message=error_message,
                    http_status=result.get(
                        "http_status"
                    ),
                    duration_seconds=duration_seconds,
                )

                if result.get(
                    "network_pause"
                ):
                    pause_requested = True

                if status in {
                    STATUS_DOWNLOADED,
                    STATUS_REUSED,
                    STATUS_EXISTING,
                }:
                    print(
                        f"DONE {quad_id} | "
                        f"status={status} | "
                        f"size={format_bytes_short(file_size)} | "
                        f"attempts={attempts} | "
                        f"time={duration_seconds:.2f}s"
                    )
                elif status == STATUS_FAILED:
                    print(
                        f"FAILED {quad_id} | "
                        f"attempts={attempts} | "
                        f"type={error_type or 'DownloadError'} | "
                        f"http={result.get('http_status') or '-'} | "
                        f"error={error_message or 'Download gagal'}"
                    )
                elif status == STATUS_CANCELLED:
                    print(
                        f"CANCELLED {quad_id} | "
                        f"time={duration_seconds:.2f}s"
                    )
                elif status == STATUS_PENDING:
                    print(
                        f"PAUSED {quad_id} | "
                        f"status=pending | "
                        f"reason={error_type or 'menunggu resume'} | "
                        f"partial={format_bytes_short(file_size)}"
                    )
                else:
                    print(
                        f"RESULT {quad_id} | "
                        f"status={status} | "
                        f"size={format_bytes_short(file_size)} | "
                        f"time={duration_seconds:.2f}s"
                    )

                counts = state.counts()
                print(
                    f"PROGRESS {counts['processed']}/{counts['total']} | "
                    f"last={quad_id} status={status} | "
                    f"downloaded={counts[STATUS_DOWNLOADED]} "
                    f"reused={counts[STATUS_REUSED]} "
                    f"existing={counts[STATUS_EXISTING]} "
                    f"failed={counts[STATUS_FAILED]} "
                    f"active={counts.get('downloading', 0)} "
                    f"pending={counts[STATUS_PENDING]}"
                )

            now = time.monotonic()
            if (
                now - last_heartbeat
                >= RUN_HEARTBEAT_SECONDS
            ):
                payload = make_run_payload(
                    context,
                    status=(
                        "pausing"
                        if PAUSE_FLAG_FILE.exists()
                        else "running"
                    ),
                    state=state,
                    batch_index=batch_index,
                    total_batches=total_batches,
                    started_monotonic=started_monotonic,
                    mode=mode,
                )
                write_run_payload(
                    context,
                    payload,
                )
                last_heartbeat = now

    if CANCEL_FLAG_FILE.exists():
        return "cancelled"

    if PAUSE_FLAG_FILE.exists():
        reason = read_json(
            PAUSE_FLAG_FILE
        ).get("reason")
        return (
            "paused_network"
            if reason == "network"
            else "paused"
        )

    return "batch_complete"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Planet Quad downloader dengan batch, checkpoint, pause, dan resume.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--resume", action="store_true", help="Lanjutkan sesi aktif pada folder yang sama.")
    group.add_argument("--retry-failed", action="store_true", help="Ulangi hanya Quad berstatus failed pada sesi aktif.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mode = (
        "retry-failed"
        if args.retry_failed
        else "resume"
        if args.resume
        else "new"
    )

    if not API_KEY:
        raise RuntimeError("PL_API_KEY belum diisi di file .env.")
    if mode != "new" and not ENABLE_DOWNLOAD_RESUME:
        raise RuntimeError(
            "ENABLE_DOWNLOAD_RESUME=false; sesi tidak dapat dilanjutkan."
        )

    context = create_or_resume_context(mode)
    run_mosaic_name = str(context.get("mosaic_name", "")).strip()
    run_bbox_value = context.get("bbox", "")
    run_bbox = (
        ",".join(str(value) for value in run_bbox_value)
        if isinstance(run_bbox_value, (list, tuple))
        else str(run_bbox_value).strip()
    )

    if not run_mosaic_name:
        raise RuntimeError("Nama mosaic sesi tidak tersedia.")
    if not run_bbox:
        raise RuntimeError("BBOX sesi tidak tersedia.")

    # Pastikan seluruh helper report/Excel menggunakan identitas sesi,
    # bukan .env yang mungkin sudah diubah setelah sesi dibuat.
    report.MOSAIC_NAME = run_mosaic_name
    report.BBOX = run_bbox

    output_folder = Path(context["output_folder"])
    output_folder.mkdir(parents=True, exist_ok=True)
    log_path = Path(context["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_stream = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = Tee(sys.__stdout__, log_stream)
    sys.stderr = Tee(sys.__stderr__, log_stream)

    clear_control_flags()
    started_monotonic = time.monotonic()
    state = DownloadState(output_folder / DOWNLOAD_STATE_FILENAME)

    try:
        print("=" * 76)
        print("PLANET GLOBAL QUARTERLY — RESUMABLE QUAD DOWNLOADER")
        print("=" * 76)
        print(f"Mode                : {mode}")
        print(f"Run ID              : {context['run_id']}")
        print(f"Run name            : {context['run_name']}")
        print(f"Mosaic              : {run_mosaic_name}")
        print(f"Area mode           : {context.get('area_mode', '')}")
        print(f"Workers             : {MAX_WORKERS}")
        print(f"Batch size          : {DOWNLOAD_BATCH_SIZE}")
        print(f"Chunk               : {DOWNLOAD_CHUNK_BYTES // (1024*1024)} MB")
        print(f"State DB            : {state.database_path}")
        print(f"Output folder       : {output_folder}")
        print(f"Excel               : {context['excel_path']}")

        initial = make_run_payload(
            context,
            status="initializing",
            state=state,
            started_monotonic=started_monotonic,
            mode=mode,
        )
        write_run_payload(context, initial)

        snapshot_path = Path(context["selection_snapshot"])
        snapshot = read_json(snapshot_path)

        mosaic = report.find_mosaic(run_mosaic_name)
        quads = report.list_quads(
            str(mosaic["id"]),
            report.validate_bbox(run_bbox),
        )

        if mode == "new":
            selection_metadata = report.load_selection_metadata()
            selected_ids = report.load_selected_quad_ids()
            if selected_ids is not None:
                quads = [
                    quad
                    for quad in quads
                    if str(quad.get("id")) in selected_ids
                ]
        else:
            snapshot_ids = {
                str(value).strip()
                for value in snapshot.get("quad_ids", [])
                if str(value).strip()
            }
            checkpoint_ids = {
                str(row["quad_id"])
                for row in state.rows()
            }
            selected_ids = snapshot_ids or checkpoint_ids
            if selected_ids:
                quads = [
                    quad
                    for quad in quads
                    if str(quad.get("id")) in selected_ids
                ]

        if not quads:
            raise RuntimeError("Tidak ada Quad untuk pilihan area sesi ini.")

        quad_by_id = {
            str(quad["id"]): quad
            for quad in quads
        }
        state.initialize_quads(quad_by_id)

        if mode == "new":
            snapshot = {
                "schema_version": 2,
                "run_id": context["run_id"],
                "run_name": context["run_name"],
                "selection_signature": context["selection_signature"],
                "mosaic_name": run_mosaic_name,
                "area_mode": context.get("area_mode", ""),
                "bbox": run_bbox_value,
                "quad_count": len(quad_by_id),
                "quad_ids": sorted(quad_by_id),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            atomic_write_json(snapshot_path, snapshot)

        state.set_meta("run_id", context["run_id"])
        state.set_meta("selection_signature", context["selection_signature"])
        state.set_meta("mosaic_name", run_mosaic_name)
        state.set_meta("area_mode", context.get("area_mode", ""))
        state.set_meta("bbox", run_bbox_value)
        state.set_meta("run_name", context["run_name"])
        state.set_meta("selection_snapshot", str(snapshot_path.resolve()))

        state.reset_interrupted()
        state.reconcile_existing_files(output_folder)
        if mode == "retry-failed":
            reset_count = state.reset_failed()
            print(f"Quad failed dikembalikan ke pending: {reset_count}")

        previous_tiff_index = report.build_previous_tiff_index(output_folder)
        start_counts = state.counts()
        total = start_counts["total"]
        total_batches = max(1, math.ceil(total / DOWNLOAD_BATCH_SIZE))
        # Resume melanjutkan nomor batch berdasarkan progress yang sudah selesai.
        batch_index = min(
            total_batches,
            int(start_counts.get("processed", 0)) // DOWNLOAD_BATCH_SIZE,
        )
        final_status = "completed"

        while True:
            batch_ids = state.work_ids(DOWNLOAD_BATCH_SIZE)
            if not batch_ids:
                break

            batch_index += 1
            batch_index = min(batch_index, total_batches)
            print(
                f"\nBATCH {batch_index}/{total_batches} — "
                f"{len(batch_ids)} Quad"
            )
            payload = make_run_payload(
                context,
                status="running",
                state=state,
                batch_index=batch_index,
                total_batches=total_batches,
                started_monotonic=started_monotonic,
                mode=mode,
            )
            write_run_payload(context, payload)

            result = process_batch(
                batch_ids,
                quad_by_id,
                state,
                context,
                previous_tiff_index,
                started_monotonic=started_monotonic,
                batch_index=batch_index,
                total_batches=total_batches,
                mode=mode,
            )

            state.export_failures(
                output_folder / FAILED_JSON_NAME,
                output_folder / FAILED_CSV_NAME,
            )
            if EXCEL_UPDATE_EVERY_BATCH and EXPORT_EXCEL:
                update_excel(state, quads, context)

            if result in {"paused", "paused_network", "cancelled"}:
                final_status = result
                break

        counts = state.counts()
        if final_status == "completed":
            final_status = (
                "completed_with_failures"
                if counts[STATUS_FAILED]
                else "completed"
            )

        if EXPORT_EXCEL and (
            final_status in {"completed", "completed_with_failures"}
            or (
                final_status in {"paused", "paused_network"}
                and EXCEL_UPDATE_ON_PAUSE
            )
        ):
            print("\nMembuat/memperbarui workbook Excel...")
            update_excel(state, quads, context)

        failures = state.export_failures(
            output_folder / FAILED_JSON_NAME,
            output_folder / FAILED_CSV_NAME,
        )
        payload = make_run_payload(
            context,
            status=final_status,
            state=state,
            batch_index=batch_index,
            total_batches=total_batches,
            started_monotonic=started_monotonic,
            mode=mode,
        )
        payload["failed_ids"] = [
            item["quad_id"]
            for item in failures
        ]
        write_run_payload(context, payload)

        print("\n" + "=" * 76)
        print(f"STATUS AKHIR: {final_status}")
        print(json.dumps(counts, indent=2, ensure_ascii=False))
        if failures:
            print("Quad gagal:")
            for item in failures:
                print(
                    f"- {item['quad_id']} | "
                    f"{item['error_type']} | "
                    f"{item['error_message']}"
                )
        print(f"RUN_FOLDER={output_folder.resolve()}")
        print(f"RUN_EXCEL={Path(context['excel_path']).resolve()}")
        return 0 if final_status != "failed" else 1

    except FatalAccessError as exc:
        payload = make_run_payload(
            context,
            status="failed",
            state=state,
            error=str(exc),
            started_monotonic=started_monotonic,
            mode=mode,
        )
        write_run_payload(context, payload)
        print(f"FATAL: {exc}")
        return 2
    except Exception as exc:
        payload = make_run_payload(
            context,
            status="failed",
            state=state,
            error=str(exc),
            started_monotonic=started_monotonic,
            mode=mode,
        )
        write_run_payload(context, payload)
        print(f"FATAL: {type(exc).__name__}: {exc}")
        return 1
    finally:
        log_stream.flush()
        log_stream.close()

if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import folium
import requests
import shapefile
import streamlit as st
from dotenv import dotenv_values, set_key
from folium.plugins import Draw, Fullscreen, MousePosition
from streamlit_folium import st_folium
from run_identity import (
    build_selection_signature,
    create_run_id,
    ensure_run_id,
)


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DOWNLOADER_PATH = BASE_DIR / "download_quads.py"
BACKUP_DIR = BASE_DIR / "config_backups"
ACTIVE_RUN_FILE_DEFAULT = BASE_DIR / "config" / "active_run.json"
PAUSE_FLAG_FILE_DEFAULT = BASE_DIR / "config" / "download.pause"
CANCEL_FLAG_FILE_DEFAULT = BASE_DIR / "config" / "download.cancel"
LAUNCHER_LOG_PATH = BASE_DIR / "config" / "downloader_launcher.log"

# Logo UI. File disimpan pada workspace:
# nicfi_automation/asset/logo/logo-kemenhut-new.png
LOGO_KEMENHUT_PATH = (
    BASE_DIR
    / "asset"
    / "logo"
    / "logo-kemenhut-new.png"
)

GITHUB_USERNAME = "HolitSky"
GITHUB_PROFILE_URL = "https://github.com/HolitSky"

PLANET_API_ROOT = "https://api.planet.com/basemaps/v1"
GLOBAL_QUARTERLY_PATTERN = re.compile(
    r"^global_quarterly_(?P<year>\d{4})q(?P<quarter>[1-4])_mosaic$",
    re.IGNORECASE,
)

EARTH_RADIUS = 6_378_137.0
MAX_WEB_MERCATOR_LAT = 85.05112878

DEFAULT_INDONESIA_BBOX = (
    94.48192600,
    -12.46876000,
    141.69006400,
    6.57730300,
)

STATUS_EMPTY = "EMPTY"
STATUS_DRAFT = "DRAFT"
STATUS_READY = "READY"

STEP_PREVIEW = "PREVIEW"
STEP_SAVE = "SAVE"
STEP_DOWNLOAD = "DOWNLOAD"
STEP_DONE = "DONE"
STEP_ERROR = "ERROR"


# ---------------------------------------------------------------------------
# PATH DAN ENV
# ---------------------------------------------------------------------------

def resolve_project_path(
    value: str,
    default: str,
) -> Path:
    raw = (value or default).strip()
    raw = raw.strip('"').strip("'")
    raw = os.path.expandvars(
        os.path.expanduser(raw)
    )

    path = Path(raw)

    if not path.is_absolute():
        path = BASE_DIR / path

    return path.resolve()


def path_for_env(path: Path) -> str:
    try:
        return (
            path.resolve()
            .relative_to(BASE_DIR)
            .as_posix()
        )
    except ValueError:
        return path.resolve().as_posix()


def load_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}

    return {
        key: value or ""
        for key, value in dotenv_values(
            ENV_PATH
        ).items()
        if key
    }


def env_bool(
    env: dict[str, str],
    key: str,
    default: bool = False,
) -> bool:
    raw_value = env.get(key)

    if raw_value is None or raw_value == "":
        return default

    return raw_value.strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def remove_env_keys(
    keys: set[str],
) -> None:
    """
    Menghapus key lama dari .env tanpa membuat backup.

    Digunakan untuk membersihkan AREA_NAME yang sudah tidak dipakai.
    """
    if not ENV_PATH.exists():
        return

    normalized_keys = {
        key.strip()
        for key in keys
        if key.strip()
    }

    original_lines = ENV_PATH.read_text(
        encoding="utf-8"
    ).splitlines()

    kept_lines: list[str] = []

    for line in original_lines:
        stripped = line.strip()

        if (
            not stripped
            or stripped.startswith("#")
            or "=" not in line
        ):
            kept_lines.append(line)
            continue

        key = line.split("=", 1)[0].strip()

        if key not in normalized_keys:
            kept_lines.append(line)

    ENV_PATH.write_text(
        "\n".join(kept_lines).rstrip() + "\n",
        encoding="utf-8",
    )


def sanitize_run_component(
    value: str,
) -> str:
    cleaned = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        value.strip(),
    )
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("._-") or "planet_mosaic"


def mosaic_period_label(
    mosaic_name: str,
) -> str:
    """
    Mengubah global_quarterly_2025q3_mosaic menjadi:
    Juli 2025 – September 2025.
    """
    match = GLOBAL_QUARTERLY_PATTERN.fullmatch(
        mosaic_name.strip()
    )

    if not match:
        return "Periode tidak dikenali dari nama mosaic"

    year = match.group("year")
    quarter = int(match.group("quarter"))

    period_by_quarter = {
        1: ("Januari", "Maret"),
        2: ("April", "Juni"),
        3: ("Juli", "September"),
        4: ("Oktober", "Desember"),
    }

    start_month, end_month = period_by_quarter[
        quarter
    ]

    return (
        f"{start_month} {year} – "
        f"{end_month} {year}"
    )


@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def validate_planet_mosaic(
    mosaic_name: str,
    api_key: str,
) -> dict[str, Any]:
    """
    Memastikan nama mosaic tersedia dan dapat diakses akun Planet.
    """
    clean_name = mosaic_name.strip()

    if not clean_name:
        raise RuntimeError(
            "Nama mosaic tidak boleh kosong."
        )

    if not api_key.strip():
        raise RuntimeError(
            "PL_API_KEY belum tersedia di file .env."
        )

    response = requests.get(
        f"{PLANET_API_ROOT}/mosaics/",
        params={
            "name__is": clean_name,
        },
        auth=(
            api_key.strip(),
            "",
        ),
        timeout=(15, 60),
    )

    if response.status_code == 401:
        raise RuntimeError(
            "API key Planet tidak valid atau tidak terbaca."
        )

    if response.status_code == 403:
        raise RuntimeError(
            "Akun Planet tidak memiliki akses ke mosaic tersebut."
        )

    response.raise_for_status()
    payload = response.json()

    mosaic = next(
        (
            item
            for item in payload.get(
                "mosaics",
                [],
            )
            if str(
                item.get("name", "")
            ).strip() == clean_name
        ),
        None,
    )

    if mosaic is None:
        raise RuntimeError(
            "Mosaic tidak ditemukan atau tidak dapat diakses: "
            f"{clean_name}"
        )

    return {
        "id": str(
            mosaic.get("id", "")
        ),
        "name": clean_name,
        "period_label": mosaic_period_label(
            clean_name
        ),
    }


def folder_matches_mosaic(
    folder: Path,
    mosaic_name: str,
) -> bool:
    """
    Menentukan apakah folder run berisi TIFF untuk mosaic yang sama.

    Mencegah TIFF quarter lain dianggap Existing.
    """
    manifest_path = (
        folder / "run_manifest.json"
    )

    if manifest_path.exists():
        try:
            payload = json.loads(
                manifest_path.read_text(
                    encoding="utf-8"
                )
            )

            return (
                str(
                    payload.get(
                        "mosaic_name",
                        "",
                    )
                ).strip()
                == mosaic_name.strip()
            )

        except (
            OSError,
            json.JSONDecodeError,
        ):
            pass

    mosaic_component = sanitize_run_component(
        mosaic_name
    )

    return (
        folder.name == mosaic_name
        or mosaic_component in folder.name
    )


def parse_bbox_text(
    value: str,
    fallback: tuple[
        float,
        float,
        float,
        float,
    ],
) -> tuple[float, float, float, float]:
    try:
        parsed = tuple(
            float(item.strip())
            for item in value.split(",")
        )

        if len(parsed) != 4:
            return fallback

        min_lon, min_lat, max_lon, max_lat = parsed

        if (
            min_lon >= max_lon
            or min_lat >= max_lat
        ):
            return fallback

        return parsed

    except (TypeError, ValueError):
        return fallback


def bbox_text(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> str:
    return ",".join(
        f"{value:.8f}"
        for value in bbox
    )


def backup_file(
    path: Path,
    prefix: str,
) -> Path | None:
    if not path.exists():
        return None

    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = (
        BACKUP_DIR
        / f"{prefix}_{timestamp}{path.suffix or '.backup'}"
    )

    shutil.copy2(
        path,
        backup_path,
    )

    return backup_path


def update_env(
    updates: dict[str, str],
    *,
    create_backup: bool = False,
) -> None:
    """
    Memperbarui .env secara langsung.

    Backup hanya dibuat jika BACKUP_CONFIG_FILES=true.
    Default false agar folder config_backups tidak terus bertambah.
    """
    if not ENV_PATH.exists():
        ENV_PATH.touch()

    if create_backup:
        backup_file(
            ENV_PATH,
            "env",
        )

    for key, value in updates.items():
        set_key(
            str(ENV_PATH),
            key,
            str(value),
            quote_mode="never",
        )


# ---------------------------------------------------------------------------
# KOORDINAT DAN SHAPEFILE
# ---------------------------------------------------------------------------

def detect_source_crs(
    prj_path: Path,
) -> str:
    if not prj_path.exists():
        return "wgs84_assumed"

    text = prj_path.read_text(
        encoding="utf-8",
        errors="ignore",
    ).upper()

    if (
        "MERCATOR_AUXILIARY_SPHERE" in text
        or "PSEUDO-MERCATOR" in text
        or "WEB_MERCATOR" in text
        or "WGS_1984_WEB_MERCATOR" in text
    ):
        return "web_mercator"

    if (
        "GEOGCS" in text
        and (
            "WGS_1984" in text
            or "WGS 84" in text
        )
    ):
        return "wgs84"

    raise RuntimeError(
        "CRS shapefile belum didukung. "
        "Gunakan WGS84 (EPSG:4326) atau "
        "Web Mercator (EPSG:3857)."
    )


def web_mercator_to_lonlat(
    x_value: float,
    y_value: float,
) -> tuple[float, float]:
    longitude = math.degrees(
        x_value / EARTH_RADIUS
    )

    latitude = math.degrees(
        2.0
        * math.atan(
            math.exp(
                y_value / EARTH_RADIUS
            )
        )
        - math.pi / 2.0
    )

    latitude = max(
        -MAX_WEB_MERCATOR_LAT,
        min(
            MAX_WEB_MERCATOR_LAT,
            latitude,
        ),
    )

    return longitude, latitude


def transform_point(
    x_value: float,
    y_value: float,
    source_crs: str,
) -> tuple[float, float]:
    if source_crs == "web_mercator":
        return web_mercator_to_lonlat(
            float(x_value),
            float(y_value),
        )

    return (
        float(x_value),
        float(y_value),
    )


def transform_bbox(
    raw_bbox: Iterable[float],
    source_crs: str,
) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = (
        float(value)
        for value in raw_bbox
    )

    min_lon, min_lat = transform_point(
        min_x,
        min_y,
        source_crs,
    )

    max_lon, max_lat = transform_point(
        max_x,
        max_y,
        source_crs,
    )

    return (
        min(min_lon, max_lon),
        min(min_lat, max_lat),
        max(min_lon, max_lon),
        max(min_lat, max_lat),
    )


def transform_coordinates(
    coordinates: Any,
    source_crs: str,
) -> Any:
    if (
        isinstance(
            coordinates,
            (list, tuple),
        )
        and len(coordinates) >= 2
        and isinstance(
            coordinates[0],
            (int, float),
        )
        and isinstance(
            coordinates[1],
            (int, float),
        )
    ):
        longitude, latitude = (
            transform_point(
                coordinates[0],
                coordinates[1],
                source_crs,
            )
        )

        return [
            longitude,
            latitude,
        ]

    if isinstance(
        coordinates,
        (list, tuple),
    ):
        return [
            transform_coordinates(
                item,
                source_crs,
            )
            for item in coordinates
        ]

    return coordinates


def find_field_index(
    field_names: list[str],
    requested_field: str,
) -> int:
    lookup = {
        name.strip().lower(): index
        for index, name in enumerate(
            field_names
        )
    }

    index = lookup.get(
        requested_field.strip().lower()
    )

    if index is None:
        raise RuntimeError(
            f"Field '{requested_field}' "
            "tidak ditemukan. "
            f"Field tersedia: {field_names}"
        )

    return index


@st.cache_data(
    show_spinner=(
        "Membaca indeks Quad Indonesia..."
    )
)
def read_master_index(
    shapefile_path_text: str,
    id_field: str,
    modified_time: float,
) -> dict[str, Any]:
    """
    Hanya membaca Quad ID dan bounding box.

    Geometry polygon tidak dikonversi pada startup,
    sehingga payload dan waktu muat jauh lebih kecil.
    """
    del modified_time

    shapefile_path = Path(
        shapefile_path_text
    )

    if not shapefile_path.exists():
        raise FileNotFoundError(
            "Shapefile master tidak ditemukan: "
            f"{shapefile_path}"
        )

    dbf_path = shapefile_path.with_suffix(
        ".dbf"
    )

    if not dbf_path.exists():
        raise FileNotFoundError(
            "DBF shapefile tidak ditemukan: "
            f"{dbf_path}"
        )

    source_crs = detect_source_crs(
        shapefile_path.with_suffix(".prj")
    )

    reader = shapefile.Reader(
        str(shapefile_path)
    )

    try:
        field_names = [
            field[0]
            for field in reader.fields[1:]
        ]

        id_index = find_field_index(
            field_names,
            id_field,
        )

        records: list[dict[str, Any]] = []

        for shape_record in (
            reader.iterShapeRecords()
        ):
            quad_id = str(
                shape_record.record[
                    id_index
                ]
            ).strip()

            shape_bbox = getattr(
                shape_record.shape,
                "bbox",
                None,
            )

            if not quad_id or not shape_bbox:
                continue

            records.append(
                {
                    "id": quad_id,
                    "bbox": transform_bbox(
                        shape_bbox,
                        source_crs,
                    ),
                }
            )

        if not records:
            raise RuntimeError(
                "Shapefile tidak memiliki "
                "Quad ID yang dapat dibaca."
            )

        return {
            "records": records,
            "all_ids": sorted(
                {
                    record["id"]
                    for record in records
                }
            ),
            "source_crs": source_crs,
        }

    finally:
        reader.close()


@st.cache_data(
    show_spinner=(
        "Menyiapkan preview Quad terpilih..."
    )
)
def load_selected_geometries(
    shapefile_path_text: str,
    id_field: str,
    selected_ids_tuple: tuple[str, ...],
    modified_time: float,
) -> list[dict[str, Any]]:
    """
    Geometry hanya dibaca untuk ID hasil preview.

    Pada area custom biasanya hanya puluhan/ratusan
    polygon yang dikirim ke browser, bukan 7.731.
    """
    del modified_time

    selected_ids = set(
        selected_ids_tuple
    )

    if not selected_ids:
        return []

    shapefile_path = Path(
        shapefile_path_text
    )

    source_crs = detect_source_crs(
        shapefile_path.with_suffix(".prj")
    )

    reader = shapefile.Reader(
        str(shapefile_path)
    )

    try:
        field_names = [
            field[0]
            for field in reader.fields[1:]
        ]

        id_index = find_field_index(
            field_names,
            id_field,
        )

        features: list[
            dict[str, Any]
        ] = []

        for shape_record in (
            reader.iterShapeRecords()
        ):
            quad_id = str(
                shape_record.record[
                    id_index
                ]
            ).strip()

            if quad_id not in selected_ids:
                continue

            raw_geometry = (
                shape_record.shape
                .__geo_interface__
            )

            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "id": quad_id,
                    },
                    "geometry": {
                        "type": (
                            raw_geometry[
                                "type"
                            ]
                        ),
                        "coordinates": (
                            transform_coordinates(
                                raw_geometry[
                                    "coordinates"
                                ],
                                source_crs,
                            )
                        ),
                    },
                }
            )

        return features

    finally:
        reader.close()


def boxes_intersect(
    first: tuple[
        float,
        float,
        float,
        float,
    ],
    second: tuple[
        float,
        float,
        float,
        float,
    ],
) -> bool:
    return not (
        first[2] < second[0]
        or first[0] > second[2]
        or first[3] < second[1]
        or first[1] > second[3]
    )


def select_quad_ids(
    master_records: list[
        dict[str, Any]
    ],
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> list[str]:
    return sorted(
        {
            record["id"]
            for record in master_records
            if boxes_intersect(
                record["bbox"],
                bbox,
            )
        }
    )


def geometry_points(
    coordinates: Any,
) -> Iterable[
    tuple[float, float]
]:
    if (
        isinstance(
            coordinates,
            (list, tuple),
        )
        and len(coordinates) >= 2
        and isinstance(
            coordinates[0],
            (int, float),
        )
        and isinstance(
            coordinates[1],
            (int, float),
        )
    ):
        yield (
            float(coordinates[0]),
            float(coordinates[1]),
        )
        return

    if isinstance(
        coordinates,
        (list, tuple),
    ):
        for item in coordinates:
            yield from geometry_points(
                item
            )


def bbox_from_drawing(
    drawing: dict[str, Any],
) -> tuple[
    float,
    float,
    float,
    float,
] | None:
    geometry = drawing.get(
        "geometry",
        drawing,
    )

    if geometry.get("type") not in {
        "Polygon",
        "MultiPolygon",
    }:
        return None

    points = list(
        geometry_points(
            geometry.get(
                "coordinates",
                [],
            )
        )
    )

    if not points:
        return None

    longitudes = [
        point[0]
        for point in points
    ]
    latitudes = [
        point[1]
        for point in points
    ]

    return (
        min(longitudes),
        min(latitudes),
        max(longitudes),
        max(latitudes),
    )


def area_km2(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> float:
    min_lon, min_lat, max_lon, max_lat = bbox

    middle_latitude = math.radians(
        (min_lat + max_lat) / 2.0
    )

    width = (
        math.radians(
            max_lon - min_lon
        )
        * EARTH_RADIUS
        * math.cos(
            middle_latitude
        )
    )

    height = (
        math.radians(
            max_lat - min_lat
        )
        * EARTH_RADIUS
    )

    return abs(
        width * height
    ) / 1_000_000.0


# ---------------------------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------------------------

def initialize_state(
    env: dict[str, str],
) -> None:
    defaults: dict[str, Any] = {
        "area_status": STATUS_EMPTY,
        "draft_bbox": None,
        "ready_bbox": None,
        "selected_ids": [],
        "app_mode": (
            "indonesia"
            if env.get("AREA_MODE")
            == "indonesia"
            else "custom"
        ),
        "app_mosaic": env.get(
            "MOSAIC_NAME",
            "global_quarterly_2026q2_mosaic",
        ),
        "mosaic_validated": False,
        "mosaic_period_label": mosaic_period_label(
            env.get(
                "MOSAIC_NAME",
                "global_quarterly_2026q2_mosaic",
            )
        ),
        "mosaic_validation_notice": "",
        "notice": "",
        "workflow_step": STEP_PREVIEW,
        "last_download_code": None,
        "last_download_log": [],
        "last_run_name": "",
        "last_run_folder": "",
        "last_run_excel": "",
        "selection_signature": "",
        "attached_run_id": "",
        "attached_selection_signature": "",
        "detached_run_id": "",
        # Layer master Quad default OFF agar startup tetap ringan.
        "show_master_quad_layer": env_bool(
            env,
            "SHOW_MASTER_QUADS_DEFAULT",
            False,
        ),
        # Digunakan untuk memaksa komponen peta dibuat ulang saat area
        # atau pilihan layer berubah.
        "map_revision": 0,
        # True setelah browser pernah mengirim rectangle Leaflet Draw.
        "leaflet_drawing_present": False,
        "downloader_pid": None,
        "download_launching": False,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def detach_current_run() -> None:
    """Melepaskan status run dari workflow pilihan area saat ini."""
    attached = str(
        st.session_state.get("attached_run_id", "")
    ).strip()
    if attached:
        st.session_state.detached_run_id = attached
    st.session_state.attached_run_id = ""
    st.session_state.attached_selection_signature = ""


def current_selection_signature(
    *,
    mosaic_name: str,
    area_mode: str,
    bbox: Any,
    selected_ids: Iterable[Any],
) -> str:
    return build_selection_signature(
        mosaic_name=mosaic_name,
        area_mode=area_mode,
        bbox=bbox,
        quad_ids=selected_ids,
    )


def clear_area_state(
    *,
    rebuild_map: bool = True,
) -> None:
    """Menghapus seluruh state AOI dan mengembalikan workflow ke awal."""
    detach_current_run()
    st.session_state.area_status = (
        STATUS_EMPTY
    )
    st.session_state.draft_bbox = None
    st.session_state.ready_bbox = None
    st.session_state.selected_ids = []
    st.session_state.notice = ""
    st.session_state.workflow_step = STEP_PREVIEW
    st.session_state.last_download_code = None
    st.session_state.last_download_log = []
    st.session_state.last_run_name = ""
    st.session_state.last_run_folder = ""
    st.session_state.last_run_excel = ""
    st.session_state.selection_signature = ""
    st.session_state.leaflet_drawing_present = False

    if rebuild_map:
        st.session_state.map_revision += 1


def refresh_map_component() -> None:
    """Membangun ulang komponen peta setelah toggle layer berubah."""
    st.session_state.map_revision += 1


def replace_active_rectangle(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> bool:
    """
    Menjadikan rectangle terbaru sebagai satu-satunya area aktif.

    Rectangle Leaflet hanya dipakai sebagai alat input sementara.
    Setelah koordinat diterima, komponen peta dibangun ulang dan hanya
    rectangle canonical dari session state yang ditampilkan.
    """
    current_bbox = (
        st.session_state.ready_bbox
        if (
            st.session_state.area_status
            == STATUS_READY
        )
        else st.session_state.draft_bbox
    )

    # Event yang sama dapat terkirim kembali saat fragment dirender.
    # Abaikan hanya jika area memang sudah berada pada status draft
    # dengan koordinat yang identik.
    if (
        current_bbox == bbox
        and st.session_state.area_status
        == STATUS_DRAFT
    ):
        return False

    had_previous_area = bool(
        st.session_state.draft_bbox
        or st.session_state.ready_bbox
        or st.session_state.selected_ids
    )

    detach_current_run()
    st.session_state.draft_bbox = bbox
    st.session_state.ready_bbox = None
    st.session_state.selected_ids = []
    st.session_state.area_status = (
        STATUS_DRAFT
    )
    st.session_state.workflow_step = STEP_PREVIEW
    st.session_state.last_download_code = None
    st.session_state.last_download_log = []
    st.session_state.last_run_name = ""
    st.session_state.last_run_folder = ""
    st.session_state.last_run_excel = ""
    st.session_state.selection_signature = ""
    st.session_state.leaflet_drawing_present = True

    if had_previous_area:
        st.session_state.notice = (
            "Area baru telah digambar. Preview sebelumnya dibatalkan. "
            "Klik **Preview Area** untuk menghitung Quad pada area terbaru."
        )
    else:
        st.session_state.notice = (
            "Area sudah digambar. Klik **Preview Area** untuk "
            "menghitung Quad yang beririsan."
        )

    # Key baru membuat st_folium membuang seluruh objek Draw mentah.
    # Peta berikutnya hanya menampilkan satu rectangle dari state Python.
    st.session_state.map_revision += 1

    return True


# ---------------------------------------------------------------------------
# STATUS FILE DAN PENYIMPANAN
# ---------------------------------------------------------------------------

@st.cache_data(
    ttl=30,
    show_spinner=False,
)
def scan_existing_tiff_ids(
    output_dir_text: str,
    mosaic_name: str,
) -> set[str]:
    """
    Hanya membaca TIFF dari run dengan mosaic yang sama.

    Quad ID yang sama pada 2026Q2 dan 2025Q3 mewakili citra berbeda,
    sehingga tidak boleh dianggap file Existing lintas mosaic.
    """
    output_root = Path(
        output_dir_text
    )

    if not output_root.exists():
        return set()

    ids: set[str] = set()

    for folder in output_root.iterdir():
        if not folder.is_dir():
            continue

        if not folder_matches_mosaic(
            folder,
            mosaic_name,
        ):
            continue

        for path in folder.rglob("*.tif"):
            if (
                path.is_file()
                and path.stat().st_size > 0
            ):
                ids.add(path.stem)

    return ids


def local_tiff_status(
    selected_ids: set[str],
    output_dir: Path,
    mosaic_name: str,
) -> tuple[
    set[str],
    set[str],
]:
    existing_all = scan_existing_tiff_ids(
        str(output_dir.resolve()),
        mosaic_name,
    )

    return (
        selected_ids & existing_all,
        selected_ids - existing_all,
    )


def save_selection(
    *,
    area_mode: str,
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
    selected_ids: list[str],
    mosaic_name: str,
    selected_file: Path,
    create_backup: bool = False,
) -> Path:
    selected_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if create_backup:
        backup_file(
            selected_file,
            "selected_quads",
        )

    selection_signature = current_selection_signature(
        mosaic_name=mosaic_name,
        area_mode=area_mode,
        bbox=bbox,
        selected_ids=selected_ids,
    )

    payload = {
        "schema_version": 4,
        "selection_signature": selection_signature,
        "generated_at": (
            datetime.now().isoformat(
                timespec="seconds"
            )
        ),
        "area_mode": area_mode,
        "bbox": list(bbox),
        "mosaic_name": mosaic_name,
        "quad_count": len(
            selected_ids
        ),
        "quad_ids": sorted(
            set(selected_ids)
        ),
    }

    selected_file.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    update_env(
        {
            "MOSAIC_NAME": mosaic_name,
            "AREA_MODE": area_mode,
            "BBOX": bbox_text(bbox),
            "USE_SELECTED_QUADS": "true",
            "SELECTED_QUADS_FILE": (
                path_for_env(
                    selected_file
                )
            ),
        },
        create_backup=create_backup,
    )

    # Bersihkan key versi lama bila masih tersisa pada .env.
    remove_env_keys(
        {"AREA_NAME"}
    )

    return selected_file



def load_last_run_info(
    last_run_file: Path,
) -> dict[str, Any]:
    if not last_run_file.exists():
        return {}

    try:
        payload = json.loads(
            last_run_file.read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return {}

    return payload


def write_run_log_from_ui(
    run_info: dict[str, Any],
    lines: list[str],
) -> None:
    if not lines:
        return

    if not bool(
        run_info.get(
            "save_run_log",
            True,
        )
    ):
        return

    log_path_text = str(
        run_info.get(
            "run_log_path",
            "",
        )
    ).strip()

    if not log_path_text:
        return

    log_path = Path(log_path_text)
    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    log_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# MAP
# ---------------------------------------------------------------------------

def add_base_layers(
    map_object: folium.Map,
) -> None:
    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/"
            "ArcGIS/rest/services/"
            "World_Imagery/MapServer/"
            "tile/{z}/{y}/{x}"
        ),
        attr=(
            "Tiles © Esri — Source: Esri, "
            "Maxar, Earthstar Geographics, "
            "and the GIS User Community"
        ),
        name="Satellite",
        overlay=False,
        control=True,
    ).add_to(map_object)

    folium.TileLayer(
        tiles="OpenStreetMap",
        name="OpenStreetMap",
        overlay=False,
        control=True,
    ).add_to(map_object)


@st.cache_data(
    show_spinner=False,
)
def build_master_quad_geojson(
    shapefile_path_text: str,
    id_field: str,
    modified_time: float,
) -> dict[str, Any]:
    """
    Membentuk layer grid ringan dari bounding box setiap Quad.

    Geometry penuh shapefile tidak dikirim untuk layer master. Setiap record
    hanya menjadi rectangle lima titik sehingga proses dan payload lebih kecil.
    """
    master = read_master_index(
        shapefile_path_text,
        id_field,
        modified_time,
    )

    features: list[dict[str, Any]] = []

    for record in master["records"]:
        min_lon, min_lat, max_lon, max_lat = record["bbox"]

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": record["id"],
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [min_lon, min_lat],
                            [max_lon, min_lat],
                            [max_lon, max_lat],
                            [min_lon, max_lat],
                            [min_lon, min_lat],
                        ]
                    ],
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def build_map(
    *,
    default_view_bbox: tuple[
        float,
        float,
        float,
        float,
    ],
    draft_bbox: tuple[
        float,
        float,
        float,
        float,
    ] | None,
    ready_bbox: tuple[
        float,
        float,
        float,
        float,
    ] | None,
    master_quad_geojson: dict[str, Any] | None,
    selected_features: list[
        dict[str, Any]
    ],
    allow_drawing: bool,
) -> folium.Map:
    focus_bbox = (
        ready_bbox
        or draft_bbox
        or default_view_bbox
    )

    center = [
        (
            focus_bbox[1]
            + focus_bbox[3]
        ) / 2.0,
        (
            focus_bbox[0]
            + focus_bbox[2]
        ) / 2.0,
    ]

    map_object = folium.Map(
        location=center,
        zoom_start=5,
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
        width="100%",
        height="100%",
    )

    add_base_layers(
        map_object
    )

    # Layer master bersifat opsional. Saat toggle OFF, GeoJSON ini bahkan
    # tidak dibentuk atau dikirim ke browser.
    if master_quad_geojson:
        folium.GeoJson(
            master_quad_geojson,
            name="Grid Quad Indonesia",
            show=True,
            smooth_factor=1.0,
            style_function=lambda _: {
                "color": "#F4A261",
                "weight": 0.9,
                "opacity": 0.85,
                "fillColor": "#F4A261",
                "fillOpacity": 0.015,
            },
            highlight_function=lambda _: {
                "color": "#FFB703",
                "weight": 2.2,
                "opacity": 1.0,
                "fillOpacity": 0.08,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["id"],
                aliases=["Quad ID"],
                sticky=False,
            ),
        ).add_to(map_object)

    rectangle_bbox = (
        ready_bbox
        or draft_bbox
    )

    if rectangle_bbox:
        is_ready = (
            ready_bbox is not None
        )

        # Rectangle hanya menjadi outline area. Dibuat non-interaktif agar
        # tidak menutupi hover/cursor milik polygon Quad di bawah/atasnya.
        folium.Rectangle(
            bounds=[
                [
                    rectangle_bbox[1],
                    rectangle_bbox[0],
                ],
                [
                    rectangle_bbox[3],
                    rectangle_bbox[2],
                ],
            ],
            color=(
                "#FFFFFF"
                if is_ready
                else "#00D4E8"
            ),
            weight=3,
            fill=not is_ready,
            fill_color="#00D4E8",
            fill_opacity=(
                0.10
                if not is_ready
                else 0.0
            ),
            interactive=False,
            bubbling_mouse_events=False,
        ).add_to(map_object)

    # Quad hasil Preview diletakkan setelah rectangle agar tooltip Quad ID
    # selalu menjadi target utama ketika cursor berada di area unduhan.
    if selected_features:
        folium.GeoJson(
            {
                "type": (
                    "FeatureCollection"
                ),
                "features": (
                    selected_features
                ),
            },
            name="Quad terpilih",
            show=True,
            smooth_factor=1.0,
            style_function=lambda _: {
                "color": "#00D4E8",
                "weight": 2.0,
                "opacity": 1.0,
                "fillColor": "#00D4E8",
                "fillOpacity": 0.22,
            },
            highlight_function=lambda _: {
                "color": "#FFFFFF",
                "weight": 3.0,
                "fillColor": "#00D4E8",
                "fillOpacity": 0.36,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["id"],
                aliases=["Quad ID"],
                sticky=False,
            ),
        ).add_to(map_object)

    if allow_drawing:
        # Tool ini hanya menerima satu input rectangle pada satu waktu.
        # Setiap input baru langsung menggantikan area lama melalui
        # replace_active_rectangle(), lalu komponen peta dibangun ulang.
        # Penghapusan manual dilakukan melalui tombol "Hapus Rectangle".
        Draw(
            export=False,
            position="topleft",
            draw_options={
                "polyline": False,
                "polygon": False,
                "circle": False,
                "marker": False,
                "circlemarker": False,
                "rectangle": {
                    "shapeOptions": {
                        "color": "#00D4E8",
                        "weight": 3,
                        "fillColor": "#AEB7BF",
                        "fillOpacity": 0.30,
                    }
                },
            },
            edit_options={
                "edit": False,
                "remove": False,
            },
        ).add_to(map_object)

    Fullscreen(
        position="topright",
        title="Layar penuh",
        title_cancel=(
            "Keluar layar penuh"
        ),
    ).add_to(map_object)

    MousePosition(
        position="bottomleft",
        separator=" | ",
        prefix="Koordinat:",
        num_digits=6,
    ).add_to(map_object)

    folium.LayerControl(
        position="topright",
        collapsed=True,
    ).add_to(map_object)

    map_object.fit_bounds(
        [
            [
                focus_bbox[1],
                focus_bbox[0],
            ],
            [
                focus_bbox[3],
                focus_bbox[2],
            ],
        ],
        padding=(10, 10),
    )

    return map_object


# ---------------------------------------------------------------------------
# DOWNLOADER BACKGROUND / PAUSE / RESUME
# ---------------------------------------------------------------------------

ACTIVE_DOWNLOAD_STATUSES = {
    "initializing",
    "running",
    "pausing",
}
PAUSED_DOWNLOAD_STATUSES = {
    "paused",
    "paused_network",
}
TERMINAL_DOWNLOAD_STATUSES = {
    "completed",
    "completed_with_failures",
    "failed",
    "cancelled",
}


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def atomic_write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def retire_terminal_active_run(
    active_run_file: Path,
    last_run_file: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Migrasi otomatis active_run terminal ke last_run."""
    active = read_json_file(active_run_file)
    last = read_json_file(last_run_file)
    status = str(active.get("status", "")).strip()

    if active and status in TERMINAL_DOWNLOAD_STATUSES:
        active["run_id"] = ensure_run_id(active)
        atomic_write_json_file(last_run_file, active)
        active_run_file.unlink(missing_ok=True)
        last = active
        active = {}

    return active, last


def activate_historical_run(
    run_payload: dict[str, Any],
    active_run_file: Path,
    *,
    status: str = "paused",
) -> dict[str, Any]:
    """Menjadikan run historis aktif lagi untuk resume/retry."""
    payload = dict(run_payload)
    payload["run_id"] = ensure_run_id(payload)
    payload["status"] = status
    payload["heartbeat_at"] = datetime.now().isoformat(timespec="seconds")
    payload["error"] = ""
    atomic_write_json_file(active_run_file, payload)
    return payload


def tail_text_file(path: Path, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max(1, max_lines):]


def launch_downloader_process(
    mode: str = "new",
    *,
    run_id: str = "",
    selection_signature: str = "",
) -> int:
    if not DOWNLOADER_PATH.exists():
        raise FileNotFoundError(
            f"Downloader tidak ditemukan: {DOWNLOADER_PATH}"
        )

    command = [sys.executable, "-u", str(DOWNLOADER_PATH)]
    if mode == "resume":
        command.append("--resume")
    elif mode == "retry-failed":
        command.append("--retry-failed")
    elif mode != "new":
        raise ValueError(f"Mode downloader tidak dikenal: {mode}")

    process_env = os.environ.copy()
    if run_id:
        process_env["REQUESTED_RUN_ID"] = run_id
    if selection_signature:
        process_env["REQUESTED_SELECTION_SIGNATURE"] = selection_signature

    LAUNCHER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    launcher_log = LAUNCHER_LOG_PATH.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": str(BASE_DIR),
        "stdout": launcher_log,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
        "env": process_env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    launcher_log.close()
    st.session_state.downloader_pid = process.pid
    st.session_state.download_launching = True
    return process.pid

def request_download_pause(pause_flag: Path, active_run_file: Path) -> None:
    atomic_write_json_file(
        pause_flag,
        {
            "reason": "manual",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    payload = read_json_file(active_run_file)
    if payload:
        payload["status"] = "pausing"
        atomic_write_json_file(active_run_file, payload)


def request_download_cancel(cancel_flag: Path) -> None:
    atomic_write_json_file(
        cancel_flag,
        {
            "reason": "manual",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def format_bytes(value: int | float) -> str:
    size = float(value or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:,.2f} {unit}"
        size /= 1024
    return f"{size:,.2f} TB"


def format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "—"
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def load_failed_items(active_run: dict[str, Any]) -> list[dict[str, Any]]:
    path_text = str(active_run.get("failed_json", "")).strip()
    if not path_text:
        return []
    payload = read_json_file(Path(path_text))
    items = payload.get("items", [])
    return items if isinstance(items, list) else []


def load_runtime_quad_details(
    active_run: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    """
    Membaca Quad aktif dan hasil terakhir langsung dari SQLite.

    Pembacaan langsung membuat daftar ID lebih aktual daripada menunggu
    heartbeat active_run.json. Payload JSON tetap dipakai sebagai fallback.
    """
    fallback_active = active_run.get(
        "active_quad_items",
        [],
    )
    if not isinstance(
        fallback_active,
        list,
    ):
        fallback_active = []

    fallback_last = active_run.get(
        "last_result",
        {},
    )
    if not isinstance(
        fallback_last,
        dict,
    ):
        fallback_last = {}

    database_text = str(
        active_run.get(
            "state_database",
            "",
        )
    ).strip()
    if not database_text:
        return fallback_active, fallback_last

    database_path = Path(
        database_text
    )
    if not database_path.exists():
        return fallback_active, fallback_last

    try:
        connection = sqlite3.connect(
            database_path,
            timeout=2.0,
        )
        connection.row_factory = sqlite3.Row

        active_rows = connection.execute(
            """
            SELECT quad_id, status, attempts, started_at, updated_at
            FROM quads
            WHERE status='downloading'
            ORDER BY started_at, quad_id
            """
        ).fetchall()

        recent_row = connection.execute(
            """
            SELECT *
            FROM quads
            WHERE status IN (
                'downloaded',
                'reused',
                'existing',
                'failed',
                'cancelled'
            )
            ORDER BY COALESCE(completed_at, updated_at) DESC, quad_id
            LIMIT 1
            """
        ).fetchone()

        connection.close()

        active_items = [
            dict(row)
            for row in active_rows
        ]
        last_result = (
            dict(recent_row)
            if recent_row is not None
            else fallback_last
        )
        return active_items, last_result

    except (
        OSError,
        sqlite3.Error,
    ):
        return fallback_active, fallback_last


def render_download_runtime_panel(
    active_run: dict[str, Any],
    *,
    active_run_file: Path,
    pause_flag: Path,
    cancel_flag: Path,
    ui_log_max_lines: int,
) -> None:
    if not active_run:
        return

    status = str(active_run.get("status", "")).strip()
    counts = active_run.get("counts", {}) if isinstance(active_run.get("counts"), dict) else {}
    total = int(counts.get("total", active_run.get("quad_count", 0)) or 0)
    completed = int(counts.get("completed", 0) or 0)
    processed = int(counts.get("processed", completed + int(counts.get("failed", 0) or 0)) or 0)
    failed = int(counts.get("failed", 0) or 0)
    pending = int(counts.get("pending", 0) or 0)
    downloading = int(counts.get("downloading", 0) or 0)

    active_items, last_result = (
        load_runtime_quad_details(
            active_run
        )
    )
    if active_items:
        downloading = len(
            active_items
        )

    status_labels = {
        "initializing": "Menyiapkan sesi download",
        "running": "Download sedang berjalan",
        "pausing": "Menunggu file aktif selesai sebelum pause",
        "paused": "Download dijeda",
        "paused_network": "Download dijeda karena koneksi terputus",
        "completed": "Download selesai",
        "completed_with_failures": "Download selesai dengan beberapa kegagalan",
        "failed": "Downloader berhenti karena error",
        "cancelled": "Sesi dibatalkan; file yang sudah selesai tetap disimpan",
    }

    if status in {"completed"}:
        st.success(status_labels.get(status, status))
    elif status in {"completed_with_failures", "failed"}:
        st.error(status_labels.get(status, status))
    elif status in PAUSED_DOWNLOAD_STATUSES:
        st.warning(status_labels.get(status, status))
    else:
        st.info(status_labels.get(status, status or "Status belum tersedia"))

    if total > 0:
        st.progress(min(1.0, processed / total), text=f"{processed:,} / {total:,} Quad diproses")

    metric_columns = st.columns(6)
    metric_columns[0].metric("Selesai", f"{completed:,}")
    metric_columns[1].metric("Pending", f"{pending:,}")
    metric_columns[2].metric("Aktif", f"{downloading:,}")
    metric_columns[3].metric("Gagal", f"{failed:,}")
    metric_columns[4].metric("Data", format_bytes(active_run.get("total_bytes", 0)))
    metric_columns[5].metric("ETA", format_duration(active_run.get("eta_seconds")))

    st.caption(
        f"Batch {int(active_run.get('batch_index', 0) or 0):,} / "
        f"{int(active_run.get('total_batches', 0) or 0):,} · "
        f"Kecepatan rata-rata {format_bytes(active_run.get('speed_bps', 0))}/detik"
    )

    if active_items:
        active_ids = [
            str(
                item.get(
                    "quad_id",
                    "",
                )
            )
            for item in active_items
            if item.get(
                "quad_id"
            )
        ]
        with st.expander(
            f"Quad sedang diproses ({len(active_ids):,})",
            expanded=True,
        ):
            st.code(
                "\n".join(
                    active_ids
                ),
                language="text",
            )

    if last_result:
        last_quad_id = str(
            last_result.get(
                "quad_id",
                "",
            )
        )
        last_status = str(
            last_result.get(
                "status",
                "",
            )
        )
        last_attempts = int(
            last_result.get(
                "attempts",
                0,
            )
            or 0
        )
        last_size = format_bytes(
            last_result.get(
                "file_size",
                0,
            )
        )
        last_duration = float(
            last_result.get(
                "duration_seconds",
                0,
            )
            or 0
        )

        if (
            last_quad_id
            and last_status == "failed"
        ):
            st.error(
                "Terakhir gagal: "
                f"{last_quad_id} — "
                f"{last_result.get('error_type') or 'DownloadError'} — "
                f"{last_result.get('error_message') or 'Download gagal'} — "
                f"{last_attempts} percobaan"
            )
        elif last_quad_id:
            st.caption(
                "Terakhir selesai: "
                f"**{last_quad_id}** — "
                f"{last_status} — "
                f"{last_size} — "
                f"{last_duration:.2f} detik — "
                f"{last_attempts} percobaan"
            )

    action_columns = st.columns(4)
    if status in ACTIVE_DOWNLOAD_STATUSES:
        if action_columns[0].button("⏸ Pause Download", width="stretch", key="pause_active_download"):
            request_download_pause(pause_flag, active_run_file)
            st.rerun(scope="fragment")
    elif status in PAUSED_DOWNLOAD_STATUSES:
        if action_columns[0].button("▶ Lanjutkan Download", type="primary", width="stretch", key="resume_active_download"):
            pause_flag.unlink(missing_ok=True)
            cancel_flag.unlink(missing_ok=True)
            launch_downloader_process("resume")
            st.rerun(scope="fragment")

    if status == "completed_with_failures" and failed > 0:
        if action_columns[1].button("🔁 Retry Quad Gagal", type="primary", width="stretch", key="retry_failed_download"):
            pause_flag.unlink(missing_ok=True)
            cancel_flag.unlink(missing_ok=True)
            launch_downloader_process("retry-failed")
            st.rerun(scope="fragment")

    if status in ACTIVE_DOWNLOAD_STATUSES | PAUSED_DOWNLOAD_STATUSES:
        if action_columns[2].button("Batalkan Sesi", width="stretch", key="cancel_active_download"):
            request_download_cancel(cancel_flag)
            st.rerun(scope="fragment")

    output_text = str(active_run.get("output_folder", "")).strip()
    if output_text and action_columns[3].button("Buka Folder Sesi", width="stretch", key="open_active_run_folder"):
        output_folder = Path(output_text)
        if output_folder.exists() and os.name == "nt":
            os.startfile(output_folder)  # type: ignore[attr-defined]
        else:
            st.info(f"Folder sesi: {output_folder}")

    failed_items = load_failed_items(active_run)
    if failed_items:
        with st.expander(f"Quad gagal diunduh ({len(failed_items):,})", expanded=status == "completed_with_failures"):
            st.dataframe(
                [
                    {
                        "Quad ID": item.get("quad_id", ""),
                        "Percobaan": item.get("attempts", 0),
                        "Jenis error": item.get("error_type", ""),
                        "Pesan": item.get("error_message", ""),
                        "HTTP": item.get("http_status", ""),
                    }
                    for item in failed_items
                ],
                width="stretch",
                hide_index=True,
            )

    log_path_text = str(active_run.get("run_log_path", "")).strip()
    if log_path_text:
        log_lines = tail_text_file(Path(log_path_text), ui_log_max_lines)
        if log_lines:
            with st.expander("Log proses terbaru", expanded=status in {"failed", "completed_with_failures"}):
                st.code("\n".join(log_lines), language="text")


def resolve_run_log_path(
    run_info: dict[str, Any],
) -> Path | None:
    """
    Menentukan lokasi log sebuah run.

    Prioritas:
    1. run_log_path dari payload;
    2. <output_folder>/run.log sebagai fallback untuk run lama.
    """
    explicit_text = str(
        run_info.get(
            "run_log_path",
            "",
        )
    ).strip()

    candidates: list[Path] = []

    if explicit_text:
        candidates.append(
            Path(explicit_text)
        )

    output_text = str(
        run_info.get(
            "output_folder",
            "",
        )
    ).strip()

    if output_text:
        fallback_path = (
            Path(output_text)
            / "run.log"
        )
        if fallback_path not in candidates:
            candidates.append(
                fallback_path
            )

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return (
        candidates[0]
        if candidates
        else None
    )


def render_last_run_panel(
    last_run: dict[str, Any],
    *,
    active_run: dict[str, Any],
    active_run_file: Path,
    pause_flag: Path,
    cancel_flag: Path,
    ui_log_max_lines: int,
) -> None:
    if not last_run:
        return

    status = str(last_run.get("status", "")).strip()
    counts = (
        last_run.get("counts", {})
        if isinstance(last_run.get("counts"), dict)
        else {}
    )
    failed = int(counts.get("failed", 0) or 0)
    pending = int(counts.get("pending", 0) or 0)
    run_log_path = resolve_run_log_path(
        last_run
    )
    run_log_exists = bool(
        run_log_path
        and run_log_path.exists()
    )

    st.subheader("Hasil Run Terakhir")
    st.code(
        "\n".join(
            [
                f"Run ID   : {ensure_run_id(last_run)}",
                f"Run name : {last_run.get('run_name', '')}",
                f"Status   : {status}",
                f"Mosaic   : {last_run.get('mosaic_name', '')}",
                f"Quad     : {last_run.get('quad_count', counts.get('total', 0))}",
                f"TIFF     : {last_run.get('output_folder', '')}",
                f"Excel    : {last_run.get('excel_path', '')}",
            ]
        ),
        language="text",
    )

    columns = st.columns(4)
    output_text = str(
        last_run.get(
            "output_folder",
            "",
        )
    ).strip()

    if columns[0].button(
        "Buka Folder Run Terakhir",
        width="stretch",
        key="open_last_run_folder",
        disabled=not output_text,
    ):
        output_path = Path(
            output_text
        )
        if (
            output_path.exists()
            and os.name == "nt"
        ):
            os.startfile(  # type: ignore[attr-defined]
                output_path
            )
        else:
            st.info(
                f"Folder run terakhir: "
                f"{output_path}"
            )

    if columns[1].button(
        "Buka File Log",
        width="stretch",
        key="open_last_run_log",
        disabled=not run_log_exists,
    ):
        if (
            run_log_path is not None
            and run_log_path.exists()
            and os.name == "nt"
        ):
            os.startfile(  # type: ignore[attr-defined]
                run_log_path
            )
        elif run_log_path is not None:
            st.info(
                f"File log: "
                f"{run_log_path}"
            )

    can_reactivate = not active_run

    if failed > 0 and columns[2].button(
        "🔁 Retry Quad Gagal",
        type="primary",
        width="stretch",
        key="retry_last_run_failed",
        disabled=not can_reactivate,
    ):
        payload = activate_historical_run(
            last_run,
            active_run_file,
            status="paused",
        )
        pause_flag.unlink(
            missing_ok=True
        )
        cancel_flag.unlink(
            missing_ok=True
        )
        st.session_state.attached_run_id = (
            ensure_run_id(
                payload
            )
        )
        st.session_state.attached_selection_signature = str(
            payload.get(
                "selection_signature",
                "",
            )
        )
        launch_downloader_process(
            "retry-failed"
        )
        st.rerun(
            scope="fragment"
        )

    if (
        status in {
            "failed",
            "cancelled",
        }
        and pending > 0
        and columns[3].button(
            "▶ Lanjutkan Sesi Terakhir",
            width="stretch",
            key="resume_last_run",
            disabled=not can_reactivate,
        )
    ):
        payload = activate_historical_run(
            last_run,
            active_run_file,
            status="paused",
        )
        pause_flag.unlink(
            missing_ok=True
        )
        cancel_flag.unlink(
            missing_ok=True
        )
        st.session_state.attached_run_id = (
            ensure_run_id(
                payload
            )
        )
        st.session_state.attached_selection_signature = str(
            payload.get(
                "selection_signature",
                "",
            )
        )
        launch_downloader_process(
            "resume"
        )
        st.rerun(
            scope="fragment"
        )

    failed_items = load_failed_items(last_run)
    if failed_items:
        with st.expander(
            f"Quad gagal pada run terakhir ({len(failed_items):,})",
            expanded=False,
        ):
            st.dataframe(
                [
                    {
                        "Quad ID": item.get("quad_id", ""),
                        "Percobaan": item.get("attempts", 0),
                        "Jenis error": item.get("error_type", ""),
                        "Pesan": item.get("error_message", ""),
                        "HTTP": item.get("http_status", ""),
                    }
                    for item in failed_items
                ],
                width="stretch",
                hide_index=True,
            )
    if run_log_path is not None:
        log_lines = tail_text_file(
            run_log_path,
            ui_log_max_lines,
        )

        with st.expander(
            "Log Run Terakhir",
            expanded=True,
        ):
            if log_lines:
                st.code(
                    "\n".join(
                        log_lines
                    ),
                    language="text",
                )
                st.caption(
                    "Menampilkan "
                    f"{len(log_lines):,} baris terakhir dari "
                    f"`{run_log_path}`. "
                    "File run.log tetap menyimpan log lengkap."
                )
            else:
                st.info(
                    "File log belum tersedia atau tidak dapat dibaca."
                )
                st.caption(
                    f"Lokasi yang diperiksa: `{run_log_path}`"
                )




# ---------------------------------------------------------------------------
# BRANDING UI
# ---------------------------------------------------------------------------

def render_global_widget_css() -> None:
    """
    Menyembunyikan instruksi keyboard bawaan Streamlit pada text input.

    Streamlit dapat tetap menampilkan teks "Press Enter to apply" pada
    st.text_input walaupun widget tidak berada dalam st.form.
    """
    st.markdown(
        """
        <style>
            /* Selector utama Streamlit InputInstructions. */
            div[data-testid="InputInstructions"] {
                display: none !important;
                visibility: hidden !important;
                width: 0 !important;
                height: 0 !important;
                min-width: 0 !important;
                min-height: 0 !important;
                max-width: 0 !important;
                max-height: 0 !important;
                margin: 0 !important;
                padding: 0 !important;
                overflow: hidden !important;
                pointer-events: none !important;
            }

            /* Scoped fallback untuk text input. */
            div[data-testid="stTextInput"]
            div[data-testid="InputInstructions"] {
                display: none !important;
                visibility: hidden !important;
            }

            /* Fallback jika nama class DOM memuat InputInstructions. */
            div[data-testid="stTextInput"]
            div[class*="InputInstructions"] {
                display: none !important;
                visibility: hidden !important;
                width: 0 !important;
                height: 0 !important;
                min-height: 0 !important;
                margin: 0 !important;
                padding: 0 !important;
                overflow: hidden !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_app_header() -> None:
    """
    Menampilkan judul aplikasi dan identitas instansi dalam dua tingkat.

    Header diposisikan rata kiri menggunakan properti flex yang valid.
    """
    if LOGO_KEMENHUT_PATH.exists():
        logo_bytes = (
            LOGO_KEMENHUT_PATH.read_bytes()
        )
        logo_base64 = base64.b64encode(
            logo_bytes
        ).decode("ascii")
        logo_html = (
            f'<img '
            f'src="data:image/png;base64,{logo_base64}" '
            f'alt="Logo Kementerian Kehutanan" '
            f'class="app-agency-logo">'
        )
    else:
        logo_html = (
            '<span class="app-logo-fallback" '
            'title="Logo Kementerian Kehutanan belum ditemukan">'
            '🌲'
            '</span>'
        )

    st.html(
        f"""
        <style>
            .app-branding {{
                width: 100%;
                margin: 0.35rem 0 0.75rem 0;
                text-align: left;
            }}

            .app-main-title {{
                display: flex;
                align-items: center;
                justify-content: flex-start;
                gap: 0.55rem;
                margin: 0;
                padding: 0;
                line-height: 1.16;
                font-size: clamp(1.15rem, 2.1vw, 2.15rem);
                font-weight: 800;
                letter-spacing: -0.025em;
                color: inherit;
                text-align: left;
            }}

            .app-main-title-text {{
                min-width: 0;
            }}

            .app-title-icon {{
                flex: 0 0 auto;
                font-size: 0.88em;
                line-height: 1;
            }}

            .app-agency-row {{
                display: flex;
                align-items: center;
                justify-content: flex-start;
                gap: 0.48rem;
                margin-top: 0.48rem;
                line-height: 1.15;
                font-size: clamp(0.95rem, 1.45vw, 1.18rem);
                font-weight: 600;
                color: rgba(250, 250, 250, 0.82);
                text-align: left;
            }}

            .app-agency-logo {{
                display: block;
                width: 20px;
                height: 20px;
                object-fit: contain;
                flex: 0 0 20px;
            }}

            .app-logo-fallback {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 20px;
                height: 20px;
                flex: 0 0 20px;
                font-size: 1.05rem;
                line-height: 1;
            }}

            .app-agency-separator {{
                opacity: 0.55;
                padding: 0 0.08rem;
            }}

            @media (max-width: 760px) {{
                .app-branding {{
                    margin-top: 0.15rem;
                }}

                .app-main-title {{
                    align-items: flex-start;
                    gap: 0.35rem;
                    font-size: clamp(1.22rem, 5.6vw, 1.80rem);
                }}

                .app-agency-row {{
                    align-items: center;
                    justify-content: flex-start;
                    gap: 0.32rem;
                    flex-wrap: wrap;
                    padding: 0;
                    font-size: clamp(0.85rem, 3.7vw, 1rem);
                }}

                .app-agency-logo {{
                    width: 20px;
                    height: 20px;
                    flex-basis: 20px;
                }}

                .app-logo-fallback {{
                    width: 20px;
                    height: 20px;
                    flex-basis: 20px;
                }}
            }}
        </style>

        <div class="app-branding">
            <h1 class="app-main-title">
                <span class="app-title-icon" aria-hidden="true">🛰️</span>
                <span class="app-main-title-text">
                    Planet Global Quarterly — Interactive Quad Selector
                    &amp; Downloader
                </span>
                <span class="app-title-icon" aria-hidden="true">🛰️</span>
            </h1>

            <div class="app-agency-row">
                {logo_html}
                <span>Kementerian Kehutanan</span>
                <span class="app-agency-separator">·</span>
                <span>Direktorat IPSDH</span>
                <span aria-hidden="true">🌲🌳</span>
            </div>
        </div>
        """
    )


def render_app_footer() -> None:
    """Menampilkan copyright dan tautan profil GitHub."""
    current_year = datetime.now().year

    st.markdown(
        f"""
        <div
            style="
                margin-top: 3rem;
                padding: 1.15rem 0 0.75rem 0;
                border-top: 1px solid rgba(128, 128, 128, 0.28);
                text-align: center;
                font-size: 0.88rem;
                color: rgba(250, 250, 250, 0.64);
            "
        >
            © {current_year}
            <a
                href="{GITHUB_PROFILE_URL}"
                target="_blank"
                rel="noopener noreferrer"
                style="
                    color: #00D4E8;
                    text-decoration: none;
                    font-weight: 600;
                "
            >
                {GITHUB_USERNAME}
            </a>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# STREAMLIT APP
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title=(
        "Planet Global Quarterly — Interactive Quad Selector & Downloader"
    ),
    page_icon="🛰️",
    layout="wide",
)

render_global_widget_css()
render_app_header()

st.caption(
    "Default menampilkan seluruh Indonesia "
    "tanpa rectangle. Gambar area, lakukan "
    "preview, lalu simpan atau download "
    "seluruh Quad yang beririsan."
)

env = load_env()

# Default false: tidak membuat env_*.backup maupun
# selected_quads_*.json di config_backups.
backup_config_files = env_bool(
    env,
    "BACKUP_CONFIG_FILES",
    False,
)

initialize_state(env)

default_view_bbox = parse_bbox_text(
    env.get(
        "DEFAULT_VIEW_BBOX",
        "",
    ),
    DEFAULT_INDONESIA_BBOX,
)

master_source = resolve_project_path(
    env.get(
        "MASTER_QUADS_SOURCE",
        "",
    ),
    "data/quads.shp",
)

master_id_field = (
    env.get(
        "MASTER_QUAD_ID_FIELD",
        "id",
    )
    or "id"
)

selected_file = resolve_project_path(
    env.get(
        "SELECTED_QUADS_FILE",
        "",
    ),
    "config/selected_quads.json",
)

output_dir = resolve_project_path(
    env.get(
        "OUTPUT_DIR",
        "",
    ),
    "downloads",
)

last_run_file = resolve_project_path(
    env.get(
        "LAST_RUN_FILE",
        "",
    ),
    "config/last_run.json",
)

active_run_file = resolve_project_path(
    env.get("ACTIVE_RUN_FILE", ""),
    "config/active_run.json",
)
pause_flag_file = resolve_project_path(
    env.get("PAUSE_FLAG_FILE", ""),
    "config/download.pause",
)
cancel_flag_file = resolve_project_path(
    env.get("CANCEL_FLAG_FILE", ""),
    "config/download.cancel",
)
ui_log_max_lines = max(20, int(env.get("UI_LOG_MAX_LINES", "100") or "100"))

try:
    source_mtime = (
        master_source.stat().st_mtime
    )

    master_data = read_master_index(
        str(master_source),
        master_id_field,
        source_mtime,
    )

except Exception as exc:
    st.error(str(exc))
    st.stop()

master_records = master_data[
    "records"
]
all_master_ids = master_data[
    "all_ids"
]


# ---------------------------------------------------------------------------
# SIDEBAR: konfigurasi tidak memicu proses berat
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Konfigurasi")

    # Container biasa mempertahankan layout konfigurasi.
    # Hint "Press Enter to apply" disembunyikan oleh
    # render_global_widget_css(). Validasi Planet API tetap hanya
    # berjalan setelah tombol Terapkan pilihan ditekan.
    with st.container(
        border=True
    ):
        mosaic_input = st.text_input(
            "Mosaic",
            value=(
                st.session_state
                .app_mosaic
            ),
            key="configuration_mosaic_input",
            help=(
                "Isi persis nama mosaic yang tersedia "
                "pada Planet Insights/API."
            ),
        )

        mode_options = [
            "Area tertentu",
            "Seluruh Indonesia",
        ]

        current_mode_index = (
            1
            if (
                st.session_state
                .app_mode
                == "indonesia"
            )
            else 0
        )

        mode_input = st.radio(
            "Mode pemilihan area",
            options=mode_options,
            index=current_mode_index,
            key="configuration_mode_input",
        )

        apply_config = st.button(
            "Terapkan pilihan",
            width="stretch",
            key="apply_configuration_button",
        )

    if apply_config:
        new_mode = (
            "indonesia"
            if mode_input
            == "Seluruh Indonesia"
            else "custom"
        )

        candidate_mosaic = (
            mosaic_input.strip()
        )

        try:
            with st.spinner(
                "Memvalidasi mosaic ke Planet API..."
            ):
                mosaic_info = validate_planet_mosaic(
                    candidate_mosaic,
                    env.get(
                        "PL_API_KEY",
                        "",
                    ),
                )

        except Exception as exc:
            st.session_state.mosaic_validated = False
            st.session_state.mosaic_validation_notice = str(exc)
            st.error(str(exc))

        else:
            changed = (
                new_mode
                != st.session_state.app_mode
                or candidate_mosaic
                != st.session_state.app_mosaic
            )

            if changed:
                clear_area_state()

            st.session_state.app_mode = (
                new_mode
            )
            st.session_state.app_mosaic = (
                candidate_mosaic
            )
            st.session_state.mosaic_validated = True
            st.session_state.mosaic_period_label = (
                mosaic_info[
                    "period_label"
                ]
            )
            st.session_state.mosaic_validation_notice = (
                "Mosaic ditemukan dan dapat diakses: "
                f"{candidate_mosaic}"
            )

            st.rerun()

    detected_period = mosaic_period_label(
        st.session_state.app_mosaic
    )

    if st.session_state.mosaic_validated:
        st.success(
            st.session_state.mosaic_validation_notice
        )
    elif st.session_state.mosaic_validation_notice:
        st.error(
            st.session_state.mosaic_validation_notice
        )
    else:
        st.caption(
            "Tekan **Terapkan pilihan** untuk "
            "memvalidasi mosaic ke Planet API."
        )

    st.write(
        f"**Periode terdeteksi:** "
        f"{detected_period}"
    )

    st.divider()

    st.write(
        f"**Master Quad:** "
        f"{len(all_master_ids):,}"
    )

    st.write(
        f"**Sumber:** "
        f"`{path_for_env(master_source)}`"
    )

    st.write(
        f"**CRS terbaca:** "
        f"`{master_data['source_crs']}`"
    )

    st.divider()
    st.subheader("Tampilan Peta")

    st.toggle(
        "Tampilkan Grid Quad Indonesia",
        key="show_master_quad_layer",
        on_change=refresh_map_component,
        help=(
            "Default tidak aktif agar peta cepat dibuka. "
            "Aktifkan untuk menampilkan seluruh grid Quad "
            "dan melihat Quad ID melalui cursor."
        ),
    )

    if st.session_state.show_master_quad_layer:
        st.caption(
            "Grid master aktif. Garis oranye menunjukkan seluruh "
            "Quad Indonesia."
        )
    else:
        st.caption(
            "Grid master tidak dimuat. Quad hasil Preview tetap "
            "ditampilkan dengan garis cyan."
        )



# ---------------------------------------------------------------------------
# FRAGMENT: hanya workspace peta yang rerun
# ---------------------------------------------------------------------------

@st.fragment(run_every=1.0)
def render_area_workspace() -> None:
    active_run, last_run = retire_terminal_active_run(
        active_run_file,
        last_run_file,
    )
    active_status = str(active_run.get("status", "")).strip()
    active_run_id = ensure_run_id(active_run) if active_run else ""
    active_signature = str(
        active_run.get("selection_signature", "")
    ).strip()

    # Browser baru otomatis tersambung ke proses aktif. Setelah Reset,
    # detached_run_id mencegah proses yang sama menempel lagi ke workflow area.
    if (
        active_run_id
        and not st.session_state.attached_run_id
        and st.session_state.detached_run_id != active_run_id
    ):
        st.session_state.attached_run_id = active_run_id
        st.session_state.attached_selection_signature = active_signature

    run_blocks_new_download = bool(
        active_run
        and active_status
        in (ACTIVE_DOWNLOAD_STATUSES | PAUSED_DOWNLOAD_STATUSES)
    )

    mode = st.session_state.app_mode
    mosaic_name = (
        st.session_state.app_mosaic
    )
    status = (
        st.session_state.area_status
    )

    is_indonesia = (
        mode == "indonesia"
    )

    selected_ids = list(
        st.session_state.selected_ids
    )
    current_signature = str(
        st.session_state.selection_signature
    ).strip()
    attached_run_id = str(
        st.session_state.attached_run_id
    ).strip()

    active_matches_selection = bool(
        active_run
        and active_run_id == attached_run_id
        and (
            not current_signature
            or active_signature == current_signature
        )
    )

    last_run_id = ensure_run_id(last_run) if last_run else ""
    last_signature = str(
        last_run.get("selection_signature", "")
    ).strip()
    last_matches_selection = bool(
        last_run
        and last_run_id == attached_run_id
        and current_signature
        and last_signature == current_signature
    )

    if active_matches_selection:
        st.session_state.workflow_step = STEP_DOWNLOAD
    elif last_matches_selection:
        last_status = str(last_run.get("status", "")).strip()
        if last_status == "completed":
            st.session_state.workflow_step = STEP_DONE
            st.session_state.notice = (
                "Download selesai. TIFF dan workbook Excel sudah diproses."
            )
        elif last_status in {"completed_with_failures", "failed"}:
            st.session_state.workflow_step = STEP_ERROR

        st.session_state.last_run_name = str(last_run.get("run_name", ""))
        st.session_state.last_run_folder = str(last_run.get("output_folder", ""))
        st.session_state.last_run_excel = str(last_run.get("excel_path", ""))

    master_quad_geojson: dict[str, Any] | None = None

    if st.session_state.show_master_quad_layer:
        with st.spinner(
            f"Memuat Grid Quad Indonesia "
            f"({len(all_master_ids):,} Quad)..."
        ):
            master_quad_geojson = (
                build_master_quad_geojson(
                    str(master_source),
                    master_id_field,
                    source_mtime,
                )
            )

    selected_features: list[
        dict[str, Any]
    ] = []

    if (
        status == STATUS_READY
        and not is_indonesia
        and selected_ids
    ):
        selected_features = (
            load_selected_geometries(
                str(master_source),
                master_id_field,
                tuple(
                    sorted(selected_ids)
                ),
                source_mtime,
            )
        )

    map_object = build_map(
        default_view_bbox=(
            default_view_bbox
        ),
        draft_bbox=(
            None
            if is_indonesia
            else st.session_state
            .draft_bbox
        ),
        ready_bbox=(
            None
            if is_indonesia
            else st.session_state
            .ready_bbox
        ),
        master_quad_geojson=(
            master_quad_geojson
        ),
        selected_features=(
            selected_features
        ),
        allow_drawing=(
            not is_indonesia
        ),
    )

    map_result = st_folium(
        map_object,
        width=1300,
        height=680,
        returned_objects=[
            "last_active_drawing",
        ],
        key=(
            "optimized_area_map_"
            f"{st.session_state.map_revision}"
        ),
    )

    if (
        not is_indonesia
        and isinstance(
            map_result,
            dict,
        )
    ):
        drawing = map_result.get(
            "last_active_drawing"
        )

        if drawing:
            newest_bbox = bbox_from_drawing(
                drawing
            )

            if (
                newest_bbox
                and replace_active_rectangle(
                    newest_bbox
                )
            ):
                # Rerun segera agar objek Draw lama hilang sebelum metric,
                # preview lama, atau polygon terpilih sempat ditampilkan.
                st.rerun(
                    scope="fragment"
                )

    # Tombol ini menjalankan fungsi yang sama dengan Reset:
    # membersihkan rectangle, BBOX aktif, preview, status download,
    # dan membuat ulang komponen peta tanpa objek lama.
    delete_rectangle_clicked = st.button(
        "🗑️ Hapus Rectangle",
        key=(
            "delete_rectangle_"
            f"{st.session_state.map_revision}"
        ),
        width="stretch",
        disabled=(
            is_indonesia
            or (
                st.session_state.draft_bbox
                is None
                and st.session_state.ready_bbox
                is None
            )
        ),
    )

    if delete_rectangle_clicked:
        clear_area_state(
            rebuild_map=True
        )
        st.rerun(
            scope="fragment"
        )

    if not is_indonesia:
        st.caption(
            "Gunakan **Hapus Rectangle** untuk membersihkan area "
            "dan memulai pilihan baru. Setelah Preview Area, arahkan "
            "cursor ke grid cyan untuk melihat Quad ID."
        )

    if st.session_state.show_master_quad_layer:
        st.caption(
            "Grid Quad Indonesia aktif. Arahkan cursor ke kotak "
            "oranye atau cyan untuk melihat Quad ID."
        )

    active_bbox = (
        st.session_state.ready_bbox
        if (
            st.session_state
            .area_status
            == STATUS_READY
        )
        else st.session_state
        .draft_bbox
    )

    selected_set = set(
        st.session_state.selected_ids
    )

    existing_ids, pending_ids = (
        local_tiff_status(
            selected_set,
            output_dir,
            mosaic_name,
        )
        if (
            st.session_state
            .area_status
            == STATUS_READY
        )
        else (set(), set())
    )

    metric_columns = st.columns(5)

    if (
        st.session_state.area_status
        == STATUS_EMPTY
    ):
        area_status_label = "Belum dipilih"
        selected_area_size_label = "—"
        quad_count = 0

    elif (
        st.session_state.area_status
        == STATUS_DRAFT
    ):
        # Rectangle sudah digambar, tetapi belum dikonfirmasi melalui
        # tombol Preview Area.
        area_status_label = "Belum dipilih"
        selected_area_size_label = (
            f"{area_km2(active_bbox):,.0f} km²"
            if active_bbox
            else "—"
        )
        quad_count = 0

    else:
        area_status_label = "Sudah dipilih"
        selected_area_size_label = (
            f"{area_km2(active_bbox):,.0f} km²"
            if active_bbox
            else "—"
        )
        quad_count = len(
            st.session_state.selected_ids
        )

    metric_columns[0].metric(
        "Status Area",
        area_status_label,
    )

    metric_columns[1].metric(
        "Luas Area Pilihan",
        selected_area_size_label,
    )

    metric_columns[2].metric(
        "Quad terpilih",
        f"{quad_count:,}",
    )

    metric_columns[3].metric(
        "Sudah tersedia",
        f"{len(existing_ids):,}",
    )

    metric_columns[4].metric(
        "Belum diunduh",
        f"{len(pending_ids):,}",
    )

    if (
        st.session_state.area_status
        == STATUS_EMPTY
    ):
        st.info(
            "Belum ada area aktif. "
            + (
                "Gambar rectangle pada peta."
                if not is_indonesia
                else (
                    "Klik **Gunakan Seluruh "
                    "Indonesia**."
                )
            )
        )

    elif (
        st.session_state.area_status
        == STATUS_DRAFT
    ):
        st.warning(
            st.session_state.notice
            or (
                "Area sudah digambar tetapi belum dipilih. "
                "Klik **Preview Area** untuk mengonfirmasi "
                "dan menghitung Quad."
            )
        )

    elif st.session_state.notice:
        st.success(
            st.session_state.notice
        )

    if active_bbox:
        with st.expander(
            "Detail Koordinat Area",
            expanded=False,
        ):
            min_lon, min_lat, max_lon, max_lat = active_bbox

            st.code(
                bbox_text(active_bbox),
                language="text",
            )

            st.caption(
                "Urutan koordinat: batas barat, batas selatan, "
                "batas timur, batas utara."
            )

            st.write(
                f"**Barat:** {min_lon:.8f}  "
                f"**Selatan:** {min_lat:.8f}  "
                f"**Timur:** {max_lon:.8f}  "
                f"**Utara:** {max_lat:.8f}"
            )

    button_columns = st.columns(
        [1, 1.2, 1.2, 1.5, 1.2]
    )

    reset_clicked = (
        button_columns[0].button(
            "Reset",
            width="stretch",
        )
    )

    preview_label = (
        "Gunakan Seluruh Indonesia"
        if is_indonesia
        else "Preview Area"
    )

    preview_enabled = (
        is_indonesia
        or (
            st.session_state.draft_bbox
            is not None
        )
    )

    ready = (
        st.session_state.area_status
        == STATUS_READY
        and bool(
            st.session_state.selected_ids
        )
    )

    workflow_step = (
        st.session_state.workflow_step
    )

    preview_type = (
        "primary"
        if workflow_step == STEP_PREVIEW
        else "secondary"
    )
    save_type = (
        "primary"
        if workflow_step == STEP_SAVE
        else "secondary"
    )
    download_type = (
        "primary"
        if workflow_step in {
            STEP_DOWNLOAD,
            STEP_ERROR,
        }
        else "secondary"
    )
    folder_type = (
        "primary"
        if workflow_step == STEP_DONE
        else "secondary"
    )

    preview_clicked = (
        button_columns[1].button(
            preview_label,
            type=preview_type,
            width="stretch",
            disabled=(
                not preview_enabled
            ),
        )
    )

    save_enabled = (
        ready
        and workflow_step == STEP_SAVE
    )

    download_enabled = (
        ready
        and workflow_step in {STEP_DOWNLOAD, STEP_ERROR}
        and not run_blocks_new_download
    )

    folder_enabled = bool(
        last_matches_selection
        and last_run.get("output_folder")
    )

    save_clicked = (
        button_columns[2].button(
            "Simpan Konfigurasi",
            type=save_type,
            width="stretch",
            disabled=not save_enabled,
        )
    )

    download_clicked = (
        button_columns[3].button(
            (
                "Coba Download Lagi"
                if workflow_step == STEP_ERROR
                else "Download Semua Quad"
            ),
            type=download_type,
            width="stretch",
            disabled=not download_enabled,
        )
    )

    open_folder_clicked = (
        button_columns[4].button(
            "Buka Folder Hasil",
            type=folder_type,
            width="stretch",
            disabled=not folder_enabled,
        )
    )

    step_labels = {
        STEP_PREVIEW: "1/3 — Preview area",
        STEP_SAVE: "2/3 — Simpan konfigurasi",
        STEP_DOWNLOAD: "3/3 — Download",
        STEP_DONE: "Selesai — hasil siap dibuka",
        STEP_ERROR: "Download gagal — periksa log lalu coba lagi",
    }

    st.caption(
        f"**Langkah aktif:** "
        f"{step_labels.get(workflow_step, workflow_step)}"
    )

    if reset_clicked:
        clear_area_state()
        st.rerun(scope="fragment")

    if preview_clicked:
        if is_indonesia:
            ready_bbox = (
                default_view_bbox
            )
            preview_ids = list(
                all_master_ids
            )
            draft_bbox = None

        else:
            draft_bbox = (
                st.session_state
                .draft_bbox
            )

            if draft_bbox is None:
                st.warning(
                    "Gambar rectangle terlebih dahulu."
                )
                return

            ready_bbox = draft_bbox
            preview_ids = (
                select_quad_ids(
                    master_records,
                    ready_bbox,
                )
            )

        if not preview_ids:
            st.error(
                "Tidak ada Quad master "
                "yang beririsan dengan area."
            )
            return

        detach_current_run()
        area_mode_value = (
            "indonesia"
            if is_indonesia
            else "custom"
        )
        st.session_state.selection_signature = (
            current_selection_signature(
                mosaic_name=mosaic_name,
                area_mode=area_mode_value,
                bbox=ready_bbox,
                selected_ids=preview_ids,
            )
        )

        st.session_state.ready_bbox = (
            ready_bbox
        )

        st.session_state.draft_bbox = (
            draft_bbox
        )

        st.session_state.selected_ids = (
            preview_ids
        )

        st.session_state.area_status = (
            STATUS_READY
        )

        st.session_state.workflow_step = STEP_SAVE
        st.session_state.last_download_code = None
        st.session_state.last_download_log = []
        st.session_state.last_run_name = ""
        st.session_state.last_run_folder = ""
        st.session_state.last_run_excel = ""
        st.session_state.notice = (
            f"Preview siap: "
            f"{len(preview_ids):,} Quad terpilih. "
            "Langkah berikutnya: simpan konfigurasi."
        )

        st.rerun(scope="fragment")

    if save_clicked:
        saved_path = save_selection(
            area_mode=(
                "indonesia"
                if is_indonesia
                else "custom"
            ),
            bbox=tuple(
                st.session_state
                .ready_bbox
            ),
            selected_ids=list(
                st.session_state
                .selected_ids
            ),
            mosaic_name=mosaic_name,
            selected_file=selected_file,
            create_backup=backup_config_files,
        )

        st.session_state.workflow_step = STEP_DOWNLOAD
        st.session_state.notice = (
            "Konfigurasi berhasil disimpan ke "
            f"{path_for_env(saved_path)} dan .env. "
            "Langkah berikutnya: download seluruh Quad."
        )
        st.rerun(scope="fragment")

    if download_clicked:
        if active_run:
            st.error(
                "Masih ada sesi download aktif. Pause/batalkan atau "
                "selesaikan sesi tersebut sebelum membuat run baru."
            )
            return

        saved_path = save_selection(
            area_mode=("indonesia" if is_indonesia else "custom"),
            bbox=tuple(st.session_state.ready_bbox),
            selected_ids=list(st.session_state.selected_ids),
            mosaic_name=mosaic_name,
            selected_file=selected_file,
            create_backup=backup_config_files,
        )
        run_id = create_run_id()
        signature = str(
            st.session_state.selection_signature
        ).strip()
        st.session_state.attached_run_id = run_id
        st.session_state.attached_selection_signature = signature
        st.session_state.detached_run_id = ""
        st.session_state.workflow_step = STEP_DOWNLOAD
        st.session_state.notice = (
            "Konfigurasi tersimpan. Downloader berjalan di background; "
            "aplikasi dapat digunakan untuk memantau, pause, dan resume."
        )
        pause_flag_file.unlink(missing_ok=True)
        cancel_flag_file.unlink(missing_ok=True)
        launch_downloader_process(
            "new",
            run_id=run_id,
            selection_signature=signature,
        )
        st.rerun(scope="fragment")

    if open_folder_clicked:
        result_folder_text = str(
            last_run.get("output_folder", "")
            if last_matches_selection
            else ""
        )
        result_folder = Path(
            result_folder_text
        )

        if not result_folder.exists():
            st.error(
                "Folder hasil run terakhir tidak ditemukan: "
                f"{result_folder}"
            )
        elif os.name == "nt":
            os.startfile(  # type: ignore[attr-defined]
                result_folder
            )
        else:
            st.info(
                f"Folder hasil: "
                f"{result_folder}"
            )

    # Panel proses aktif tidak lagi memakai status terminal.
    if active_run:
        if not active_matches_selection:
            st.info(
                "Ada sesi download aktif yang terpisah dari pilihan area "
                "saat ini. Selesaikan, pause, atau batalkan sesi tersebut "
                "sebelum memulai run baru."
            )
        render_download_runtime_panel(
            active_run,
            active_run_file=active_run_file,
            pause_flag=pause_flag_file,
            cancel_flag=cancel_flag_file,
            ui_log_max_lines=ui_log_max_lines,
        )

    # Riwayat selalu terpisah dan tidak mengubah workflow area baru.
    render_last_run_panel(
        last_run,
        active_run=active_run,
        active_run_file=active_run_file,
        pause_flag=pause_flag_file,
        cancel_flag=cancel_flag_file,
        ui_log_max_lines=ui_log_max_lines,
    )

    if ready:
        preview_limit = 100
        sorted_ids = sorted(
            st.session_state
            .selected_ids
        )

        with st.expander(
            (
                "Preview Quad ID "
                f"(100 pertama dari "
                f"{len(sorted_ids):,})"
            ),
            expanded=False,
        ):
            preview_rows = [
                {
                    "Quad ID": quad_id,
                    "Status lokal": (
                        "Existing"
                        if quad_id
                        in existing_ids
                        else "Belum diunduh"
                    ),
                }
                for quad_id in (
                    sorted_ids[
                        :preview_limit
                    ]
                )
            ]

            st.dataframe(
                preview_rows,
                width="stretch",
                hide_index=True,
            )

            if (
                len(sorted_ids)
                > preview_limit
            ):
                st.caption(
                    "Daftar lengkap tersimpan "
                    "di selected_quads.json "
                    "setelah konfigurasi disimpan."
                )


render_area_workspace()
render_app_footer()
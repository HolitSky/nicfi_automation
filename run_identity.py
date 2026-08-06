from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable
from uuid import uuid4


def normalize_bbox(value: Any) -> list[float]:
    """Normalize BBOX to [min_lon, min_lat, max_lon, max_lat]."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = []

    if len(parts) != 4:
        return []

    try:
        return [round(float(part), 8) for part in parts]
    except (TypeError, ValueError):
        return []


def build_selection_signature(
    *,
    mosaic_name: str,
    area_mode: str,
    bbox: Any,
    quad_ids: Iterable[Any],
) -> str:
    """Create a deterministic signature for one UI selection."""
    normalized_ids = sorted(
        {
            str(value).strip()
            for value in quad_ids
            if str(value).strip()
        }
    )
    payload = {
        "mosaic_name": str(mosaic_name).strip(),
        "area_mode": str(area_mode).strip().lower(),
        "bbox": normalize_bbox(bbox),
        "quad_ids": normalized_ids,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def selection_signature_from_payload(payload: dict[str, Any]) -> str:
    stored = str(payload.get("selection_signature", "")).strip()
    if stored:
        return stored
    return build_selection_signature(
        mosaic_name=str(payload.get("mosaic_name", "")),
        area_mode=str(payload.get("area_mode", "")),
        bbox=payload.get("bbox", []),
        quad_ids=payload.get("quad_ids", []),
    )


def create_run_id(now: datetime | None = None) -> str:
    moment = now or datetime.now()
    return f"{moment:%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"


def ensure_run_id(payload: dict[str, Any]) -> str:
    existing = str(payload.get("run_id", "")).strip()
    if existing:
        return existing

    seed = "|".join(
        [
            str(payload.get("run_name", "")),
            str(payload.get("started_at", "")),
            str(payload.get("output_folder", "")),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"legacy_{digest}"

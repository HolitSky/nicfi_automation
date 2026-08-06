"""Core library package for NICFI Automation.

Modul-modul library (download_state, run_identity, planet_report)
dikumpulkan di sini agar root folder tetap bersih.

PROJECT_ROOT menunjuk ke folder project (parent dari ``core/``),
sehingga semua path relatif terhadap project root tetap benar.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

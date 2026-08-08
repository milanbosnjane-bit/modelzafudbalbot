"""Resolve trained model artifact paths."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import get_settings

settings = get_settings()

# Package directory: app/models/ (alongside lightgbm_model.py, etc.)
APP_MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def model_search_dirs() -> list[Path]:
    """Search order: configured model_dir, then app/models/."""
    dirs: list[Path] = []
    seen: set[Path] = set()
    for raw in (settings.model_dir, APP_MODELS_DIR):
        path = Path(raw)
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        dirs.append(path)
    return dirs


def resolve_trained_model(filename: str) -> Path | None:
    for base in model_search_dirs():
        candidate = base / filename
        if candidate.is_file():
            return candidate
    return None


def missing_model_files() -> list[str]:
    """Deprecated — ML modeli više nisu potrebni."""
    return []


def resolve_dc_params_path() -> Path | None:
    for base in model_search_dirs():
        candidate = base / settings.dc_params_file
        if candidate.is_file():
            return candidate
    return None


def dc_params_age_days(path: Path | None = None) -> int:
    target = path or resolve_dc_params_path()
    if target is None or not target.is_file():
        return 10_000
    mtime = datetime.fromtimestamp(target.stat().st_mtime)
    return max(0, (datetime.utcnow() - mtime).days)

"""
config/settings.py
Ładowanie i walidacja konfiguracji użytkownika z YAML.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Ścieżka do domyślnego pliku konfiguracyjnego
DEFAULT_CONFIG_PATH = Path(__file__).parent / "user_config.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Wczytaj konfigurację z YAML. Jeśli brak pliku – używaj wartości domyślnych."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        logger.warning(f"Brak pliku konfiguracyjnego: {config_path}. Używam domyślnych ustawień.")
        return _default_config()

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    _validate_config(config)
    logger.info(f"Wczytano konfigurację z: {config_path}")
    return config


def _validate_config(config: dict) -> None:
    """Podstawowa walidacja struktury konfiguracji."""
    required_sections = ["source", "metrics", "filters", "scoring", "portfolio", "settings"]
    for section in required_sections:
        if section not in config:
            raise ValueError(f"Brakuje sekcji '{section}' w konfiguracji.")

    valid_sources = {"sp500", "nasdaq100", "wig20", "dax40", "custom"}
    source = config.get("source", {}).get("index", "")
    if source not in valid_sources:
        raise ValueError(f"Nieznane źródło danych: '{source}'. Dostępne: {valid_sources}")

    if source == "custom":
        tickers = config.get("source", {}).get("custom_tickers", [])
        if not tickers:
            raise ValueError("Dla source.index='custom' wymagana jest lista source.custom_tickers.")


def _default_config() -> dict[str, Any]:
    """Minimalna domyślna konfiguracja."""
    return {
        "source": {"index": "sp500", "custom_tickers": []},
        "metrics": {
            "fundamental": {"enabled": True, "fields": ["pe_ratio", "roe", "debt_to_equity"]},
            "technical": {"enabled": True, "fields": ["momentum_3m", "rsi_14"]},
        },
        "filters": {
            "fundamental": {"pe_ratio": [0, 30], "roe": [5, None]},
            "technical": {"rsi_14": [30, 70]},
        },
        "scoring": {"weights": {"roe": 2.0, "momentum_3m": 1.0, "pe_ratio": -0.5}},
        "portfolio": {
            "max_positions": 20,
            "min_results": 5,
            "weighting": "score_weighted",
            "min_history_runs": 3,
            "stability_bonus_weight": 0.5,
        },
        "scheduler": {"enabled": False, "frequency": "weekly", "run_at_hour": 7, "weekday": 0},
        "settings": {
            "fetch_workers": 5,
            "api_delay_seconds": 0.3,
            "db_path": "screener_data.db",
            "reports_dir": "reports",
            "log_level": "INFO",
            "price_history_days": 400,
            "max_fetch_errors": 3,
        },
    }


def setup_logging(level: str = "INFO") -> None:
    """Skonfiguruj globalny logger."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

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
DEFAULT_CONFIG_PATH = Path(__file__).parent / "user_config.yaml"


def load_config(path=None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        logger.warning(f"Brak pliku konfiguracyjnego: {config_path}. Używam domyślnych.")
        return _default_config()
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    _validate_config(config)
    logger.info(f"Wczytano konfigurację z: {config_path}")
    return config


def _validate_config(config: dict) -> None:
    required = ["source", "metrics", "filters", "scoring", "portfolio", "settings"]
    for section in required:
        if section not in config:
            raise ValueError(f"Brakuje sekcji '{section}' w konfiguracji.")

    ai_config = config.get("source", {}).get("ai", {})
    backend = ai_config.get("backend", "groq")
    valid_backends = {"groq", "anthropic", "openai", "mock"}
    if backend not in valid_backends:
        raise ValueError(f"Nieznany backend AI: '{backend}'. Dostępne: {valid_backends}")

    valid_strategies = {
        "growth_quality", "deep_value", "compounders",
        "sector_leaders", "thematic", "global_diversified",
        "emerging_growth", "asymmetric_risk",
    }
    strategy = config.get("source", {}).get("strategy", "growth_quality")
    if strategy not in valid_strategies:
        raise ValueError(f"Nieznana strategia: '{strategy}'. Dostępne: {valid_strategies}")


def _default_config() -> dict[str, Any]:
    return {
        "source": {
            "strategy": "growth_quality",
            "ai": {
                "backend": "mock",
                "model": "llama-3.3-70b-versatile",
                "n_tickers": 50,
                "temperature": 0.35,
                "max_retries": 3,
                "multi_shot": False,
                "multi_shot_runs": 3,
            },
        },
        "metrics": {
            "fundamental": {"enabled": True, "fields": ["pe_ratio", "roe", "debt_to_equity"]},
            "technical": {"enabled": True, "fields": ["momentum_3m", "rsi_14"]},
        },
        "filters": {
            "fundamental": {"pe_ratio": [0, 60], "roe": [10, None]},
            "technical": {"rsi_14": [25, 80]},
        },
        "scoring": {"weights": {"roe": 2.0, "momentum_3m": 1.0, "pe_ratio": -0.4}},
        "portfolio": {
            "max_positions": 20, "min_results": 5, "weighting": "score_weighted",
            "min_history_runs": 3, "stability_bonus_weight": 0.5,
        },
        "scheduler": {"enabled": False, "frequency": "weekly", "run_at_hour": 7, "weekday": 0},
        "settings": {
            "fetch_workers": 5, "api_delay_seconds": 0.3, "db_path": "screener_data.db",
            "reports_dir": "reports", "log_level": "INFO", "price_history_days": 400,
            "max_fetch_errors": 3,
        },
    }


def setup_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

"""
data/ticker_source.py
Router źródeł tickerów – teraz wyłącznie AI-based.
"""
from __future__ import annotations
import logging
from data.ai_ticker_source import fetch_ai_tickers

logger = logging.getLogger(__name__)


def get_tickers(source_config: dict) -> list[str]:
    """Pobierz listę tickerów na podstawie konfiguracji AI."""
    ai_config = source_config.get("ai", {})
    if "strategy" not in ai_config:
        ai_config["strategy"] = source_config.get("strategy", "growth_quality")

    logger.info(
        f"Źródło tickerów: AI [{ai_config.get('backend', 'groq')}] "
        f"strategia={ai_config.get('strategy')} "
        f"n={ai_config.get('n_tickers', 50)}"
    )

    tickers = fetch_ai_tickers(ai_config)
    if not tickers:
        raise RuntimeError(
            "AI ticker source zwróciło pustą listę. "
            "Sprawdź konfigurację (api_key, backend) i spróbuj ponownie."
        )
    return tickers


def get_tickers_multi_strategy(source_config: dict) -> dict[str, list[str]]:
    """Pobierz tickery dla wielu strategii jednocześnie."""
    strategies = source_config.get("multi_strategy", ["growth_quality"])
    ai_config = source_config.get("ai", {})
    results: dict[str, list[str]] = {}

    for strategy in strategies:
        logger.info(f"Pobieranie tickerów dla strategii: {strategy}")
        try:
            cfg = {**ai_config, "strategy": strategy}
            results[strategy] = fetch_ai_tickers(cfg)
            logger.info(f"  {strategy}: {len(results[strategy])} tickerów")
        except Exception as exc:
            logger.error(f"  {strategy}: błąd – {exc}")
            results[strategy] = []

    return results

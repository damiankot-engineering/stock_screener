"""
data/fetcher.py
DataFetcher — używa CompositeProvider (FMP + Stooq + yFinance fallback).

Zachowana ta sama zewnętrzna API (TickerData, DataFetcher.fetch_all)
żeby reszta systemu nie wymagała zmian.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# TickerData – bez zmian (kompatybilność z enriched_fetcher etc.)
# ─────────────────────────────────────────────────────────────

@dataclass
class TickerData:
    """Komplet danych dla jednego tickera."""
    ticker: str
    fundamentals: dict[str, float | None] = field(default_factory=dict)
    technicals:   dict[str, float | None] = field(default_factory=dict)
    fetch_errors: list[str]               = field(default_factory=list)
    success: bool = True

    @property
    def all_metrics(self) -> dict[str, float | None]:
        return {**self.fundamentals, **self.technicals}


# ─────────────────────────────────────────────────────────────
# TechnicalCalculator — oblicza wskaźniki z DataFrame cen
# ─────────────────────────────────────────────────────────────

class TechnicalCalculator:
    """Oblicza wskaźniki techniczne z historii cen OHLCV."""

    def compute(self, ticker: str, hist: pd.DataFrame,
                fields: list[str]) -> dict[str, float | None]:
        if hist.empty or "Close" not in hist.columns:
            return {f: None for f in fields}

        close  = hist["Close"].dropna()
        volume = hist["Volume"].dropna() if "Volume" in hist.columns else pd.Series(dtype=float)

        if len(close) < 20:
            logger.debug(f"{ticker}: za mało sesji ({len(close)})")
            return {f: None for f in fields}

        results: dict[str, float | None] = {}
        for field_name in fields:
            try:
                results[field_name] = self._compute_one(field_name, close, volume)
            except Exception as exc:
                logger.debug(f"{ticker} {field_name}: {exc}")
                results[field_name] = None
        return results

    def _compute_one(self, field: str, close: pd.Series,
                     volume: pd.Series) -> float | None:
        def momentum(days: int) -> float | None:
            if len(close) < days:
                return None
            pct = (close.iloc[-1] / close.iloc[-days] - 1) * 100
            return round(float(pct), 4) if np.isfinite(pct) else None

        if field == "momentum_1m":   return momentum(21)
        if field == "momentum_3m":   return momentum(63)
        if field == "momentum_6m":   return momentum(126)
        if field == "momentum_12m":  return momentum(252)
        if field == "rsi_14":        return self._rsi(close, 14)
        if field == "above_ma50":
            return (1.0 if len(close) >= 50 and close.iloc[-1] > close.iloc[-50:].mean() else 0.0)
        if field == "above_ma200":
            return (1.0 if len(close) >= 200 and close.iloc[-1] > close.iloc[-200:].mean() else 0.0)
        if field == "volatility_30d":
            if len(close) < 30: return None
            returns = close.pct_change().dropna().iloc[-30:]
            return round(float(returns.std() * np.sqrt(252) * 100), 4)
        if field == "volume_ratio":
            if volume.empty or len(volume) < 20: return None
            avg = volume.iloc[-20:].mean()
            return round(float(volume.iloc[-1] / avg), 4) if avg > 0 else None
        return None

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> float | None:
        if len(close) < period + 1:
            return None
        delta  = close.diff().dropna()
        gains  = delta.clip(lower=0)
        losses = (-delta).clip(lower=0)
        avg_g  = gains.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
        avg_l  = losses.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
        if avg_l == 0:
            return 100.0
        return round(float(100 - 100 / (1 + avg_g / avg_l)), 4)


# ─────────────────────────────────────────────────────────────
# DataFetcher — publiczne API (niezmienione)
# ─────────────────────────────────────────────────────────────

class DataFetcher:
    """
    Pobiera dane fundamentalne i techniczne używając CompositeProvider.
    Zewnętrzne API identyczne jak poprzednio — reszta systemu bez zmian.
    """

    def __init__(self, config: dict):
        self.settings       = config.get("settings", {})
        self.metrics_config = config.get("metrics", {})
        self.workers        = self.settings.get("fetch_workers", 5)
        self.api_delay      = self.settings.get("api_delay_seconds", 0.3)
        self.max_errors     = self.settings.get("max_fetch_errors", 3)
        self.history_days   = self.settings.get("price_history_days", 400)

        self.fundamental_fields: list[str] = (
            self.metrics_config.get("fundamental", {}).get("fields", [])
            if self.metrics_config.get("fundamental", {}).get("enabled", True)
            else []
        )
        self.technical_fields: list[str] = (
            self.metrics_config.get("technical", {}).get("fields", [])
            if self.metrics_config.get("technical", {}).get("enabled", True)
            else []
        )

        self._tech_calc = TechnicalCalculator()

        # Zbuduj composite provider leniwie (przy pierwszym użyciu)
        self._config   = config
        self._provider = None

    @property
    def provider(self):
        if self._provider is None:
            from data.providers.composite import build_composite
            self._provider = build_composite(self._config)
            logger.info(f"DataFetcher używa: {self._provider.name}")
        return self._provider

    # ── fetch_all — publiczne API (bez zmian) ─────────────────

    def fetch_all(self, tickers: list[str]) -> list[TickerData]:
        logger.info(f"Pobieranie danych dla {len(tickers)} tickerów "
                    f"({self.workers} wątków)")
        results: list[TickerData] = []
        failed:  list[str]        = []

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self._fetch_one_safe, t): t
                for t in tickers
            }
            for i, future in enumerate(as_completed(futures), 1):
                ticker = futures[future]
                try:
                    td = future.result()
                    results.append(td)
                    if td.success:
                        logger.debug(f"[{i}/{len(tickers)}] ✓ {ticker}")
                    else:
                        failed.append(ticker)
                except Exception as exc:
                    logger.warning(f"Nieoczekiwany błąd {ticker}: {exc}")
                    results.append(TickerData(ticker=ticker, success=False,
                                              fetch_errors=[str(exc)]))
                    failed.append(ticker)

        ok = len(results) - len(failed)
        logger.info(f"Pobrano: {ok}/{len(tickers)} OK, {len(failed)} błędów")
        return results

    def _fetch_one_safe(self, ticker: str) -> TickerData:
        time.sleep(self.api_delay)
        errors: list[str] = []
        for attempt in range(1, self.max_errors + 1):
            try:
                return self._fetch_one(ticker)
            except Exception as exc:
                errors.append(f"Próba {attempt}: {exc}")
                if attempt < self.max_errors:
                    time.sleep(self.api_delay * attempt * 2)
        return TickerData(ticker=ticker, success=False, fetch_errors=errors)

    def _fetch_one(self, ticker: str) -> TickerData:
        pr = self.provider.get_all(ticker, days=self.history_days)

        # Filtruj do żądanych pól
        fundamentals = {k: pr.fundamentals.get(k)
                        for k in self.fundamental_fields
                        if k in DataProvider.FUNDAMENTAL_FIELDS}

        # Uzupełnij też metryki spoza standardowego zestawu (np. makro, insider)
        extra = {k: v for k, v in pr.fundamentals.items()
                 if k not in DataProvider.FUNDAMENTAL_FIELDS}
        fundamentals.update(extra)

        # Wskaźniki techniczne z historii cen
        technicals = (
            self._tech_calc.compute(ticker, pr.price_history, self.technical_fields)
            if self.technical_fields and pr.has_prices
            else {f: None for f in self.technical_fields}
        )

        total_non_none = sum(1 for v in {**fundamentals, **technicals}.values()
                             if v is not None)
        if total_non_none == 0:
            raise ValueError(f"Brak danych dla {ticker}")

        return TickerData(
            ticker=ticker,
            fundamentals=fundamentals,
            technicals=technicals,
            success=True,
        )


# Potrzebny przez composite.py dla type hints
from data.providers.base import DataProvider  # noqa: E402

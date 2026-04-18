"""
data/fetcher.py
Pobieranie danych fundamentalnych i technicznych z Yahoo Finance (yfinance).
Obsługuje równoległe pobieranie, retry, brakujące dane oraz throttling API.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Struktury danych
# ─────────────────────────────────────────────────────────────

@dataclass
class TickerData:
    """Komplet danych dla jednego tickera."""
    ticker: str
    fundamentals: dict[str, float | None] = field(default_factory=dict)
    technicals: dict[str, float | None] = field(default_factory=dict)
    fetch_errors: list[str] = field(default_factory=list)
    success: bool = True

    @property
    def all_metrics(self) -> dict[str, float | None]:
        return {**self.fundamentals, **self.technicals}


# ─────────────────────────────────────────────────────────────
# Główna klasa fetchera
# ─────────────────────────────────────────────────────────────

class DataFetcher:
    """
    Pobiera dane z Yahoo Finance dla listy tickerów.
    Implementuje:
    - Równoległe pobieranie (ThreadPoolExecutor)
    - Throttling (api_delay_seconds)
    - Obsługę błędów z retry
    - Normalizację brakujących danych
    """

    def __init__(self, config: dict):
        self.settings = config.get("settings", {})
        self.metrics_config = config.get("metrics", {})
        self.workers = self.settings.get("fetch_workers", 5)
        self.api_delay = self.settings.get("api_delay_seconds", 0.3)
        self.max_errors = self.settings.get("max_fetch_errors", 3)
        self.history_days = self.settings.get("price_history_days", 400)

        self.fundamental_fields = (
            self.metrics_config.get("fundamental", {}).get("fields", [])
            if self.metrics_config.get("fundamental", {}).get("enabled", True)
            else []
        )
        self.technical_fields = (
            self.metrics_config.get("technical", {}).get("fields", [])
            if self.metrics_config.get("technical", {}).get("enabled", True)
            else []
        )

    def fetch_all(self, tickers: list[str]) -> list[TickerData]:
        """
        Pobierz dane dla wszystkich tickerów równolegle.

        Returns:
            Lista obiektów TickerData (nawet te z błędami, dla logowania)
        """
        logger.info(f"Rozpoczynam pobieranie danych dla {len(tickers)} tickerów "
                    f"({self.workers} wątków, delay={self.api_delay}s)")

        results: list[TickerData] = []
        failed: list[str] = []

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self._fetch_one_safe, ticker): ticker
                for ticker in tickers
            }
            for i, future in enumerate(as_completed(futures), 1):
                ticker = futures[future]
                try:
                    data = future.result()
                    results.append(data)
                    if data.success:
                        logger.debug(f"[{i}/{len(tickers)}] ✓ {ticker}")
                    else:
                        failed.append(ticker)
                        logger.debug(f"[{i}/{len(tickers)}] ✗ {ticker}: {data.fetch_errors}")
                except Exception as exc:
                    logger.warning(f"Nieoczekiwany błąd dla {ticker}: {exc}")
                    results.append(TickerData(ticker=ticker, success=False,
                                              fetch_errors=[str(exc)]))
                    failed.append(ticker)

        success_count = len(results) - len(failed)
        logger.info(f"Pobrano dane: {success_count}/{len(tickers)} sukces, "
                    f"{len(failed)} błędów")
        if failed:
            logger.warning(f"Nieudane tickery: {failed[:10]}{'...' if len(failed) > 10 else ''}")

        return results

    def _fetch_one_safe(self, ticker: str) -> TickerData:
        """Wrapper z obsługą błędów i throttlingiem."""
        time.sleep(self.api_delay)
        errors: list[str] = []

        for attempt in range(1, self.max_errors + 1):
            try:
                return self._fetch_one(ticker)
            except Exception as exc:
                errors.append(f"Próba {attempt}: {exc}")
                if attempt < self.max_errors:
                    time.sleep(self.api_delay * attempt * 2)

        logger.warning(f"Porzucono {ticker} po {self.max_errors} próbach")
        return TickerData(ticker=ticker, success=False, fetch_errors=errors)

    def _fetch_one(self, ticker: str) -> TickerData:
        """Pobierz dane dla jednego tickera."""
        yticker = yf.Ticker(ticker)

        info = {}
        try:
            info = yticker.info or {}
        except Exception as exc:
            logger.debug(f"{ticker}: błąd pobierania info: {exc}")

        # Pobierz historię cen tylko jeśli potrzebne technikalia
        price_history = pd.DataFrame()
        if self.technical_fields:
            try:
                price_history = yticker.history(period=f"{self.history_days}d", auto_adjust=True)
            except Exception as exc:
                logger.debug(f"{ticker}: błąd historii cen: {exc}")

        fundamentals = self._extract_fundamentals(ticker, info) if self.fundamental_fields else {}
        technicals = self._compute_technicals(ticker, price_history) if self.technical_fields else {}

        # Walidacja: jeśli brakuje wszystkich danych, uznaj za błąd
        total_non_none = sum(1 for v in {**fundamentals, **technicals}.values() if v is not None)
        if total_non_none == 0:
            raise ValueError(f"Brak jakichkolwiek danych dla {ticker}")

        return TickerData(
            ticker=ticker,
            fundamentals=fundamentals,
            technicals=technicals,
            success=True,
        )

    # ─────────────────────────────────────────────────────────
    # Ekstrakcja danych fundamentalnych
    # ─────────────────────────────────────────────────────────

    def _extract_fundamentals(self, ticker: str, info: dict) -> dict[str, float | None]:
        """Mapuje surowe dane z yfinance.info na znormalizowane metryki."""

        def safe(key: str, divisor: float = 1.0) -> float | None:
            val = info.get(key)
            if val is None or val == "Infinity" or (isinstance(val, float) and np.isnan(val)):
                return None
            try:
                result = float(val) / divisor
                return result if np.isfinite(result) else None
            except (TypeError, ValueError):
                return None

        mapping: dict[str, float | None] = {
            "pe_ratio":       safe("trailingPE"),
            "pb_ratio":       safe("priceToBook"),
            "ps_ratio":       safe("priceToSalesTrailing12Months"),
            "roe":            safe("returnOnEquity", 1 / 100),  # yfinance zwraca ułamek
            "roa":            safe("returnOnAssets", 1 / 100),
            "debt_to_equity": safe("debtToEquity", 100),        # yfinance zwraca np. 150 = 1.5
            "current_ratio":  safe("currentRatio"),
            "revenue_growth": safe("revenueGrowth", 1 / 100),
            "earnings_growth": safe("earningsGrowth", 1 / 100),
            "profit_margin":  safe("profitMargins", 1 / 100),
            "dividend_yield": safe("dividendYield", 1 / 100),
            "market_cap":     safe("marketCap"),
        }

        # yfinance zwraca ROE/ROA jako ułamki (0.15 = 15%), przelicz na procenty
        for pct_key in ["roe", "roa", "revenue_growth", "earnings_growth",
                        "profit_margin", "dividend_yield"]:
            if mapping.get(pct_key) is not None:
                mapping[pct_key] = round(mapping[pct_key] * 100, 4)  # type: ignore

        # Filtruj tylko żądane pola
        return {k: mapping.get(k) for k in self.fundamental_fields if k in mapping}

    # ─────────────────────────────────────────────────────────
    # Obliczanie wskaźników technicznych
    # ─────────────────────────────────────────────────────────

    def _compute_technicals(self, ticker: str, hist: pd.DataFrame) -> dict[str, float | None]:
        """Oblicza wskaźniki techniczne z historii cen."""
        if hist.empty or "Close" not in hist.columns:
            return {k: None for k in self.technical_fields}

        close = hist["Close"].dropna()
        volume = hist["Volume"].dropna() if "Volume" in hist.columns else pd.Series(dtype=float)

        if len(close) < 20:
            logger.debug(f"{ticker}: za mało danych historycznych ({len(close)} sesji)")
            return {k: None for k in self.technical_fields}

        results: dict[str, float | None] = {}

        for field in self.technical_fields:
            try:
                results[field] = self._compute_single_technical(field, close, volume)
            except Exception as exc:
                logger.debug(f"{ticker}: błąd wskaźnika {field}: {exc}")
                results[field] = None

        return results

    def _compute_single_technical(
        self, field: str, close: pd.Series, volume: pd.Series
    ) -> float | None:
        """Oblicz jeden wskaźnik techniczny."""

        def momentum(days: int) -> float | None:
            if len(close) < days:
                return None
            pct = (close.iloc[-1] / close.iloc[-days] - 1) * 100
            return round(float(pct), 4) if np.isfinite(pct) else None

        if field == "momentum_1m":
            return momentum(21)
        elif field == "momentum_3m":
            return momentum(63)
        elif field == "momentum_6m":
            return momentum(126)
        elif field == "momentum_12m":
            return momentum(252)

        elif field == "rsi_14":
            return self._compute_rsi(close, 14)

        elif field == "volume_ratio":
            if volume.empty or len(volume) < 20:
                return None
            avg_20 = volume.iloc[-20:].mean()
            if avg_20 == 0:
                return None
            ratio = float(volume.iloc[-1]) / float(avg_20)
            return round(ratio, 4)

        elif field == "above_ma50":
            if len(close) < 50:
                return None
            ma50 = close.iloc[-50:].mean()
            return 1.0 if close.iloc[-1] > ma50 else 0.0

        elif field == "above_ma200":
            if len(close) < 200:
                return None
            ma200 = close.iloc[-200:].mean()
            return 1.0 if close.iloc[-1] > ma200 else 0.0

        elif field == "volatility_30d":
            if len(close) < 30:
                return None
            returns = close.pct_change().dropna().iloc[-30:]
            vol = returns.std() * np.sqrt(252) * 100  # annualizowana zmienność
            return round(float(vol), 4)

        return None

    @staticmethod
    def _compute_rsi(close: pd.Series, period: int = 14) -> float | None:
        """RSI (Relative Strength Index) – klasyczna implementacja Wildera."""
        if len(close) < period + 1:
            return None
        delta = close.diff().dropna()
        gains = delta.clip(lower=0)
        losses = (-delta).clip(lower=0)

        # Wilder's smoothing (EMA z alpha=1/period)
        avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
        avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi), 4)

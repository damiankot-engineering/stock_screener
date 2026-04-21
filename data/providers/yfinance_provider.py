"""
data/providers/yfinance_provider.py
Yahoo Finance (yfinance) — jako implementacja DataProvider, używana jako fallback.

Refaktoryzacja istniejącej logiki z fetcher.py do spójnego interfejsu.
yfinance pozostaje jako backup gdy FMP lub Stooq nie mają danych dla danego tickera.

Zalety yfinance jako fallback:
  • Bez klucza API
  • Szerokie pokrycie tickerów (kryterium walidatora)
  • Dane dostępne natychmiast po uruchomieniu

Wady (dlatego jest fallback, nie primary):
  • Niespójna jakość danych fundamentalnych
  • Częste timeouty i zmiany API
  • Dane z opóźnieniem lub błędne dla rynków EM
  • .info dict nie ma gwarantowanej struktury
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .base import DataProvider

logger = logging.getLogger(__name__)


class YFinanceProvider(DataProvider):
    """Yahoo Finance jako fallback DataProvider."""

    def __init__(self, timeout_seconds: int = 30):
        self.timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "yFinance"

    @property
    def provides_fundamentals(self) -> bool:
        return True

    @property
    def provides_prices(self) -> bool:
        return True

    def get_fundamentals(self, ticker: str) -> dict[str, float | None]:
        """Pobierz dane fundamentalne z yfinance.info."""
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
        except Exception as exc:
            logger.debug(f"yFinance info {ticker}: {exc}")
            return {}

        def safe(key: str, divisor: float = 1.0) -> float | None:
            return self._safe_float(info.get(key), divisor)

        # yfinance zwraca ROE/ROA jako ułamki (0.15 = 15%) → mnożymy przez 100
        raw = {
            "pe_ratio":        safe("trailingPE"),
            "pb_ratio":        safe("priceToBook"),
            "ps_ratio":        safe("priceToSalesTrailing12Months"),
            "roe":             safe("returnOnEquity"),
            "roa":             safe("returnOnAssets"),
            "debt_to_equity":  safe("debtToEquity", 100),
            "current_ratio":   safe("currentRatio"),
            "revenue_growth":  safe("revenueGrowth"),
            "earnings_growth": safe("earningsGrowth"),
            "profit_margin":   safe("profitMargins"),
            "gross_margin":    safe("grossMargins"),
            "operating_margin": safe("operatingMargins"),
            "dividend_yield":  safe("dividendYield"),
            "market_cap":      safe("marketCap"),
        }

        # Konwertuj ułamki → procenty dla metryk procentowych
        pct_keys = {"roe", "roa", "revenue_growth", "earnings_growth",
                    "profit_margin", "gross_margin", "operating_margin",
                    "dividend_yield"}
        for k in pct_keys:
            if raw.get(k) is not None:
                raw[k] = round(raw[k] * 100, 4)

        return {k: v for k, v in raw.items() if v is not None}

    def get_price_history(self, ticker: str, days: int = 400) -> pd.DataFrame:
        """Pobierz historię cen z yfinance."""
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(
                period=f"{days}d",
                auto_adjust=True,
                timeout=self.timeout,
            )
            if hist.empty:
                return pd.DataFrame()
            # Normalizuj nazwy kolumn
            return hist.rename(columns=str.title)[
                [c for c in ["Open", "High", "Low", "Close", "Volume"]
                 if c in hist.rename(columns=str.title).columns]
            ]
        except Exception as exc:
            logger.debug(f"yFinance history {ticker}: {exc}")
            return pd.DataFrame()

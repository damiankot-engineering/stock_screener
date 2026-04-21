"""
data/providers/base.py
Abstrakcyjny interfejs dostawcy danych finansowych.

Wszystkie implementacje (FMP, Stooq, yfinance, Bloomberg, Refinitiv) dziedziczą
z DataProvider i implementują te same metody. Composite provider używa ich
w kolejności priorytetu i scala wyniki.

KONTRAKT:
  get_fundamentals(ticker) → dict {metric_name: float|None}
  get_price_history(ticker, days) → pd.DataFrame (kolumny: Close, Volume)
  Brakujące dane → None (nie rzuca wyjątku)
  Każde pole normalizowane do wspólnych jednostek:
    - ceny w USD
    - stopy procentowe w %
    - market_cap w USD
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    """Wynik pobrania danych od jednego dostawcy."""
    ticker:       str
    fundamentals: dict[str, float | None] = field(default_factory=dict)
    price_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    source:       str = "unknown"
    errors:       list[str] = field(default_factory=list)

    @property
    def has_fundamentals(self) -> bool:
        return any(v is not None for v in self.fundamentals.values())

    @property
    def has_prices(self) -> bool:
        return not self.price_history.empty and len(self.price_history) >= 20


class DataProvider(ABC):
    """
    Abstrakcyjny dostawca danych finansowych.

    Implementacje:
      FMPProvider         — Financial Modeling Prep (darmowy, 250 req/dzień)
      StooqProvider       — Stooq CSV (darmowy, bez klucza, dane historyczne)
      YFinanceProvider    — Yahoo Finance (fallback, bez klucza)
      — przyszłe płatne: BloombergProvider, RefinitivProvider, FactSetProvider
    """

    # Nazwy metryk — wspólne dla wszystkich dostawców
    FUNDAMENTAL_FIELDS = {
        "pe_ratio", "pb_ratio", "ps_ratio", "ev_ebitda",
        "roe", "roa", "roic",
        "debt_to_equity", "net_debt_to_ebitda", "current_ratio", "quick_ratio",
        "revenue_growth", "earnings_growth", "ebitda_growth",
        "profit_margin", "gross_margin", "operating_margin", "fcf_margin",
        "dividend_yield", "payout_ratio",
        "market_cap",
    }

    @property
    @abstractmethod
    def name(self) -> str:
        """Nazwa dostawcy (do logów i diagnostyki)."""
        ...

    @property
    @abstractmethod
    def provides_fundamentals(self) -> bool:
        """True jeśli dostawca dostarcza dane fundamentalne."""
        ...

    @property
    @abstractmethod
    def provides_prices(self) -> bool:
        """True jeśli dostawca dostarcza historię cen."""
        ...

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> dict[str, float | None]:
        """
        Pobierz dane fundamentalne dla tickera.

        Returns:
            Słownik {metric_name: value}. Brakujące metryki → None.
            Metryki zawsze w jednostkach standardowych (% dla stóp, USD dla cen).
        """
        ...

    @abstractmethod
    def get_price_history(self, ticker: str, days: int = 400) -> pd.DataFrame:
        """
        Pobierz historię cen OHLCV.

        Returns:
            DataFrame z indeksem DatetimeIndex i kolumnami: Open, High, Low, Close, Volume.
            Pusty DataFrame przy braku danych.
        """
        ...

    def normalize_ticker(self, ticker: str) -> str:
        """Konwertuj ticker z formatu Yahoo Finance na format tego dostawcy."""
        return ticker

    @staticmethod
    def _safe_float(val, divisor: float = 1.0) -> float | None:
        """Bezpieczna konwersja wartości na float. None przy braku/inf/nan."""
        import math
        if val is None:
            return None
        try:
            f = float(val) / divisor
            return f if math.isfinite(f) else None
        except (TypeError, ValueError):
            return None

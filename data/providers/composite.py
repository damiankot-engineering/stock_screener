"""
data/providers/composite.py
Composite DataProvider — orkiestruje wiele dostawców w łańcuchu fallback.

LOGIKA:
  1. Fundamenty:   FMP → yFinance (fallback)
  2. Ceny/tech:    Stooq → yFinance (fallback)

  Jeśli primary source zwróci None dla danej metryki,
  composite uzupełnia ją z następnego źródła w kolejności.
  Nigdy nie blokuje — każdy błąd kończy się None, nie wyjątkiem.

ROZSZERZALNOŚĆ:
  Dodanie płatnego źródła:
    from data.providers.bloomberg import BloombergProvider
    composite = CompositeProvider(
        fundamental_providers=[BloombergProvider(...), FMPProvider(...)],
        price_providers=[BloombergProvider(...), StooqProvider()],
    )
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from .base import DataProvider, ProviderResult

logger = logging.getLogger(__name__)


class CompositeProvider:
    """
    Łączy wiele DataProvider w łańcuch priorytetowy.
    Każda metryka pochodzi z najwyższego dostępnego źródła.
    """

    def __init__(
        self,
        fundamental_providers: list[DataProvider],
        price_providers: list[DataProvider],
    ):
        self.fundamental_providers = fundamental_providers
        self.price_providers       = price_providers

    @property
    def name(self) -> str:
        f_names = "+".join(p.name for p in self.fundamental_providers)
        p_names = "+".join(p.name for p in self.price_providers)
        return f"Composite(fund={f_names}, price={p_names})"

    # ── Główne metody ─────────────────────────────────────────

    def get_fundamentals(self, ticker: str) -> dict[str, float | None]:
        """
        Pobierz fundamenty z najwyższego dostępnego źródła.
        Brakujące metryki uzupełniane z kolejnych źródeł.
        """
        merged: dict[str, float | None] = {}

        for provider in self.fundamental_providers:
            if not provider.provides_fundamentals:
                continue
            try:
                data = provider.get_fundamentals(ticker)
                if not data:
                    continue
                # Uzupełnij tylko te pola, których jeszcze nie mamy
                new_fields = {k: v for k, v in data.items()
                              if k not in merged and v is not None}
                merged.update(new_fields)
                filled = sum(1 for v in merged.values() if v is not None)
                logger.debug(f"{ticker} [{provider.name}]: {len(new_fields)} nowych pól "
                             f"(łącznie: {filled})")
                # Jeśli mamy wszystkie kluczowe metryki — nie odpytuj kolejnych
                if self._has_core_fundamentals(merged):
                    break
            except Exception as exc:
                logger.debug(f"{ticker} [{provider.name}] błąd: {exc}")

        return merged

    def get_price_history(self, ticker: str, days: int = 400) -> pd.DataFrame:
        """Pobierz historię cen z najwyższego dostępnego źródła."""
        for provider in self.price_providers:
            if not provider.provides_prices:
                continue
            try:
                df = provider.get_price_history(ticker, days)
                if not df.empty and len(df) >= 20:
                    logger.debug(f"{ticker} [{provider.name}]: {len(df)} sesji")
                    return df
            except Exception as exc:
                logger.debug(f"{ticker} [{provider.name}] ceny błąd: {exc}")

        return pd.DataFrame()

    def get_all(self, ticker: str, days: int = 400) -> ProviderResult:
        """Pobierz fundamenty + ceny w jednym wywołaniu."""
        fundamentals  = self.get_fundamentals(ticker)
        price_history = self.get_price_history(ticker, days)
        return ProviderResult(
            ticker=ticker,
            fundamentals=fundamentals,
            price_history=price_history,
            source=self.name,
        )

    @staticmethod
    def _has_core_fundamentals(data: dict) -> bool:
        """Sprawdź czy mamy zestaw kluczowych metryk fundamentalnych."""
        core = {"pe_ratio", "roe", "debt_to_equity", "revenue_growth", "profit_margin"}
        return sum(1 for k in core if data.get(k) is not None) >= 3


def build_composite(config: dict) -> CompositeProvider:
    """
    Fabryka: zbuduj CompositeProvider na podstawie konfiguracji.

    Kolejność priorytetów (konfigurowalna przez data_sources w YAML):
      fundamenty: FMP → yFinance
      ceny:       Stooq → yFinance
    """
    import os
    data_cfg      = config.get("data_sources", {})
    fmp_cfg       = data_cfg.get("fmp", {})
    stooq_cfg     = data_cfg.get("stooq", {})
    yfinance_cfg  = data_cfg.get("yfinance", {})
    settings      = config.get("settings", {})
    api_delay     = settings.get("api_delay_seconds", 0.25)

    fundamental_providers: list[DataProvider] = []
    price_providers:       list[DataProvider] = []

    # ── FMP (primary fundamentals) ────────────────────────────
    fmp_enabled = fmp_cfg.get("enabled", True)
    fmp_key_env = fmp_cfg.get("api_key_env", "FMP_API_KEY")
    fmp_key     = os.getenv(fmp_key_env, "").strip()

    if fmp_enabled and fmp_key:
        try:
            from .fmp import FMPProvider
            fmp = FMPProvider(api_key=fmp_key, api_delay=api_delay)
            fundamental_providers.append(fmp)
            price_providers.append(fmp)   # FMP może też dostarczyć ceny
            logger.info("Dostawca danych: FMP (primary fundamentals)")
        except Exception as exc:
            logger.warning(f"FMP niedostępny: {exc}")
    elif fmp_enabled and not fmp_key:
        logger.info("FMP_API_KEY nie ustawiony — pomijam FMP. "
                    "Zarejestruj się na https://financialmodelingprep.com/register")

    # ── Stooq (primary prices) ────────────────────────────────
    stooq_enabled = stooq_cfg.get("enabled", True)
    if stooq_enabled:
        try:
            from .stooq import StooqProvider
            stooq = StooqProvider(api_delay=stooq_cfg.get("api_delay", api_delay))
            price_providers.insert(0, stooq)  # Stooq przed FMP dla cen
            logger.info("Dostawca danych: Stooq (primary prices)")
        except Exception as exc:
            logger.warning(f"Stooq niedostępny: {exc}")

    # ── yFinance (fallback — zawsze dodawany) ─────────────────
    yf_enabled = yfinance_cfg.get("enabled", True)
    if yf_enabled:
        try:
            from .yfinance_provider import YFinanceProvider
            yf = YFinanceProvider()
            fundamental_providers.append(yf)   # fallback dla fundamentów
            price_providers.append(yf)          # fallback dla cen
            logger.info("Dostawca danych: yFinance (fallback)")
        except ImportError:
            logger.warning("yfinance nie zainstalowany")

    if not fundamental_providers:
        logger.warning(
            "Brak dostawców danych fundamentalnych! "
            "Ustaw FMP_API_KEY lub zainstaluj yfinance."
        )
    if not price_providers:
        logger.warning("Brak dostawców danych cenowych!")

    return CompositeProvider(
        fundamental_providers=fundamental_providers,
        price_providers=price_providers,
    )

"""
data/providers/fmp.py
Financial Modeling Prep (FMP) — dostawca danych fundamentalnych.

DLACZEGO FMP zamiast yfinance:
  • Dane bezpośrednio ze sprawozdań finansowych (SEC, giełdy)
  • Spójny format dla wszystkich rynków (nie tylko US)
  • Dedykowany endpoint dla wskaźników — nie trzeba parsować raw info dict
  • Pełne metryki: EV/EBITDA, ROIC, FCF margin, Altman Z-Score
  • Lepsze pokrycie rynków EM

DARMOWY PLAN:
  250 zapytań/dzień, klucz bez karty kredytowej
  Rejestracja: https://financialmodelingprep.com/register
  Ustaw: export FMP_API_KEY=twój_klucz

ENDPOINTY (darmowe):
  /v3/profile/{symbol}          — sektor, MC, opis
  /v3/ratios/{symbol}?limit=1   — P/E, P/B, ROE, D/E, marże
  /v3/financial-growth/{symbol} — wzrost przychodów, EPS
  /v3/key-metrics/{symbol}      — EV/EBITDA, ROIC, FCF/akcję
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import pandas as pd
import requests

from .base import DataProvider

logger = logging.getLogger(__name__)


class FMPProvider(DataProvider):
    """
    Financial Modeling Prep — fundamenty ze sprawozdań finansowych.
    Jeden ticker wymaga 2–3 requestów (ratios + growth + key-metrics).
    Przy 250 req/dzień: ~80 tickerów pełnych danych.
    """

    BASE_URL = "https://financialmodelingprep.com/api"

    def __init__(self, api_key: str | None = None, timeout: int = 15,
                 api_delay: float = 0.25):
        self._key     = (api_key or os.getenv("FMP_API_KEY", "")).strip()
        self.timeout  = timeout
        self.api_delay = api_delay

        if not self._key:
            raise ValueError(
                "Brak FMP_API_KEY.\n"
                "1. Zarejestruj się na https://financialmodelingprep.com/register\n"
                "2. Skopiuj klucz API (darmowy plan: 250 req/dzień)\n"
                "3. export FMP_API_KEY=twój_klucz"
            )

    @property
    def name(self) -> str:
        return "FMP"

    @property
    def provides_fundamentals(self) -> bool:
        return True

    @property
    def provides_prices(self) -> bool:
        return True  # FMP ma też historię cen, ale Stooq jest szybszy

    def normalize_ticker(self, ticker: str) -> str:
        """
        Konwertuj ticker Yahoo Finance → FMP format.
        FMP przyjmuje większość tickerów Yahoo bez zmian, ale:
          BRK-B → BRK-B (OK)
          ASML.AS → ASML  (FMP używa samego symbolu dla europejskich)
          7203.T  → 7203.T (OK dla japońskich)
        """
        # Dla rynków europejskich FMP często przyjmuje sam symbol
        yahoo_to_fmp = {
            ".AS": "",    # Amsterdam → bez sufiksu (ASML.AS → ASML)
            ".PA": "",    # Paris → bez sufiksu
            ".DE": "",    # Frankfurt → bez sufiksu
            ".L":  "",    # London → bez sufiksu
            ".MI": "",    # Milan → bez sufiksu
            ".SW": "",    # Zurich → bez sufiksu
            ".CO": "",    # Copenhagen → bez sufiksu
            # Azja zachowuje sufiks
            ".T":  ".T",
            ".HK": ".HK",
            ".KS": ".KS",
            ".TW": ".TW",
            ".NS": ".NS",
            ".SA": ".SA",
            ".AX": ".AX",
        }
        for suffix, replacement in yahoo_to_fmp.items():
            if ticker.endswith(suffix):
                return ticker[:-len(suffix)] + replacement
        return ticker

    # ── Główne metody ─────────────────────────────────────────

    def get_fundamentals(self, ticker: str) -> dict[str, float | None]:
        """Pobierz pełne dane fundamentalne z 3 endpointów FMP."""
        fmp_ticker = self.normalize_ticker(ticker)
        result: dict[str, float | None] = {}

        # 1. Financial Ratios — P/E, P/B, ROE, marże, zadłużenie
        ratios = self._get_ratios(fmp_ticker)
        result.update(ratios)
        time.sleep(self.api_delay)

        # 2. Financial Growth — wzrost przychodów, EPS, EBITDA
        growth = self._get_growth(fmp_ticker)
        result.update(growth)
        time.sleep(self.api_delay)

        # 3. Key Metrics — EV/EBITDA, ROIC, FCF metrics
        key_metrics = self._get_key_metrics(fmp_ticker)
        result.update(key_metrics)

        return result

    def get_price_history(self, ticker: str, days: int = 400) -> pd.DataFrame:
        """Pobierz historię cen OHLCV z FMP."""
        fmp_ticker = self.normalize_ticker(ticker)
        from datetime import datetime, timedelta
        date_from = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        try:
            data = self._get(
                f"/v3/historical-price-full/{fmp_ticker}",
                {"from": date_from, "serietype": "line"},
            )
            historical = data.get("historical", []) if isinstance(data, dict) else []
            if not historical:
                return pd.DataFrame()

            df = pd.DataFrame(historical)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()

            # Normalizuj nazwy kolumn
            col_map = {"open": "Open", "high": "High", "low": "Low",
                       "close": "Close", "volume": "Volume"}
            df = df.rename(columns=col_map)

            required = ["Close"]
            if all(c in df.columns for c in required):
                return df[["Open", "High", "Low", "Close", "Volume"]
                           if "Volume" in df.columns
                           else ["Open", "High", "Low", "Close"]]
            return pd.DataFrame()

        except Exception as exc:
            logger.debug(f"FMP price history {ticker}: {exc}")
            return pd.DataFrame()

    # ── Parsowanie endpointów ─────────────────────────────────

    def _get_ratios(self, ticker: str) -> dict[str, float | None]:
        """FMP /v3/ratios — P/E, P/B, P/S, ROE, ROA, marże, zadłużenie."""
        try:
            data = self._get(f"/v3/ratios/{ticker}", {"limit": 1})
            if not data or not isinstance(data, list):
                return {}
            r = data[0]
            sf = self._safe_float
            return {
                "pe_ratio":       sf(r.get("priceEarningsRatio")),
                "pb_ratio":       sf(r.get("priceToBookRatio")),
                "ps_ratio":       sf(r.get("priceToSalesRatio")),
                "roe":            sf(r.get("returnOnEquity"),       1/100),
                "roa":            sf(r.get("returnOnAssets"),       1/100),
                "debt_to_equity": sf(r.get("debtEquityRatio")),
                "current_ratio":  sf(r.get("currentRatio")),
                "quick_ratio":    sf(r.get("quickRatio")),
                "profit_margin":  sf(r.get("netProfitMargin"),      1/100),
                "gross_margin":   sf(r.get("grossProfitMargin"),    1/100),
                "operating_margin": sf(r.get("operatingProfitMargin"), 1/100),
                "dividend_yield": sf(r.get("dividendYield"),        1/100),
                "payout_ratio":   sf(r.get("payoutRatio"),          1/100),
            }
        except Exception as exc:
            logger.debug(f"FMP ratios {ticker}: {exc}")
            return {}

    def _get_growth(self, ticker: str) -> dict[str, float | None]:
        """FMP /v3/financial-growth — wzrost przychodów, EPS, EBITDA."""
        try:
            data = self._get(f"/v3/financial-growth/{ticker}", {"limit": 1})
            if not data or not isinstance(data, list):
                return {}
            g = data[0]
            sf = self._safe_float
            return {
                "revenue_growth":   sf(g.get("revenueGrowth"),   1/100),
                "earnings_growth":  sf(g.get("netIncomeGrowth"),  1/100),
                "ebitda_growth":    sf(g.get("ebitdaGrowth"),     1/100),
                "fcf_growth":       sf(g.get("freeCashFlowGrowth"), 1/100),
                "eps_growth":       sf(g.get("epsgrowth"),        1/100),
            }
        except Exception as exc:
            logger.debug(f"FMP growth {ticker}: {exc}")
            return {}

    def _get_key_metrics(self, ticker: str) -> dict[str, float | None]:
        """FMP /v3/key-metrics — EV/EBITDA, ROIC, FCF yield, market cap."""
        try:
            data = self._get(f"/v3/key-metrics/{ticker}", {"limit": 1})
            if not data or not isinstance(data, list):
                return {}
            m = data[0]
            sf = self._safe_float
            return {
                "ev_ebitda":         sf(m.get("enterpriseValueOverEBITDA")),
                "roic":              sf(m.get("roic"),                1/100),
                "fcf_margin":        sf(m.get("freeCashFlowYield"),   1/100),
                "net_debt_to_ebitda": sf(m.get("netDebtToEBITDA")),
                "market_cap":        sf(m.get("marketCap")),
                "revenue_per_share": sf(m.get("revenuePerShare")),
            }
        except Exception as exc:
            logger.debug(f"FMP key-metrics {ticker}: {exc}")
            return {}

    # ── HTTP helper ───────────────────────────────────────────

    def _get(self, endpoint: str, params: dict | None = None) -> Any:
        """Wykonaj GET request do FMP API."""
        url    = f"{self.BASE_URL}{endpoint}"
        params = {**(params or {}), "apikey": self._key}
        resp   = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        # FMP zwraca {"Error Message": "..."} przy błędach
        if isinstance(data, dict) and "Error Message" in data:
            raise ValueError(f"FMP error: {data['Error Message']}")

        return data

"""
data/providers/stooq.py
Stooq — darmowe dane historyczne OHLCV, bez klucza API.

DLACZEGO STOOQ:
  • Całkowicie darmowy, bez rejestracji, bez limitu requestów
  • Globalne pokrycie: US, Europa, Azja, Polska, EM
  • Czyste CSV — szybsze i stabilniejsze niż yfinance
  • Dane dzienne sięgają wstecz 20+ lat
  • Używany przez QSTK, Zipline i inne systemy backtestingowe

FORMAT TICKERÓW (Stooq używa własnego formatu):
  Yahoo .AS  → .nl  (ASML.AS → asml.nl)
  Yahoo .PA  → .fr
  Yahoo .DE  → .de
  Yahoo .L   → .uk
  Yahoo .T   → .jp
  Yahoo .HK  → .hk
  Yahoo .NS  → .ns  (India)
  Yahoo .SA  → .br  (Brazil)
  US (bez sufiksu) → .us
  Numery (Japan) → 7203.jp

UWAGA: Stooq nie dostarcza danych fundamentalnych — tylko ceny.
Używany wyłącznie do obliczania wskaźników technicznych.
"""
from __future__ import annotations

import io
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from .base import DataProvider

logger = logging.getLogger(__name__)

# Mapowanie sufiksów Yahoo Finance → Stooq
YAHOO_TO_STOOQ: dict[str, str] = {
    ".AS": ".nl",   # Amsterdam
    ".PA": ".fr",   # Paris
    ".DE": ".de",   # Frankfurt
    ".L":  ".uk",   # London
    ".MI": ".it",   # Milan
    ".SW": ".ch",   # Zurich
    ".CO": ".dk",   # Copenhagen
    ".ST": ".se",   # Stockholm
    ".OL": ".no",   # Oslo
    ".BR": ".be",   # Brussels
    ".MC": ".es",   # Madrid
    ".WA": ".pl",   # Warsaw
    ".PR": ".cz",   # Prague
    ".T":  ".jp",   # Tokyo
    ".HK": ".hk",   # Hong Kong
    ".KS": ".kr",   # Seoul
    ".SS": ".cn",   # Shanghai
    ".SZ": ".cn",   # Shenzhen
    ".TW": ".tw",   # Taiwan
    ".NS": ".ns",   # India NSE
    ".BO": ".bo",   # India BSE
    ".AX": ".au",   # Australia
    ".SA": ".br",   # Brazil
    ".MX": ".mx",   # Mexico
    ".TA": ".il",   # Israel
}


class StooqProvider(DataProvider):
    """
    Stooq — historyczne dane cenowe w formacie CSV.
    Używany do obliczania wskaźników technicznych (momentum, RSI, MA, volatility).
    """

    CSV_URL = "https://stooq.com/q/d/l/"

    def __init__(self, timeout: int = 20, api_delay: float = 0.2):
        self.timeout   = timeout
        self.api_delay = api_delay

    @property
    def name(self) -> str:
        return "Stooq"

    @property
    def provides_fundamentals(self) -> bool:
        return False  # Stooq tylko ceny

    @property
    def provides_prices(self) -> bool:
        return True

    def normalize_ticker(self, ticker: str) -> str:
        """Konwertuj ticker Yahoo Finance na format Stooq (lowercase)."""
        # Sprawdź znane sufiksy
        for yahoo_suf, stooq_suf in YAHOO_TO_STOOQ.items():
            if ticker.upper().endswith(yahoo_suf):
                base = ticker[:-len(yahoo_suf)]
                return f"{base.lower()}{stooq_suf}"

        # US ticker (bez sufiksu): AAPL → aapl.us
        # Wyjątek: BRK-B → brk-b.us
        return f"{ticker.lower()}.us"

    def get_fundamentals(self, ticker: str) -> dict[str, float | None]:
        """Stooq nie dostarcza danych fundamentalnych."""
        return {}

    def get_price_history(self, ticker: str, days: int = 400) -> pd.DataFrame:
        """
        Pobierz historię cen z Stooq jako CSV.

        Stooq format: Date,Open,High,Low,Close,Volume
        """
        stooq_ticker = self.normalize_ticker(ticker)
        date_from = (datetime.utcnow() - timedelta(days=days)).strftime("%Y%m%d")
        date_to   = datetime.utcnow().strftime("%Y%m%d")

        try:
            resp = requests.get(
                self.CSV_URL,
                params={
                    "s": stooq_ticker,
                    "d1": date_from,
                    "d2": date_to,
                    "i": "d",        # daily
                },
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()

            if not resp.text or "No data" in resp.text:
                logger.debug(f"Stooq: brak danych dla {stooq_ticker}")
                return pd.DataFrame()

            df = pd.read_csv(io.StringIO(resp.text))
            if df.empty or "Date" not in df.columns or "Close" not in df.columns:
                return pd.DataFrame()

            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date").sort_index()

            # Upewnij się że mamy właściwe kolumny
            col_map = {"Open": "Open", "High": "High", "Low": "Low",
                       "Close": "Close", "Volume": "Volume"}
            df = df.rename(columns=col_map)

            logger.debug(f"Stooq {ticker} ({stooq_ticker}): {len(df)} sesji")
            return df

        except Exception as exc:
            logger.debug(f"Stooq {ticker}: {exc}")
            return pd.DataFrame()

    def test_connection(self) -> bool:
        """Sprawdź czy Stooq jest dostępny (używamy AAPL jako test)."""
        try:
            df = self.get_price_history("AAPL", days=5)
            return not df.empty
        except Exception:
            return False

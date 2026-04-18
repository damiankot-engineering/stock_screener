"""
data/ticker_source.py
Pobieranie list tickerów z różnych indeksów giełdowych.
Wszystkie źródła są darmowe (Wikipedia, stooq, dane statyczne).
"""
from __future__ import annotations

import logging
from typing import Callable

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Publiczne API modułu
# ─────────────────────────────────────────────────────────────

def get_tickers(source_config: dict) -> list[str]:
    """
    Główna funkcja pobierająca listę tickerów na podstawie konfiguracji.

    Args:
        source_config: słownik z kluczami 'index' i opcjonalnie 'custom_tickers'

    Returns:
        Lista symboli tickerów (uppercase, bez duplikatów)
    """
    index_name = source_config.get("index", "sp500").lower()
    fetchers: dict[str, Callable[[], list[str]]] = {
        "sp500":     _fetch_sp500,
        "nasdaq100": _fetch_nasdaq100,
        "wig20":     _fetch_wig20,
        "dax40":     _fetch_dax40,
        "custom":    lambda: source_config.get("custom_tickers", []),
    }

    fetcher = fetchers.get(index_name)
    if fetcher is None:
        raise ValueError(f"Nieznane źródło: '{index_name}'")

    try:
        tickers = fetcher()
        tickers = _clean_tickers(tickers)
        logger.info(f"Pobrano {len(tickers)} tickerów ze źródła: {index_name.upper()}")
        return tickers
    except Exception as exc:
        logger.error(f"Błąd pobierania tickerów ze źródła '{index_name}': {exc}")
        raise


# ─────────────────────────────────────────────────────────────
# Implementacje dla poszczególnych indeksów
# ─────────────────────────────────────────────────────────────

def _fetch_sp500() -> list[str]:
    """S&P 500 z Wikipedii – stabilne i darmowe źródło."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        tables = pd.read_html(url)
        df = tables[0]
        tickers = df["Symbol"].tolist()
        logger.debug(f"S&P 500: pobrano {len(tickers)} tickerów z Wikipedii")
        return tickers
    except Exception as exc:
        logger.warning(f"Błąd pobierania S&P 500 z Wikipedii: {exc}. Używam listy awaryjnej.")
        return _sp500_fallback()


def _fetch_nasdaq100() -> list[str]:
    """NASDAQ 100 z Wikipedii."""
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    try:
        tables = pd.read_html(url)
        # Szukamy tabeli z kolumną 'Ticker' lub 'Symbol'
        for table in tables:
            cols = [c.lower() for c in table.columns]
            if "ticker" in cols:
                col = table.columns[cols.index("ticker")]
                return table[col].dropna().tolist()
            if "symbol" in cols:
                col = table.columns[cols.index("symbol")]
                return table[col].dropna().tolist()
        raise ValueError("Nie znaleziono tabeli z tickerami NASDAQ 100")
    except Exception as exc:
        logger.warning(f"Błąd pobierania NASDAQ 100: {exc}. Używam listy awaryjnej.")
        return _nasdaq100_fallback()


def _fetch_wig20() -> list[str]:
    """
    WIG20 (Giełda Papierów Wartościowych w Warszawie).
    Sufiksy .WA są wymagane przez Yahoo Finance dla akcji GPW.
    """
    # WIG20 – statyczna lista (zmienia się rzadko, można też pobrać z GPW API)
    wig20_tickers = [
        "ALE.WA", "ALR.WA", "CCC.WA", "CDR.WA", "CEZ.WA",
        "DNP.WA", "JSW.WA", "KGH.WA", "KRU.WA", "LPP.WA",
        "MBK.WA", "OPL.WA", "PCO.WA", "PEO.WA", "PKN.WA",
        "PKO.WA", "PZU.WA", "SPL.WA", "TPE.WA", "VRG.WA",
    ]
    logger.debug(f"WIG20: {len(wig20_tickers)} tickerów (lista statyczna)")
    return wig20_tickers


def _fetch_dax40() -> list[str]:
    """DAX 40 z Wikipedii."""
    url = "https://en.wikipedia.org/wiki/DAX"
    try:
        tables = pd.read_html(url)
        for table in tables:
            cols = [str(c).lower() for c in table.columns]
            if "ticker" in cols or "symbol" in cols:
                key = "ticker" if "ticker" in cols else "symbol"
                col = table.columns[cols.index(key)]
                tickers = table[col].dropna().tolist()
                # DAX na Yahoo Finance ma sufiks .DE
                tickers = [t if t.endswith(".DE") else f"{t}.DE" for t in tickers]
                return tickers
    except Exception as exc:
        logger.warning(f"Błąd pobierania DAX 40: {exc}. Używam listy awaryjnej.")

    return _dax40_fallback()


# ─────────────────────────────────────────────────────────────
# Listy awaryjne (fallback) – używane gdy Wikipedia niedostępna
# ─────────────────────────────────────────────────────────────

def _sp500_fallback() -> list[str]:
    """Top 50 spółek S&P 500 jako fallback."""
    return [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "BRK-B",
        "JPM", "LLY", "AVGO", "XOM", "UNH", "V", "TSLA", "MA", "PG",
        "JNJ", "HD", "MRK", "COST", "ABBV", "CRM", "BAC", "CVX",
        "KO", "PEP", "WMT", "TMO", "CSCO", "ACN", "MCD", "ABT",
        "DHR", "LIN", "PM", "ADBE", "NFLX", "WFC", "TXN", "DIS",
        "AMGN", "BMY", "UNP", "INTC", "INTU", "QCOM", "IBM", "CAT",
    ]


def _nasdaq100_fallback() -> list[str]:
    return [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
        "AVGO", "COST", "NFLX", "ADBE", "AMD", "CSCO", "INTC", "INTU",
        "CMCSA", "TMUS", "QCOM", "HON", "AMGN", "TXN", "AMAT", "BKNG",
        "SBUX", "GILD", "ADI", "MDLZ", "REGN", "VRTX", "ISRG", "LRCX",
        "PDD", "PANW", "KLAC", "MELI", "ASML", "MU", "SNPS", "CDNS",
    ]


def _dax40_fallback() -> list[str]:
    return [
        "ADS.DE", "AIR.DE", "ALV.DE", "BAS.DE", "BAYN.DE", "BEI.DE",
        "BMW.DE", "CON.DE", "1COV.DE", "DHER.DE", "DB1.DE", "DBK.DE",
        "DHL.DE", "DTE.DE", "EOAN.DE", "FRE.DE", "HEI.DE", "HEN3.DE",
        "IFX.DE", "LIN.DE", "MBG.DE", "MRK.DE", "MTX.DE", "MUV2.DE",
        "PAH3.DE", "PUM.DE", "RWE.DE", "SAP.DE", "SHL.DE", "SIE.DE",
        "SY1.DE", "TKA.DE", "VNA.DE", "VOW3.DE", "ZAL.DE",
    ]


# ─────────────────────────────────────────────────────────────
# Pomocnicze
# ─────────────────────────────────────────────────────────────

def _clean_tickers(tickers: list) -> list[str]:
    """Usuń duplikaty, NaN, puste stringi; normalizuj do uppercase."""
    cleaned = []
    seen = set()
    for t in tickers:
        if not t or not isinstance(t, str):
            continue
        t = str(t).strip().upper()
        # Yahoo Finance używa '-' zamiast '.' dla BRK.B itp. w USA
        t = t.replace(".", "-") if not any(t.endswith(suf) for suf in [".WA", ".DE", ".PA", ".MI"]) else t
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    return cleaned

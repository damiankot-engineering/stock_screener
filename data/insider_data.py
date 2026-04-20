"""
data/insider_data.py
Pobieranie danych o transakcjach insiderów z SEC EDGAR (bezpłatne, bez klucza API).

SEC EDGAR pełny dostęp: https://www.sec.gov/cgi-bin/browse-edgar
Używamy:
  1. company_tickers.json — mapa ticker → CIK (jednorazowe pobieranie, cache)
  2. /cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4 — lista Form 4
  3. Parsowanie XML-owych plików Form 4 po linku z wyników wyszukiwania

Ograniczenia:
  • Tylko spółki notowane w USA (S&P500, NASDAQ, NYSE)
  • SEC wymaga User-Agent z adresem email (Rate limit: 10 req/s)
  • Dane dostępne z opóźnieniem ~2 dni

INSIDER SIGNAL:
  Liczba transakcji kupna vs sprzedaży insiderów w ostatnich 90 dniach.
  buy_ratio = buys / (buys + sells)  → im wyższy, tym silniejszy sygnał.
  Wykluczone: automatyczne transakcje (kod 'A'), opcje (kod 'M').
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

# SEC wymaga identyfikacji w User-Agent
SEC_USER_AGENT = "StockScreener research@stockscreener.local"
SEC_HEADERS    = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# Cache mapa ticker→CIK (odświeżana rzadko)
_ticker_cik_cache: dict[str, str] = {}
_cik_cache_loaded_at: datetime | None = None


@dataclass
class InsiderSignal:
    """Wynik analizy transakcji insiderów dla jednej spółki."""
    ticker:       str
    buys:         int   = 0       # liczba transakcji kupna (90 dni)
    sells:        int   = 0       # liczba transakcji sprzedaży (90 dni)
    net_shares:   int   = 0       # netto akcji (kupno - sprzedaż)
    buy_ratio:    float | None = None   # buys / (buys + sells), None = brak danych
    data_available: bool = False
    error:        str   = ""

    @property
    def signal_score(self) -> float | None:
        """
        Wynik sygnału insider: 0.0 – 1.0
        0.5 = neutralny (równe kupno/sprzedaż)
        >0.7 = silne kupno insiderów
        <0.3 = silna sprzedaż insiderów
        """
        return self.buy_ratio


class InsiderDataFetcher:
    """
    Pobiera dane o transakcjach insiderów z SEC EDGAR Form 4.
    Działa tylko dla spółek USA (wymaga CIK z SEC).
    """

    SEC_BASE = "https://data.sec.gov"
    EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"

    def __init__(self, lookback_days: int = 90, timeout: int = 15,
                 api_delay: float = 0.15):
        self.lookback_days = lookback_days
        self.timeout       = timeout
        self.api_delay     = api_delay      # SEC rate limit: max ~10 req/s

    # ── Główna metoda ─────────────────────────────────────────

    def fetch_batch(self, tickers: list[str]) -> dict[str, InsiderSignal]:
        """
        Pobierz sygnały insiderów dla listy tickerów.
        Automatycznie pomija spółki spoza USA (brak CIK).
        """
        # Załaduj mapę ticker → CIK
        cik_map = self._load_ticker_cik_map()

        results: dict[str, InsiderSignal] = {}
        us_tickers = [t for t in tickers if "." not in t or t.endswith("-A") or t.endswith("-B")]

        logger.info(f"Insider data: {len(us_tickers)}/{len(tickers)} spółek USA")

        for ticker in us_tickers:
            cik = cik_map.get(ticker.upper().replace("-", "."))
            if not cik:
                # Spróbuj różnych wariantów (BRK-B → BRK.B → BRK)
                for variant in [ticker.replace("-", "."), ticker.split("-")[0]]:
                    cik = cik_map.get(variant.upper())
                    if cik:
                        break

            if not cik:
                results[ticker] = InsiderSignal(ticker=ticker,
                    error="CIK not found (non-US or delisted)")
                continue

            try:
                signal = self._fetch_form4(ticker, cik)
                results[ticker] = signal
                time.sleep(self.api_delay)
            except Exception as exc:
                logger.debug(f"Insider {ticker}: {exc}")
                results[ticker] = InsiderSignal(ticker=ticker, error=str(exc)[:80])

        success = sum(1 for s in results.values() if s.data_available)
        logger.info(f"Insider data: {success}/{len(us_tickers)} spółek z danymi")
        return results

    # ── SEC Form 4 ────────────────────────────────────────────

    def _fetch_form4(self, ticker: str, cik: str) -> InsiderSignal:
        """Pobierz i przelicz transakcje insiderów (Form 4) z ostatnich N dni."""
        cutoff = datetime.utcnow() - timedelta(days=self.lookback_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        # EDGAR full-text search API
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q":         f'"{ticker}"',
                "forms":     "4",
                "dateRange": "custom",
                "startdt":   cutoff_str,
                "enddt":     datetime.utcnow().strftime("%Y-%m-%d"),
                "entity":    cik.lstrip("0"),
            },
            headers=SEC_HEADERS,
            timeout=self.timeout,
        )

        # Fallback: bezpośredni endpoint submissions
        if resp.status_code != 200:
            return self._fetch_via_submissions(ticker, cik, cutoff)

        data  = resp.json()
        hits  = data.get("hits", {}).get("hits", [])

        return self._parse_search_hits(ticker, hits, cutoff)

    def _fetch_via_submissions(self, ticker: str, cik: str,
                                cutoff: datetime) -> InsiderSignal:
        """Fallback: pobierz Form 4 przez submissions endpoint."""
        padded_cik = cik.zfill(10)
        resp = requests.get(
            f"{self.SEC_BASE}/submissions/CIK{padded_cik}.json",
            headers=SEC_HEADERS,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data   = resp.json()
        recent = data.get("filings", {}).get("recent", {})

        forms      = recent.get("form", [])
        dates      = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        buys = sells = net_shares = 0
        found_any = False

        for form, date_str, acc in zip(forms, dates, accessions):
            if form != "4":
                continue
            try:
                filing_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if filing_date < cutoff:
                break

            # Pobierz szczegóły Form 4 (XML)
            try:
                b, s, net = self._parse_form4_xml(padded_cik, acc)
                buys      += b
                sells     += s
                net_shares += net
                found_any  = True
                time.sleep(self.api_delay)
            except Exception:
                pass

        if not found_any:
            return InsiderSignal(ticker=ticker, error="no Form 4 filings in period")

        total     = buys + sells
        buy_ratio = round(buys / total, 4) if total > 0 else 0.5

        return InsiderSignal(
            ticker=ticker, buys=buys, sells=sells,
            net_shares=net_shares, buy_ratio=buy_ratio,
            data_available=True,
        )

    def _parse_search_hits(self, ticker: str, hits: list,
                            cutoff: datetime) -> InsiderSignal:
        """Przelicz wyniki wyszukiwania EDGAR na InsiderSignal."""
        buys = sells = net = 0
        for hit in hits:
            src = hit.get("_source", {})
            # Typ transakcji: P = purchase, S = sale
            ttype = str(src.get("transaction_type", "")).upper()
            shares = abs(int(src.get("shares", 0) or 0))
            if ttype == "P":
                buys += 1; net += shares
            elif ttype == "S":
                sells += 1; net -= shares

        total     = buys + sells
        buy_ratio = round(buys / total, 4) if total > 0 else None
        return InsiderSignal(
            ticker=ticker, buys=buys, sells=sells,
            net_shares=net, buy_ratio=buy_ratio,
            data_available=total > 0,
        )

    def _parse_form4_xml(self, cik: str, accession: str) -> tuple[int, int, int]:
        """Parsuj Form 4 XML, zwróć (buys, sells, net_shares)."""
        acc_clean = accession.replace("-", "")
        url = (f"{self.SEC_BASE}/Archives/edgar/data/"
               f"{int(cik)}/{acc_clean}/{accession}.txt")
        resp = requests.get(url, headers=SEC_HEADERS, timeout=self.timeout)
        if resp.status_code != 200:
            return 0, 0, 0

        content = resp.text
        buys = sells = net = 0

        # Prosta ekstrakcja z XML/tekstu (unikamy parsera XML dla szybkości)
        import re
        txn_blocks = re.findall(
            r'<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>',
            content, re.DOTALL
        )
        for block in txn_blocks:
            code_match   = re.search(r'<transactionCode>(\w)</transactionCode>', block)
            shares_match = re.search(r'<transactionShares>.*?<value>([\d.]+)</value>', block, re.DOTALL)
            if not code_match:
                continue
            code   = code_match.group(1).upper()
            shares = int(float(shares_match.group(1))) if shares_match else 0
            if code == "P":          # Purchase
                buys += 1; net += shares
            elif code in ("S", "F"): # Sale / Tax withholding
                sells += 1; net -= shares

        return buys, sells, net

    # ── CIK map ───────────────────────────────────────────────

    def _load_ticker_cik_map(self) -> dict[str, str]:
        """
        Wczytaj mapę ticker → CIK z SEC.
        Cache w pamięci (odświeżany raz na sesję).
        """
        global _ticker_cik_cache, _cik_cache_loaded_at

        if (_cik_cache_loaded_at and
                datetime.utcnow() - _cik_cache_loaded_at < timedelta(hours=24)
                and _ticker_cik_cache):
            return _ticker_cik_cache

        try:
            resp = requests.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=SEC_HEADERS,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            _ticker_cik_cache = {
                entry["ticker"].upper(): str(entry["cik_str"])
                for entry in data.values()
                if "ticker" in entry and "cik_str" in entry
            }
            _cik_cache_loaded_at = datetime.utcnow()
            logger.info(f"Załadowano {len(_ticker_cik_cache)} tickerów z SEC EDGAR")
        except Exception as exc:
            logger.warning(f"Nie można załadować mapy ticker→CIK: {exc}")
            _ticker_cik_cache = {}

        return _ticker_cik_cache
